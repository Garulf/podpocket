import asyncio
import threading
import time

import pytest
from argon2 import PasswordHasher

from podpocket.accounts import passwords, throttle
from podpocket.accounts.throttle import LoginBusy, LoginGate, LoginLocked
from tests.api import PASSWORD

LOGIN = "/api/user/login_pocket_casts"
STATUS = "/api/subscription/status"


class SlowHasher(PasswordHasher):
    def __init__(self, engine, delay):
        super().__init__(time_cost=1, memory_cost=8, parallelism=1)
        self._engine = engine
        self._delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.verifications = 0
        self.peak_active = 0
        self.peak_connections = 0

    def verify(self, hash, password):
        with self._lock:
            self.active += 1
            self.verifications += 1
            self.peak_active = max(self.peak_active, self.active)
            self.peak_connections = max(self.peak_connections, self._engine.pool.checkedout())
        time.sleep(self._delay)
        with self._lock:
            self.active -= 1
        return super().verify(hash, password)


@pytest.fixture
def slow_hasher(monkeypatch, services):
    def install(delay, *, concurrency, wait=5.0):
        hasher = SlowHasher(services.sessionmaker.kw["bind"].sync_engine, delay)
        monkeypatch.setattr(passwords, "HASHER", hasher)
        monkeypatch.setattr(throttle, "LOGIN_WAIT_SECONDS", wait)
        passwords.configure(concurrency)
        return hasher

    return install


def credentials(login, password="wrong"):
    return {"email": login, "password": password, "scope": "mobile"}


async def timed_login(client, login, password="wrong"):
    started = time.monotonic()
    response = await client.post(LOGIN, json=credentials(login, password))
    return response, time.monotonic() - started


async def test_no_database_connection_is_held_while_a_password_is_hashed(
    client, alice, slow_hasher
):
    hasher = slow_hasher(0.05, concurrency=2)

    response = await client.post(LOGIN, json=credentials("alice"))

    assert response.status_code == 401
    assert hasher.verifications == 1
    assert hasher.peak_connections == 0


async def test_password_hashing_never_exceeds_the_configured_concurrency(
    client, alice, slow_hasher
):
    hasher = slow_hasher(0.1, concurrency=2)

    responses = await asyncio.gather(
        *(client.post(LOGIN, json=credentials(f"nobody{n}")) for n in range(6))
    )

    assert {response.status_code for response in responses} == {401}
    assert hasher.peak_active == 2


async def test_a_login_over_capacity_is_refused_straight_away(client, alice, slow_hasher):
    slow_hasher(0.5, concurrency=1, wait=0.05)

    results = await asyncio.gather(*(timed_login(client, f"nobody{n}") for n in range(3)))

    refused = [(response, took) for response, took in results if response.status_code == 429]
    assert len(refused) == 2
    for response, took in refused:
        assert took < 0.3
        assert response.json()["errorMessageId"] == "login_server_busy"
        assert response.json()["errorMessage"]
    assert sorted(response.status_code for response, _ in results) == [401, 429, 429]


async def test_a_login_flood_does_not_delay_a_signed_in_user(
    client, alice, alice_auth, slow_hasher
):
    slow_hasher(0.5, concurrency=2, wait=0.05)
    flood = [asyncio.create_task(timed_login(client, "alice")) for _ in range(60)]
    await asyncio.sleep(0.1)

    started = time.monotonic()
    response = await client.get(STATUS, headers=alice_auth)
    took = time.monotonic() - started
    await asyncio.gather(*flood)

    assert response.status_code == 200
    assert took < 0.3


async def test_refreshing_a_token_is_never_limited(client, alice, slow_hasher):
    slow_hasher(0.5, concurrency=1, wait=0.05)
    login = (await client.post(LOGIN, json=credentials("alice", PASSWORD))).json()
    flood = [asyncio.create_task(timed_login(client, f"nobody{n}")) for n in range(5)]
    await asyncio.sleep(0.1)

    response = await client.post(
        "/api/user/token",
        json={"grant_type": "refresh_token", "refresh_token": login["refreshToken"]},
    )
    await asyncio.gather(*flood)

    assert response.status_code == 200


