import pytest
from sqlalchemy import select

from podpocket.accounts.users import (
    UserError,
    authenticate,
    create_user,
    list_users,
    remove_user,
    set_password,
)
from podpocket.models import RefreshToken
from tests.api import PASSWORD


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


async def test_set_password_changes_login_and_signs_out_devices(services, alice):
    async with services.sessionmaker() as session:
        session.add(
            RefreshToken(
                id="s1",
                user_uuid=alice.uuid,
                token_hash="h" * 64,
                scope="mobile",
                created_at=alice.created_at,
                rotated_at=alice.created_at,
            )
        )
        await session.commit()

    await set_password(services.sessionmaker, "alice@example.com", "new password")

    assert await authenticate(services.sessionmaker, "alice", PASSWORD) is None
    assert await authenticate(services.sessionmaker, "alice", "new password") is not None
    async with services.sessionmaker() as session:
        assert (await session.scalars(select(RefreshToken))).all() == []


async def test_remove_and_list(services, alice, bob):
    assert [user.username for user in await list_users(services.sessionmaker)] == ["alice", "bob"]

    removed = await remove_user(services.sessionmaker, "alice")

    assert removed.uuid == alice.uuid
    assert [user.username for user in await list_users(services.sessionmaker)] == ["bob"]
    with pytest.raises(UserError, match="no user named 'alice'"):
        await remove_user(services.sessionmaker, "alice")
