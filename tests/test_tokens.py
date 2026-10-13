import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.accounts import tokens as tokens_module
from podpocket.accounts.dependencies import CurrentUser
from podpocket.accounts.tokens import InvalidToken, TokenService
from podpocket.accounts.users import remove_user, set_password
from podpocket.models import RefreshToken, RefreshTokenHash, User
from tests.factories import FakeClock

SECRET = "t" * 40
START = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    return FakeClock(START)


async def live_hashes(services):
    async with services.sessionmaker() as session:
        return (
            await session.scalars(
                select(RefreshTokenHash.token_hash).where(RefreshTokenHash.superseded_at.is_(None))
            )
        ).all()


@pytest.fixture
def tokens(services, clock):
    return TokenService(services.sessionmaker, SECRET, timedelta(hours=1), clock=clock)


async def test_issued_access_token_authenticates(tokens, alice):
    issued = await tokens.issue(alice, "mobile")

    assert issued.expires_in == 3600
    assert (await tokens.authenticate(issued.access_token)).uuid == alice.uuid


@pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
async def test_malformed_tokens_are_rejected(tokens, token):
    with pytest.raises(InvalidToken):
        await tokens.authenticate(token)


async def test_token_signed_with_another_key_is_rejected(tokens, services, alice, clock):
    other = TokenService(services.sessionmaker, "x" * 40, timedelta(hours=1), clock=clock)
    issued = await other.issue(alice, "mobile")

    with pytest.raises(InvalidToken):
        await tokens.authenticate(issued.access_token)


async def test_access_token_expires_after_an_hour(tokens, alice, clock):
    issued = await tokens.issue(alice, "mobile")

    clock.advance(timedelta(minutes=59))
    await tokens.authenticate(issued.access_token)
    clock.advance(timedelta(minutes=1))

    with pytest.raises(InvalidToken, match="expired"):
        await tokens.authenticate(issued.access_token)


async def test_refresh_rotates_the_refresh_token(tokens, alice):
    first = await tokens.issue(alice, "mobile")

    user, second = await tokens.refresh(first.refresh_token)

    assert user.uuid == alice.uuid
    assert second.refresh_token != first.refresh_token
    assert (await tokens.authenticate(second.access_token)).uuid == alice.uuid


async def test_superseded_refresh_token_survives_a_lost_response(tokens, alice):
    first = await tokens.issue(alice, "mobile")
    await tokens.refresh(first.refresh_token)

    _, retried = await tokens.refresh(first.refresh_token)
    _, later = await tokens.refresh(retried.refresh_token)

    assert (await tokens.authenticate(later.access_token)).uuid == alice.uuid


async def test_superseded_refresh_token_stops_working_after_a_day(tokens, alice, clock):
    first = await tokens.issue(alice, "mobile")
    await tokens.refresh(first.refresh_token)

    clock.advance(timedelta(hours=23, minutes=59))
    await tokens.refresh(first.refresh_token)
    clock.advance(timedelta(minutes=2))

    with pytest.raises(InvalidToken):
        await tokens.refresh(first.refresh_token)


async def test_superseded_refresh_token_does_not_extend_its_own_grace(tokens, alice, clock):
    first = await tokens.issue(alice, "mobile")
    await tokens.refresh(first.refresh_token)

    clock.advance(timedelta(hours=20))
    await tokens.refresh(first.refresh_token)
    clock.advance(timedelta(hours=10))

    with pytest.raises(InvalidToken):
        await tokens.refresh(first.refresh_token)


async def test_refresh_token_unused_for_days_still_works_until_superseded(tokens, alice, clock):
    issued = await tokens.issue(alice, "mobile")

    clock.advance(timedelta(days=90))
    _, rotated = await tokens.refresh(issued.refresh_token)

    assert rotated.refresh_token != issued.refresh_token


async def test_two_refreshes_with_the_same_token_both_succeed(tokens, alice, clock):
    issued = await tokens.issue(alice, "mobile")

    results = await asyncio.gather(
        tokens.refresh(issued.refresh_token), tokens.refresh(issued.refresh_token)
    )

    fresh = [rotated.refresh_token for _, rotated in results]
    assert len(set(fresh)) == 2
    clock.advance(timedelta(days=3))
    for refresh_token in fresh:
        _, again = await tokens.refresh(refresh_token)
        assert (await tokens.authenticate(again.access_token)).uuid == alice.uuid


async def test_many_racing_refreshes_keep_at_most_five_live_tokens(tokens, services, alice):
    issued = await tokens.issue(alice, "mobile")

    await asyncio.gather(*(tokens.refresh(issued.refresh_token) for _ in range(8)))

    assert len(await live_hashes(services)) == 5


async def test_live_tokens_beyond_five_supersede_the_oldest(tokens, services, alice, clock):
    issued = await tokens.issue(alice, "mobile")
    async with services.sessionmaker() as session:
        session_id = await session.scalar(select(RefreshToken.id))
        session.add_all(
            RefreshTokenHash(
                token_hash=f"{n:064d}",
                session_id=session_id,
                created_at=START + timedelta(minutes=n),
                superseded_at=None,
            )
            for n in range(1, 7)
        )
        await session.commit()
    clock.advance(timedelta(hours=1))

    await tokens.refresh(issued.refresh_token)

    live = await live_hashes(services)
    assert len(live) == 5
    assert {f"{n:064d}" for n in (3, 4, 5, 6)} <= set(live)
    assert not {f"{n:064d}" for n in (1, 2)} & set(live)


