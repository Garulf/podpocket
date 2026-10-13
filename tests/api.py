import httpx
from google.protobuf.message import Message

PASSWORD = "correct horse battery"
PROTOBUF = {"Content-Type": "application/octet-stream"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def sign_in(
    client: httpx.AsyncClient, login: str, password: str = PASSWORD
) -> dict[str, str]:
    response = await client.post(
        "/api/user/login_pocket_casts",
        json={"email": login, "password": password, "scope": "mobile"},
    )
    assert response.status_code == 200, response.text
    return bearer(response.json()["accessToken"])


async def post_proto[M: Message](
    client: httpx.AsyncClient,
    path: str,
    message: Message,
    response_type: type[M],
    headers: dict[str, str],
) -> M:
    response = await client.post(
        path, content=message.SerializeToString(), headers={**headers, **PROTOBUF}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/octet-stream"
    return response_type.FromString(response.content)
