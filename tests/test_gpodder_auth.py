import base64

import pytest
from fastapi import APIRouter
from fastapi.responses import Response

from podpocket.accounts import passwords, throttle
from podpocket.accounts.throttle import MAX_FAILURES
from podpocket.accounts.users import remove_user, set_password
from podpocket.gpodder import credentials
from podpocket.gpodder.auth import GpodderRoute, GpodderUser, basic_credentials
from podpocket.gpodder.credentials import CredentialCache
from tests.api import PASSWORD
from tests.gpodder import basic

LOGIN = "/api/2/auth/alice/login.json"
ROUTES = [
    ("POST", "/api/2/auth/{user}/login.json"),
    ("POST", "/api/2/auth/{user}/logout.json"),
    ("GET", "/api/2/subscriptions/{user}/phone.json"),
    ("POST", "/api/2/subscriptions/{user}/phone.json"),
    ("POST", "/api/2/episodes/{user}.json"),
    ("GET", "/api/2/episodes/{user}.json"),
]
BODIES: dict[str, object] = {
    "/api/2/subscriptions/{user}/phone.json": {},
    "/api/2/episodes/{user}.json": [],
}


async def call(client, method, path, user, headers):
    body = BODIES.get(path) if method == "POST" else None
    return await client.request(method, path.format(user=user), json=body, headers=headers)


async def test_login_accepts_the_app_password(client, alice):
    response = await client.post(LOGIN, headers=basic("alice"))

    assert response.status_code == 200


async def test_logout_answers_200(client, alice):
    response = await client.post("/api/2/auth/alice/logout.json", headers=basic("alice"))

    assert response.status_code == 200


async def test_login_by_email_in_the_url(client, alice):
    response = await client.post(
        "/api/2/auth/alice@example.com/login.json", headers=basic("alice@example.com")
    )

    assert response.status_code == 200


async def test_username_in_the_url_is_case_insensitive(client, alice):
    response = await client.post("/api/2/auth/Alice/login.json", headers=basic("Alice"))

    assert response.status_code == 200


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_requires_credentials(client, alice, method, path):
    response = await call(client, method, path, "alice", {})

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic ")


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_rejects_a_wrong_password(client, alice, method, path):
    response = await call(client, method, path, "alice", basic("alice", "wrong password"))

    assert response.status_code == 401


@pytest.mark.parametrize(("method", "path"), ROUTES)
async def test_every_route_rejects_another_users_url(client, alice, bob, method, path):
    response = await call(client, method, path, "bob", basic("alice"))

    assert response.status_code == 403
    assert "bob" not in response.text


async def test_unknown_user_gets_the_same_answer_as_a_wrong_password(client, alice):
    unknown = await client.post("/api/2/auth/mallory/login.json", headers=basic("mallory"))
    wrong = await client.post(LOGIN, headers=basic("alice", "nope"))

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert "mallory" not in unknown.text


@pytest.mark.parametrize(
    "header",
    [
        "Bearer abc",
        "Basic",
        "Basic !!!not-base64!!!",
        "Basic " + base64.b64encode(b"no-colon").decode(),
        "Basic " + base64.b64encode(b":password-only").decode(),
        "Basic " + "A" * 5000,
    ],
)
async def test_malformed_authorization_is_a_401(client, alice, header):
    response = await client.post(LOGIN, headers={"Authorization": header})

    assert response.status_code == 401


def test_credentials_fall_back_to_latin_1():
    encoded = base64.b64encode("alice:pässword".encode("latin-1")).decode()

    assert basic_credentials(f"Basic {encoded}") == ("alice", "pässword")


def test_credentials_prefer_utf_8():
    encoded = base64.b64encode("alice:pässword".encode()).decode()

    assert basic_credentials(f"basic {encoded}") == ("alice", "pässword")


def test_password_may_contain_a_colon():
    encoded = base64.b64encode(b"alice:a:b").decode()

    assert basic_credentials(f"Basic {encoded}") == ("alice", "a:b")


async def test_password_change_locks_gpodder_out(client, services, alice):
    await set_password(services.sessionmaker, "alice", "new password")

    old = await client.post(LOGIN, headers=basic("alice", PASSWORD))
    new = await client.post(LOGIN, headers=basic("alice", "new password"))
    assert (old.status_code, new.status_code) == (401, 200)