async def test_five_failures_lock_a_username_even_for_the_right_password(client, alice, bob):
    for _ in range(5):
        assert (await client.post(LOGIN, json=credentials("alice"))).status_code == 401

    locked = await client.post(LOGIN, json=credentials("alice", PASSWORD))
    other = await client.post(LOGIN, json=credentials("bob", PASSWORD))

    assert locked.status_code == 429
    assert locked.json()["errorMessageId"] == "login_account_locked"
    assert other.status_code == 200


@pytest.fixture
def now():
    return [1000.0]


@pytest.fixture
def gate(now):
    return LoginGate(clock=lambda: now[0])


async def fail(gate, services, login, times):
    for _ in range(times):
        assert await gate.authenticate(services.sessionmaker, login, "wrong") is None


async def test_the_lock_lifts_after_thirty_seconds(gate, services, alice, now):
    await fail(gate, services, "alice", 5)
    now[0] += throttle.LOCKOUT_SECONDS - 1
    with pytest.raises(LoginLocked):
        await gate.authenticate(services.sessionmaker, "alice", PASSWORD)

    now[0] += 1
    user = await gate.authenticate(services.sessionmaker, "alice", PASSWORD)

    assert user is not None and user.uuid == alice.uuid


async def test_a_successful_login_resets_the_count(gate, services, alice):
    await fail(gate, services, "alice", 4)
    assert await gate.authenticate(services.sessionmaker, "alice", PASSWORD) is not None

    await fail(gate, services, "alice", 4)

    assert await gate.authenticate(services.sessionmaker, "alice", PASSWORD) is not None


async def test_old_failures_are_forgotten(gate, services, alice, now):
    await fail(gate, services, "alice", 4)
    now[0] += throttle.FAILURE_MEMORY_SECONDS

    await fail(gate, services, "alice", 1)

    assert await gate.authenticate(services.sessionmaker, "alice", PASSWORD) is not None


async def test_the_lock_is_the_same_for_names_that_do_not_exist(gate, services, alice):
    await fail(gate, services, "ghost", 5)
    await fail(gate, services, "alice", 5)

    for login in ("ghost", "alice"):
        with pytest.raises(LoginLocked):
            await gate.authenticate(services.sessionmaker, login, PASSWORD)


async def test_a_locked_attempt_still_spends_verification_time(gate, services, alice, monkeypatch):
    hasher = SlowHasher(services.sessionmaker.kw["bind"].sync_engine, 0)
    monkeypatch.setattr(passwords, "HASHER", hasher)
    await fail(gate, services, "ghost", 5)
    before = hasher.verifications

    with pytest.raises(LoginLocked):
        await gate.authenticate(services.sessionmaker, "ghost", "wrong")

    assert hasher.verifications == before + 1


async def test_logins_are_matched_after_normalizing_case_and_spaces(gate, services, alice):
    await fail(gate, services, " Alice ", 3)
    await fail(gate, services, "ALICE", 2)

    with pytest.raises(LoginLocked):
        await gate.authenticate(services.sessionmaker, "alice", PASSWORD)


async def test_only_the_most_recent_failures_are_tracked(gate, services, alice, monkeypatch):
    monkeypatch.setattr(throttle, "MAX_TRACKED_LOGINS", 2)
    await fail(gate, services, "alice", 5)
    await fail(gate, services, "one", 1)
    await fail(gate, services, "two", 1)

    assert await gate.authenticate(services.sessionmaker, "alice", PASSWORD) is not None


async def test_waiting_too_long_for_a_slot_raises_busy(gate, services, alice, monkeypatch):
    monkeypatch.setattr(throttle, "LOGIN_WAIT_SECONDS", 0.01)
    passwords.configure(1)

    async with passwords.hashing_slot(1):
        with pytest.raises(LoginBusy):
            await gate.authenticate(services.sessionmaker, "alice", PASSWORD)