async def test_hashes_superseded_more_than_a_day_ago_are_pruned(tokens, services, alice, clock):
    first = await tokens.issue(alice, "mobile")
    _, second = await tokens.refresh(first.refresh_token)

    clock.advance(timedelta(days=2))
    await tokens.refresh(second.refresh_token)

    async with services.sessionmaker() as session:
        stored = set((await session.scalars(select(RefreshTokenHash.token_hash))).all())
    assert tokens_module._digest(first.refresh_token) not in stored
    assert tokens_module._digest(second.refresh_token) in stored


async def test_refresh_that_loses_a_race_with_a_sign_out_is_rejected(services, alice, clock):
    class SignsOutAfterUserLoad(AsyncSession):
        async def get(self, entity, ident, **kwargs):
            user = await super().get(entity, ident, **kwargs)
            if entity is User:
                await self.execute(delete(RefreshToken))
            return user

    racing = async_sessionmaker(services.sessionmaker.kw["bind"], class_=SignsOutAfterUserLoad)
    tokens = TokenService(racing, SECRET, timedelta(hours=1), clock=clock)
    issued = await tokens.issue(alice, "mobile")

    with pytest.raises(InvalidToken, match="signed out"):
        await tokens.refresh(issued.refresh_token)


async def test_unknown_refresh_token_is_rejected(tokens):
    with pytest.raises(InvalidToken):
        await tokens.refresh("not-a-token")


async def test_revoked_session_rejects_its_access_token(tokens, services, alice):
    issued = await tokens.issue(alice, "mobile")
    async with services.sessionmaker() as session:
        await session.execute(delete(RefreshToken))
        await session.commit()

    with pytest.raises(InvalidToken, match="signed out"):
        await tokens.authenticate(issued.access_token)


@pytest.fixture
async def guarded(services):
    app = FastAPI()
    app.state.services = services

    @app.get("/me")
    async def me(user: CurrentUser) -> dict[str, str]:
        return {"uuid": user.uuid}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://guarded.test") as http:
        yield http


async def test_current_user_accepts_a_valid_bearer_token(guarded, services, alice):
    issued = await services.tokens.issue(alice, "mobile")

    response = await guarded.get("/me", headers={"Authorization": f"Bearer {issued.access_token}"})

    assert response.json() == {"uuid": alice.uuid}


@pytest.mark.parametrize("header", [None, "", "Bearer", "Basic YWxpY2U6cHc=", "Bearer not-a-jwt"])
async def test_current_user_answers_401(guarded, header):
    headers = {} if header is None else {"Authorization": header}

    response = await guarded.get("/me", headers=headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


ODD_TOKENS = ["\udc80", "caf\u00e9", "\u20ac" * 10, "a" * 100_000, "a.b.\udcff"]


@pytest.mark.parametrize("token", ODD_TOKENS)
async def test_unencodable_or_huge_access_tokens_are_rejected(tokens, token):
    with pytest.raises(InvalidToken):
        await tokens.authenticate(token)


@pytest.mark.parametrize("token", ODD_TOKENS)
async def test_unencodable_or_huge_refresh_tokens_are_rejected(tokens, token):
    with pytest.raises(InvalidToken):
        await tokens.refresh(token)


@pytest.mark.parametrize(
    "token",
    ["café".encode("latin-1"), "€".encode() * 10, b"a" * 100_000],
    ids=["latin1", "utf8", "huge"],
)
async def test_current_user_answers_401_for_odd_bearer_tokens(guarded, token):
    response = await guarded.get("/me", headers={"Authorization": b"Bearer " + token})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_password_change_revokes_access_and_refresh_tokens(tokens, services, alice):
    issued = await tokens.issue(alice, "mobile")

    await set_password(services.sessionmaker, "alice", "a brand new password")

    with pytest.raises(InvalidToken):
        await tokens.authenticate(issued.access_token)
    with pytest.raises(InvalidToken):
        await tokens.refresh(issued.refresh_token)


async def test_removing_a_user_revokes_access_and_refresh_tokens(tokens, services, alice):
    issued = await tokens.issue(alice, "mobile")

    await remove_user(services.sessionmaker, "alice")

    with pytest.raises(InvalidToken):
        await tokens.authenticate(issued.access_token)
    with pytest.raises(InvalidToken):
        await tokens.refresh(issued.refresh_token)


def forged_claims(issued, clock):
    real = jwt.decode(
        issued.access_token, SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    return {**real, "exp": int(clock().timestamp()) + 3600}


async def test_unsigned_access_token_is_rejected(tokens, alice, clock):
    issued = await tokens.issue(alice, "mobile")
    forged = jwt.encode(forged_claims(issued, clock), None, algorithm="none")

    with pytest.raises(InvalidToken):
        await tokens.authenticate(forged)


async def test_access_token_signed_with_another_algorithm_is_rejected(services, alice, clock):
    long_secret = "t" * 64
    tokens = TokenService(services.sessionmaker, long_secret, timedelta(hours=1), clock=clock)
    issued = await tokens.issue(alice, "mobile")
    claims = jwt.decode(
        issued.access_token, long_secret, algorithms=["HS256"], options={"verify_exp": False}
    )
    forged = jwt.encode(claims, long_secret, algorithm="HS512")

    with pytest.raises(InvalidToken):
        await tokens.authenticate(forged)


async def test_unknown_refresh_token_never_takes_the_write_lock(tokens, services, alice):
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split(None, 1)[0].upper())

    engine = services.sessionmaker.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        with pytest.raises(InvalidToken):
            await tokens.refresh("not-a-token-we-ever-issued")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements
    assert set(statements) == {"SELECT"}