async def test_repeated_wrong_passwords_lock_the_account_briefly(client, alice):
    for _ in range(MAX_FAILURES):
        await client.post(LOGIN, headers=basic("alice", "wrong password"))

    response = await client.post(LOGIN, headers=basic("alice"))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


async def test_app_and_gpodder_share_the_lockout(client, alice):
    for _ in range(MAX_FAILURES):
        await client.post(
            "/api/user/login_pocket_casts",
            json={"email": "alice", "password": "wrong password", "scope": "mobile"},
        )

    assert (await client.post(LOGIN, headers=basic("alice"))).status_code == 429


def test_nul_in_credentials_is_rejected():
    encoded = base64.b64encode(b"alice:pass\x00word").decode()

    assert basic_credentials(f"Basic {encoded}") is None


async def test_credentials_are_checked_before_the_body_is_parsed(app, client, alice):
    extra = APIRouter(prefix="/api/2", route_class=GpodderRoute)

    @extra.post("/probe/{username}.json")
    async def probe(user: GpodderUser, body: dict[str, int]) -> Response:
        return Response(status_code=200)

    app.include_router(extra)

    response = await client.post(
        "/api/2/probe/alice.json",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 401


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def hashing_checks(services, monkeypatch):
    checks = []
    original = services.login_gate.authenticate

    async def counted(*args):
        checks.append(args)
        return await original(*args)

    monkeypatch.setattr(services.login_gate, "authenticate", counted)
    return checks


def test_gpodder_route_without_a_username_segment_fails_at_startup():
    extra = APIRouter(route_class=GpodderRoute)

    with pytest.raises(RuntimeError, match="username"):

        @extra.get("/no-user-here")
        async def probe(user: GpodderUser) -> Response:
            return Response(status_code=200)


@pytest.mark.parametrize("url_user", ["alice", "nobody"])
async def test_missing_credentials_answer_401_whatever_the_url_names(client, alice, url_user):
    response = await client.post(f"/api/2/auth/{url_user}/login.json")

    assert response.status_code == 401


async def test_nonexistent_url_user_answers_403_after_authentication(client, alice, bob):
    existing = await client.post("/api/2/auth/bob/login.json", headers=basic("alice"))
    missing = await client.post("/api/2/auth/nobody/login.json", headers=basic("alice"))

    assert existing.status_code == missing.status_code == 403
    assert existing.json() == missing.json()


async def test_locked_unknown_login_answers_like_a_locked_real_one(client, alice):
    for login in ("alice", "mallory"):
        for _ in range(MAX_FAILURES):
            await client.post(f"/api/2/auth/{login}/login.json", headers=basic(login, "wrong"))

    real = await client.post(LOGIN, headers=basic("alice"))
    unknown = await client.post("/api/2/auth/mallory/login.json", headers=basic("mallory"))

    assert real.status_code == unknown.status_code == 429
    assert real.json() == unknown.json()
    assert real.headers["retry-after"] == unknown.headers["retry-after"] == "30"


async def test_a_busy_refusal_looks_the_same_but_retries_soon(client, alice, monkeypatch):
    monkeypatch.setattr(throttle, "LOGIN_WAIT_SECONDS", 0.01)
    passwords.configure(1)
    for _ in range(MAX_FAILURES):
        await client.post(LOGIN, headers=basic("alice", "wrong password"))
    locked = await client.post(LOGIN, headers=basic("alice"))

    async with passwords.hashing_slot(1):
        busy = await client.post("/api/2/auth/mallory/login.json", headers=basic("mallory"))

    assert busy.status_code == 429
    assert busy.json() == locked.json()
    assert busy.headers["retry-after"] == "2"


async def test_verified_credentials_skip_the_password_check(client, alice, hashing_checks):
    for _ in range(3):
        assert (await client.post(LOGIN, headers=basic("alice"))).status_code == 200

    assert len(hashing_checks) == 1


async def test_a_wrong_password_is_never_served_from_the_cache(client, alice, hashing_checks):
    await client.post(LOGIN, headers=basic("alice"))

    response = await client.post(LOGIN, headers=basic("alice", "wrong password"))

    assert response.status_code == 401
    assert len(hashing_checks) == 2


async def test_verified_credentials_ignore_the_lockout(client, alice):
    await client.post(LOGIN, headers=basic("alice"))
    for _ in range(MAX_FAILURES):
        await client.post(LOGIN, headers=basic("alice", "wrong password"))

    assert (await client.post(LOGIN, headers=basic("alice"))).status_code == 200


async def test_verified_credentials_expire_after_a_ttl_of_disuse(
    client, services, alice, hashing_checks
):
    clock = Clock()
    services.gpodder_credentials = CredentialCache("k" * 48, clock)
    await client.post(LOGIN, headers=basic("alice"))
    clock.now += credentials.CREDENTIAL_TTL_SECONDS - 1
    await client.post(LOGIN, headers=basic("alice"))
    assert len(hashing_checks) == 1

    clock.now += credentials.CREDENTIAL_TTL_SECONDS
    await client.post(LOGIN, headers=basic("alice"))

    assert len(hashing_checks) == 2


async def test_password_change_invalidates_verified_credentials(client, services, alice):
    await client.post(LOGIN, headers=basic("alice"))
    await set_password(services.sessionmaker, "alice", "new password")

    old = await client.post(LOGIN, headers=basic("alice", PASSWORD))
    new = await client.post(LOGIN, headers=basic("alice", "new password"))

    assert (old.status_code, new.status_code) == (401, 200)


async def test_removed_account_invalidates_verified_credentials(client, services, alice):
    await client.post(LOGIN, headers=basic("alice"))
    await remove_user(services.sessionmaker, "alice")

    assert (await client.post(LOGIN, headers=basic("alice"))).status_code == 401


async def test_least_recently_used_credentials_are_evicted(
    client, alice, bob, hashing_checks, monkeypatch
):
    monkeypatch.setattr(credentials, "MAX_CACHED_CREDENTIALS", 2)

    async def sign_in(login):
        await client.post(f"/api/2/auth/{login}/login.json", headers=basic(login))

    for login in ("alice", "alice@example.com", "bob", "alice@example.com"):
        await sign_in(login)
    assert len(hashing_checks) == 3

    await sign_in("alice")
    assert len(hashing_checks) == 4

    await sign_in("alice@example.com")
    assert len(hashing_checks) == 4
    await sign_in("bob")
    assert len(hashing_checks) == 5


def test_the_cache_keys_hide_the_credentials():
    cache = CredentialCache("k" * 48)
    fingerprint = cache._fingerprint("alice", "correct horse battery")

    assert len(fingerprint) == 32
    assert b"correct" not in fingerprint
    assert fingerprint != CredentialCache("j" * 48)._fingerprint("alice", "correct horse battery")


async def test_wrong_passwords_after_a_cache_hit_still_count_toward_the_lockout(client, alice):
    await client.post(LOGIN, headers=basic("alice"))
    await client.post(LOGIN, headers=basic("alice"))
    for _ in range(MAX_FAILURES):
        assert (
            await client.post(LOGIN, headers=basic("alice", "wrong password"))
        ).status_code == 401

    locked = await client.post(LOGIN, headers=basic("alice", "another wrong password"))

    assert locked.status_code == 429


async def test_verified_credentials_stay_valid_while_in_use(
    client, services, alice, hashing_checks
):
    clock = Clock()
    services.gpodder_credentials = CredentialCache("k" * 48, clock)
    await client.post(LOGIN, headers=basic("alice"))
    for _ in range(3):
        clock.now += credentials.CREDENTIAL_TTL_SECONDS - 1
        assert (await client.post(LOGIN, headers=basic("alice"))).status_code == 200
    assert len(hashing_checks) == 1

    clock.now += credentials.CREDENTIAL_TTL_SECONDS
    await client.post(LOGIN, headers=basic("alice"))

    assert len(hashing_checks) == 2


class EvictingSessionmaker:
    def __init__(self, sessionmaker, cache):
        self._sessionmaker = sessionmaker
        self._cache = cache

    def __call__(self):
        return self

    async def __aenter__(self):
        self._session = await self._sessionmaker().__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._session.__aexit__(*exc)

    async def get(self, *args):
        row = await self._session.get(*args)
        self._cache._verified.clear()
        return row


async def test_an_entry_evicted_during_the_lookup_is_not_an_error(services, alice):
    cache = CredentialCache("k" * 48)
    cache.remember("alice", PASSWORD, alice)

    user = await cache.lookup(EvictingSessionmaker(services.sessionmaker, cache), "alice", PASSWORD)

    assert user.uuid == alice.uuid
    assert cache._verified == {}
