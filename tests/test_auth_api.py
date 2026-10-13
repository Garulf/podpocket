from datetime import datetime

import pytest

from tests.api import PASSWORD, bearer

LOGIN = "/api/user/login_pocket_casts"
TOKEN = "/api/user/token"
STATUS = "/api/subscription/status"
LOGIN_KEYS = {
    "email",
    "uuid",
    "isNew",
    "accessToken",
    "refreshToken",
    "tokenType",
    "expiresIn",
}


def credentials(login, password=PASSWORD):
    return {"email": login, "password": password, "scope": "mobile"}


async def test_login_returns_every_required_key(client, alice):
    response = await client.post(LOGIN, json=credentials("alice"))

    assert response.status_code == 200
    body = response.json()
    assert set(body) == LOGIN_KEYS
    assert body["email"] == "alice@example.com"
    assert body["uuid"] == alice.uuid
    assert body["isNew"] is False
    assert body["tokenType"] == "Bearer"
    assert body["expiresIn"] == 3600
    assert isinstance(body["accessToken"], str) and body["accessToken"]
    assert isinstance(body["refreshToken"], str) and body["refreshToken"]


async def test_login_accepts_an_email_in_any_case(client, alice):
    response = await client.post(LOGIN, json=credentials(" Alice@Example.com "))

    assert response.json()["uuid"] == alice.uuid


async def test_user_without_email_gets_the_username_as_email(client, bob):
    response = await client.post(LOGIN, json=credentials("bob"))

    assert response.json()["email"] == "bob"


@pytest.mark.parametrize(
    ("login", "password", "status", "message_id"),
    [
        ("alice", "wrong", 401, "login_password_incorrect"),
        ("nobody", PASSWORD, 401, "login_password_incorrect"),
        ("", PASSWORD, 400, "login_email_blank"),
        ("alice", "", 400, "login_password_blank"),
    ],
)
async def test_login_failures_use_the_app_error_shape(
    client, alice, login, password, status, message_id
):
    response = await client.post(LOGIN, json=credentials(login, password))

    assert response.status_code == status
    body = response.json()
    assert body["errorMessageId"] == message_id
    assert isinstance(body["errorMessage"], str) and body["errorMessage"]


async def test_refresh_grant_rotates_both_tokens(client, alice):
    login = (await client.post(LOGIN, json=credentials("alice"))).json()

    response = await client.post(
        TOKEN, json={"grant_type": "refresh_token", "refresh_token": login["refreshToken"]}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == LOGIN_KEYS
    assert body["uuid"] == alice.uuid
    assert body["refreshToken"] != login["refreshToken"]
    assert (await client.get(STATUS, headers=bearer(body["accessToken"]))).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        {"grant_type": "refresh_token", "refresh_token": "unknown"},
        {"grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": "abc"},
        {},
    ],
)
async def test_token_failures_are_400_with_an_error_key(client, body):
    response = await client.post(TOKEN, json=body)

    assert response.status_code == 400
    assert isinstance(response.json()["error"], str)


@pytest.mark.parametrize(
    "body", [{"grant_type": "refresh_token"}, {"grant_type": "refresh_token", "refresh_token": 7}]
)
async def test_refresh_grant_without_a_token_string_is_an_invalid_request(client, body):
    response = await client.post(TOKEN, json=body)

    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


@pytest.mark.parametrize(
    "content", [b"{not json", b"\xff\xfe", b"[1, 2]", b"[" * 200_000], ids=lambda c: str(c[:12])
)
async def test_token_with_a_body_that_is_not_an_object_is_400(client, content):
    response = await client.post(
        TOKEN, content=content, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 400


async def test_login_with_an_oversized_scope_is_rejected_without_a_server_error(client, alice):
    response = await client.post(LOGIN, json={**credentials("alice"), "scope": "x" * 33})

    assert response.status_code == 422


async def test_subscription_status_reports_an_active_patron(client, alice_auth):
    response = await client.get(STATUS, headers=alice_auth)

    body = response.json()
    for key in ("giftDays", "paid", "platform", "frequency", "type", "index"):
        assert isinstance(body[key], int), key
    assert body["autoRenewing"] is True
    assert body["paid"] == 1
    assert body["tier"] == "patron"
    assert datetime.fromisoformat(body["expiryDate"]).year == 2099
    assert body["createdAt"].endswith("Z")


async def test_subscription_status_requires_a_token(client):
    assert (await client.get(STATUS)).status_code == 401


@pytest.mark.parametrize("path", ["/api/user/register_pocket_casts", "/api/user/login_google"])
async def test_signup_and_google_are_refused_with_a_readable_message(client, path):
    response = await client.post(path, json={})

    assert response.status_code == 400
    assert set(response.json()) == {"errorMessage", "errorMessageId"}


@pytest.mark.parametrize(
    "path",
    [
        "/api/history/sync",
        "/api/user/podcast/episodes",
        "/api/sync/update_episode",
        "/api/user/named_settings/update",
    ],
)
async def test_the_body_is_not_parsed_before_the_caller_is_known(client, path):
    response = await client.post(
        path, content=b"{ not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 401


async def test_a_signed_in_caller_still_gets_body_errors(client, alice_auth):
    response = await client.post(
        "/api/history/sync",
        content=b"{ not json",
        headers={**alice_auth, "Content-Type": "application/json"},
    )

    assert response.status_code == 422
