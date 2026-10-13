import httpx
import pytest
from fastapi import FastAPI, Request
from pydantic import BaseModel

from podpocket.pcapi.wire import (
    MAX_REQUEST_BYTES,
    BodySizeLimit,
    Int64,
    is_protobuf,
    protobuf_response,
    read_json,
    read_message,
)
from podpocket.protocol import sync_api_pb2 as pb
from tests.api import PROTOBUF


class Ping(BaseModel):
    value: Int64


@pytest.fixture
async def echo():
    app = FastAPI()

    @app.post("/proto")
    async def proto(request: Request):
        message = await read_message(request, pb.SyncUpdateRequest)
        return protobuf_response(pb.SyncUpdateResponse(last_modified=message.last_modified))

    @app.post("/json")
    async def json_body(request: Request) -> dict[str, int]:
        return {"value": (await read_json(request, Ping)).value}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wire.test") as http:
        yield http


async def test_raw_protobuf_round_trips(echo):
    request = pb.SyncUpdateRequest(last_modified=42).SerializeToString()

    response = await echo.post("/proto", content=request, headers=PROTOBUF)

    assert response.headers["content-type"] == "application/octet-stream"
    assert pb.SyncUpdateResponse.FromString(response.content).last_modified == 42


async def test_empty_body_is_the_default_message(echo):
    response = await echo.post("/proto", content=b"", headers=PROTOBUF)

    assert response.status_code == 200
    assert pb.SyncUpdateResponse.FromString(response.content).last_modified == 0


async def test_corrupt_protobuf_is_a_400(echo):
    response = await echo.post("/proto", content=b"\xff\xff", headers=PROTOBUF)

    assert response.status_code == 400


async def test_truncated_protobuf_is_a_400(echo):
    whole = pb.SyncUpdateRequest(
        records=[pb.Record(podcast=pb.SyncUserPodcast(uuid="p" * 36))]
    ).SerializeToString()

    response = await echo.post("/proto", content=whole[:-5], headers=PROTOBUF)

    assert response.status_code == 400


async def test_deeply_nested_protobuf_is_a_400(echo):
    depth = 100_000
    nested = b"\x0b" * depth + b"\x0c" * depth

    response = await echo.post("/proto", content=nested, headers=PROTOBUF)

    assert response.status_code == 400


async def test_invalid_json_is_a_422(echo):
    assert (await echo.post("/json", json={"value": 3})).json() == {"value": 3}
    assert (await echo.post("/json", content=b"{nope")).status_code == 422


async def test_json_that_is_not_utf8_is_a_422(echo):
    response = await echo.post("/json", content=b'{"value": "\xff\xfe"}')

    assert response.status_code == 422


async def test_deeply_nested_json_is_a_422(echo):
    response = await echo.post("/json", content=b"[" * 100_000 + b"]" * 100_000)

    assert response.status_code == 422


async def test_json_integers_beyond_int64_are_a_422(echo):
    assert (await echo.post("/json", json={"value": 2**63 - 1})).status_code == 200
    assert (await echo.post("/json", json={"value": 2**63})).status_code == 422


async def test_json_integers_below_int64_are_a_422(echo):
    assert (await echo.post("/json", json={"value": -(2**63)})).status_code == 200
    assert (await echo.post("/json", json={"value": -(2**63) - 1})).status_code == 422


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/octet-stream", True),
        ("application/x-protobuf", True),
        ("application/json; charset=UTF-8", False),
        ("", False),
    ],
)
def test_is_protobuf(content_type, expected):
    request = Request({"type": "http", "headers": [(b"content-type", content_type.encode())]})

    assert is_protobuf(request) is expected


@pytest.fixture
async def capped():
    app = FastAPI()
    app.add_middleware(BodySizeLimit, max_bytes=10)

    @app.post("/raw")
    async def raw(request: Request) -> dict[str, int]:
        return {"size": len(await request.body())}

    @app.post("/model")
    async def model(body: Ping) -> dict[str, int]:
        return {"value": body.value}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://capped.test") as http:
        yield http


async def ten_byte_chunks(count):
    for _ in range(count):
        yield b"0123456789"


async def test_bodies_up_to_the_cap_are_read(capped):
    assert (await capped.post("/raw", content=b"0123456789")).json() == {"size": 10}
    streamed = await capped.post("/raw", content=ten_byte_chunks(1))
    assert streamed.json() == {"size": 10}


async def test_a_declared_oversized_body_is_refused(capped):
    response = await capped.post("/raw", content=b"x" * 11)

    assert response.status_code == 413


@pytest.mark.parametrize("path", ["/raw", "/model"])
async def test_a_streamed_oversized_body_is_refused(capped, path):
    response = await capped.post(
        path, content=ten_byte_chunks(3), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


async def test_an_absurd_content_length_is_refused_not_a_crash(capped):
    response = await capped.post("/raw", content=b"x", headers={"Content-Length": "9" * 5000})

    assert response.status_code == 413


async def test_an_unparseable_content_length_is_ignored(capped):
    response = await capped.post("/model", json={"value": 1}, headers={"Content-Length": "abc"})

    assert response.status_code == 413


async def test_the_app_caps_every_request_body(client):
    response = await client.post(
        "/api/user/login_pocket_casts",
        content=b"x" * (MAX_REQUEST_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413
