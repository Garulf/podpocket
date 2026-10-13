import base64

import pytest
from fastapi import APIRouter
from fastapi.responses import Response

from podpocket.accounts.throttle import MAX_FAILURES
from podpocket.accounts.users import set_password
from podpocket.gpodder.auth import GpodderRoute, GpodderUser, basic_credentials
from tests.api import PASSWORD
from tests.gpodder import basic

LOGIN = "/api/2/auth/alice/login.json"
ROUTES = [
    ("POST", "/api/2/auth/{user}/login.json"),
    ("POST", "/api/2/auth/{user}/logout.json"),
]
BODIES: dict[str, object] = {}


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
