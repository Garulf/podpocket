import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.accounts.passwords import (
    hash_password,
    needs_rehash,
    spend_verification_time,
    verify_password,
)
from podpocket.models import RefreshToken, User
from podpocket.timefmt import epoch_ms

Sessionmaker = async_sessionmaker[AsyncSession]


class UserError(Exception):
    pass


def normalize_login(value: str) -> str:
    return value.strip().lower()


async def find_user(session: AsyncSession, login: str) -> User | None:
    login = normalize_login(login)
    if not login:
        return None
    by_name = await session.scalar(select(User).where(User.username == login))
    if by_name is not None:
        return by_name
    return await session.scalar(select(User).where(User.email == login))


async def create_user(
    sessionmaker: Sessionmaker,
    username: str,
    password: str,
    *,
    email: str | None = None,
    is_admin: bool = False,
) -> User:
    username = normalize_login(username)
    email = normalize_login(email) if email else None
    if not username or not password:
        raise UserError("username and password must not be empty")
    now = datetime.now(UTC)
    user = User(
        uuid=str(uuid.uuid4()),
        username=username,
        email=email,
        password_hash=await hash_password(password),
        is_admin=is_admin,
        created_at=now,
        last_change_ms=epoch_ms(now),
        history_modified_ms=epoch_ms(now),
    )
    async with sessionmaker() as session:
        for login in filter(None, (username, email)):
            if await find_user(session, login) is not None:
                raise UserError(f"{login!r} is already used by another account")
        session.add(user)
        try:
            await session.commit()
        except IntegrityError as exc:
            raise UserError(f"{username!r} is already used by another account") from exc
    return user


async def authenticate(sessionmaker: Sessionmaker, login: str, password: str) -> User | None:
    if not (_is_utf8_encodable(login) and _is_utf8_encodable(password)):
        await spend_verification_time("")
        return None
    async with sessionmaker() as session:
        user = await find_user(session, login)
    if user is None:
        await spend_verification_time(password)
        return None
    if not await verify_password(user.password_hash, password):
        return None
    if needs_rehash(user.password_hash):
        await _upgrade_hash(sessionmaker, user, password)
    return user


async def _upgrade_hash(sessionmaker: Sessionmaker, user: User, password: str) -> None:
    upgraded = await hash_password(password)
    async with sessionmaker() as session:
        await session.execute(
            update(User)
            .where(User.uuid == user.uuid, User.password_hash == user.password_hash)
            .values(password_hash=upgraded)
        )
        await session.commit()


def _is_utf8_encodable(value: str) -> bool:
    try:
        value.encode()
    except UnicodeEncodeError:
        return False
    return True


async def set_password(sessionmaker: Sessionmaker, login: str, password: str) -> User:
    if not password:
        raise UserError("password must not be empty")
    password_hash = await hash_password(password)
    async with sessionmaker() as session:
        user = await _require(session, login)
        user.password_hash = password_hash
        await session.execute(delete(RefreshToken).where(RefreshToken.user_uuid == user.uuid))
        await session.commit()
    return user


async def get_user(sessionmaker: Sessionmaker, login: str) -> User:
    async with sessionmaker() as session:
        return await _require(session, login)


async def remove_user(sessionmaker: Sessionmaker, login: str) -> User:
    async with sessionmaker() as session:
        user = await _require(session, login)
        await session.delete(user)
        await session.commit()
    return user


async def list_users(sessionmaker: Sessionmaker) -> list[User]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(User).order_by(User.username)))


async def _require(session: AsyncSession, login: str) -> User:
    user = await find_user(session, login)
    if user is None:
        raise UserError(f"no user named {login!r}")
    return user
