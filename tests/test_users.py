import pytest
from argon2 import PasswordHasher
from sqlalchemy import select

from podpocket.accounts import passwords
from podpocket.accounts.users import (
    UserError,
    authenticate,
    create_user,
    list_users,
    remove_user,
    set_password,
)
from podpocket.models import RefreshToken, User
from tests.api import PASSWORD


async def add_refresh_token(services, user, token_id):
    async with services.sessionmaker() as session:
        session.add(
            RefreshToken(
                id=token_id,
                user_uuid=user.uuid,
                scope="mobile",
                created_at=user.created_at,
            )
        )
        await session.commit()


async def refresh_token_ids(services):
    async with services.sessionmaker() as session:
        return sorted((await session.scalars(select(RefreshToken.id))).all())


class SpyHasher(PasswordHasher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.verified = 0

    def verify(self, hash, password):
        self.verified += 1
        return super().verify(hash, password)


async def test_create_user_normalizes_and_hashes(services):
    user = await create_user(
        services.sessionmaker, " Alice ", PASSWORD, email="Alice@Example.COM", is_admin=True
    )

    assert (user.username, user.email, user.is_admin) == ("alice", "alice@example.com", True)
    assert user.password_hash.startswith("$argon2id$")
    assert PASSWORD not in user.password_hash
    assert user.last_change_ms > 0
    assert user.history_modified_ms == user.last_change_ms


@pytest.mark.parametrize("login", ["alice", "ALICE", " alice@example.com ", "Alice@Example.com"])
async def test_authenticate_accepts_username_or_email(services, alice, login):
    user = await authenticate(services.sessionmaker, login, PASSWORD)

    assert user is not None
    assert user.uuid == alice.uuid


async def test_authenticate_rejects_wrong_password_and_unknown_user(services, alice):
    assert await authenticate(services.sessionmaker, "alice", "wrong") is None
    assert await authenticate(services.sessionmaker, "nobody", PASSWORD) is None
    assert await authenticate(services.sessionmaker, "", PASSWORD) is None


async def test_names_and_emails_must_be_unique_across_both(services, alice):
    with pytest.raises(UserError, match="already used"):
        await create_user(services.sessionmaker, "ALICE", "pw")
    with pytest.raises(UserError, match="already used"):
        await create_user(services.sessionmaker, "alice@example.com", "pw")
    with pytest.raises(UserError, match="already used"):
        await create_user(services.sessionmaker, "carol", "pw", email="alice")
    with pytest.raises(UserError, match="must not be empty"):
        await create_user(services.sessionmaker, "carol", "")


async def test_set_password_changes_login_and_signs_out_only_that_user(services, alice, bob):
    await add_refresh_token(services, alice, "a")
    await add_refresh_token(services, bob, "b")

    await set_password(services.sessionmaker, "alice@example.com", "new password")

    assert await authenticate(services.sessionmaker, "alice", PASSWORD) is None
    assert await authenticate(services.sessionmaker, "alice", "new password") is not None
    assert await refresh_token_ids(services) == ["b"]


async def test_remove_and_list(services, alice, bob):
    await add_refresh_token(services, alice, "a")
    await add_refresh_token(services, bob, "b")
    assert [user.username for user in await list_users(services.sessionmaker)] == ["alice", "bob"]

    removed = await remove_user(services.sessionmaker, "alice")

    assert removed.uuid == alice.uuid
    assert [user.username for user in await list_users(services.sessionmaker)] == ["bob"]
    assert await refresh_token_ids(services) == ["b"]
    with pytest.raises(UserError, match="no user named 'alice'"):
        await remove_user(services.sessionmaker, "alice")


@pytest.mark.parametrize(
    ("login", "password"), [("\ud800", PASSWORD), ("alice", "\ud800"), ("\udfff", "\udfff")]
)
async def test_authenticate_rejects_unencodable_input(services, alice, login, password):
    assert await authenticate(services.sessionmaker, login, password) is None


async def test_authenticate_verifies_once_for_unknown_users(services, alice, monkeypatch):
    spy = SpyHasher(time_cost=1, memory_cost=8, parallelism=1)
    monkeypatch.setattr(passwords, "HASHER", spy)

    assert await authenticate(services.sessionmaker, "nobody", PASSWORD) is None
    assert spy.verified == 1
    assert await authenticate(services.sessionmaker, "\ud800", PASSWORD) is None
    assert spy.verified == 2
    assert await authenticate(services.sessionmaker, "alice", "\ud800") is None
    assert spy.verified == 3
    assert await authenticate(services.sessionmaker, "alice", "wrong") is None
    assert spy.verified == 4


async def test_authenticate_upgrades_an_outdated_hash(services, alice, monkeypatch):
    stronger = PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    monkeypatch.setattr(passwords, "HASHER", stronger)
    assert stronger.check_needs_rehash(alice.password_hash)

    assert await authenticate(services.sessionmaker, "alice", PASSWORD) is not None

    async with services.sessionmaker() as session:
        stored = await session.scalar(select(User.password_hash).where(User.uuid == alice.uuid))
    assert stored != alice.password_hash
    assert not stronger.check_needs_rehash(stored)
    assert await authenticate(services.sessionmaker, "alice", PASSWORD) is not None


async def test_authenticate_upgrade_keeps_refresh_tokens(services, alice, monkeypatch):
    await add_refresh_token(services, alice, "a")
    monkeypatch.setattr(
        passwords, "HASHER", PasswordHasher(time_cost=2, memory_cost=16, parallelism=1)
    )

    await authenticate(services.sessionmaker, "alice", PASSWORD)

    assert await refresh_token_ids(services) == ["a"]
