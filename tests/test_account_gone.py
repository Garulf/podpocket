import pytest
from sqlalchemy import delete

from podpocket.models import User
from podpocket.sync import writer
from tests.api import PROTOBUF

EPISODE_PING = {"uuid": "e1", "podcast": "p1", "position": 1, "duration": 2, "status": 2}


@pytest.fixture
def account_deleted_after_auth(monkeypatch, services):
    real_lock_user = writer.lock_user

    async def lock_after_deleting(session, user_uuid):
        async with services.sessionmaker() as other:
            await other.execute(delete(User).where(User.uuid == user_uuid))
            await other.commit()
        return await real_lock_user(session, user_uuid)

    monkeypatch.setattr(writer, "lock_user", lock_after_deleting)


@pytest.mark.parametrize(
    ("path", "request_options"),
    [
        ("/api/user/sync/update", {"content": b"", "headers": PROTOBUF}),
        ("/api/sync/update_episode", {"json": EPISODE_PING}),
    ],
)
async def test_account_deleted_mid_request_answers_401(
    client, alice_auth, account_deleted_after_auth, path, request_options
):
    headers = {**alice_auth, **request_options.get("headers", {})}
    body = {key: value for key, value in request_options.items() if key != "headers"}

    response = await client.post(path, headers=headers, **body)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
