from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete

from podpocket.accounts.dependencies import CurrentUser
from podpocket.accounts.tokens import InvalidToken, TokenService
from podpocket.models import RefreshToken
from tests.factories import FakeClock

SECRET = "t" * 40
START = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    return FakeClock(START)


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
    await tokens.refresh(second.refresh_token)

    assert user.uuid == alice.uuid
    assert second.refresh_token != first.refresh_token
    assert (await tokens.authenticate(second.access_token)).uuid == alice.uuid
    with pytest.raises(InvalidToken):
        await tokens.refresh(first.refresh_token)


async def test_previous_refresh_token_survives_a_lost_response(tokens, alice):
    first = await tokens.issue(alice, "mobile")
    await tokens.refresh(first.refresh_token)

    _, retried = await tokens.refresh(first.refresh_token)
    _, later = await tokens.refresh(retried.refresh_token)

    assert (await tokens.authenticate(later.access_token)).uuid == alice.uuid


async def test_previous_refresh_token_stops_working_after_a_day(tokens, alice, clock):
    first = await tokens.issue(alice, "mobile")
    await tokens.refresh(first.refresh_token)

    clock.advance(timedelta(days=1, seconds=1))

    with pytest.raises(InvalidToken):
        await tokens.refresh(first.refresh_token)


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
