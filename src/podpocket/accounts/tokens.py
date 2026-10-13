import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.models import RefreshToken, RefreshTokenHash, User

ALGORITHM = "HS256"
SUPERSEDED_TOKEN_GRACE = timedelta(days=1)
MAX_LIVE_TOKENS_PER_SESSION = 5
REQUIRED_CLAIMS = ["exp", "sub", "sid"]
MAX_TOKEN_LENGTH = 4096


class InvalidToken(Exception):
    pass


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int


def utc_now() -> datetime:
    return datetime.now(UTC)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _require_plausible(token: str) -> None:
    if not token or len(token) > MAX_TOKEN_LENGTH or not token.isascii():
        raise InvalidToken("malformed token")


class TokenService:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        secret: str,
        access_ttl: timedelta,
        clock: Callable[[], datetime] = utc_now,
    ):
        self._sessionmaker = sessionmaker
        self._secret = secret
        self._access_ttl = access_ttl
        self._clock = clock

    async def issue(self, user: User, scope: str) -> IssuedTokens:
        now = self._clock()
        refresh_token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        async with self._sessionmaker() as session:
            session.add(
                RefreshToken(id=session_id, user_uuid=user.uuid, scope=scope, created_at=now)
            )
            await session.flush()
            session.add(
                RefreshTokenHash(
                    token_hash=_digest(refresh_token),
                    session_id=session_id,
                    created_at=now,
                    superseded_at=None,
                )
            )
            await session.commit()
        return self._issued(user.uuid, session_id, refresh_token, now)

    async def refresh(self, refresh_token: str) -> tuple[User, IssuedTokens]:
        _require_plausible(refresh_token)
        now = self._clock()
        digest = _digest(refresh_token)
        fresh = secrets.token_urlsafe(32)
        async with self._sessionmaker() as session:
            await session.execute(
                update(RefreshTokenHash)
                .where(
                    RefreshTokenHash.token_hash == digest, RefreshTokenHash.superseded_at.is_(None)
                )
                .values(superseded_at=now)
            )
            presented = await session.scalar(
                select(RefreshTokenHash).where(RefreshTokenHash.token_hash == digest)
            )
            if presented is None:
                raise InvalidToken("unknown refresh token")
            if presented.superseded_at is not None and (
                presented.superseded_at <= now - SUPERSEDED_TOKEN_GRACE
            ):
                raise InvalidToken("refresh token was replaced")
            session_id = presented.session_id
            login = await session.get(RefreshToken, session_id)
            user = None if login is None else await session.get(User, login.user_uuid)
            if user is None:
                raise InvalidToken("signed out")
            try:
                session.add(
                    RefreshTokenHash(
                        token_hash=_digest(fresh),
                        session_id=session_id,
                        created_at=now,
                        superseded_at=None,
                    )
                )
                await self._prune(session, session_id, now)
                await session.commit()
            except IntegrityError as exc:
                raise InvalidToken("signed out") from exc
        return user, self._issued(user.uuid, session_id, fresh, now)

    async def _prune(self, session: AsyncSession, session_id: str, now: datetime) -> None:
        await session.execute(
            delete(RefreshTokenHash).where(
                RefreshTokenHash.session_id == session_id,
                RefreshTokenHash.superseded_at < now - SUPERSEDED_TOKEN_GRACE,
            )
        )
        surplus = (
            await session.scalars(
                select(RefreshTokenHash.token_hash)
                .where(
                    RefreshTokenHash.session_id == session_id,
                    RefreshTokenHash.superseded_at.is_(None),
                )
                .order_by(RefreshTokenHash.created_at.desc())
                .offset(MAX_LIVE_TOKENS_PER_SESSION)
            )
        ).all()
        if surplus:
            await session.execute(
                update(RefreshTokenHash)
                .where(RefreshTokenHash.token_hash.in_(surplus))
                .values(superseded_at=now)
            )

    async def authenticate(self, access_token: str) -> User:
        _require_plausible(access_token)
        try:
            claims = jwt.decode(
                access_token,
                self._secret,
                algorithms=[ALGORITHM],
                options={"verify_exp": False, "verify_iat": False, "require": REQUIRED_CLAIMS},
            )
        except jwt.InvalidTokenError as exc:
            raise InvalidToken(str(exc)) from exc
        if claims["exp"] <= self._clock().timestamp():
            raise InvalidToken("access token expired")
        async with self._sessionmaker() as session:
            row = await session.get(RefreshToken, claims["sid"])
            user = await session.get(User, claims["sub"])
        if row is None or user is None or row.user_uuid != user.uuid:
            raise InvalidToken("signed out")
        return user

    def _issued(
        self, user_uuid: str, session_id: str, refresh_token: str, now: datetime
    ) -> IssuedTokens:
        claims = {
            "sub": user_uuid,
            "sid": session_id,
            "iat": int(now.timestamp()),
            "exp": int((now + self._access_ttl).timestamp()),
        }
        return IssuedTokens(
            access_token=jwt.encode(claims, self._secret, algorithm=ALGORITHM),
            refresh_token=refresh_token,
            expires_in=int(self._access_ttl.total_seconds()),
        )
