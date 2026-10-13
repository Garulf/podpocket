from typing import Annotated

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from google.protobuf.message import DecodeError, Message
from pydantic import BaseModel, Field
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.types import Message as AsgiMessage

from podpocket.sync.writer import INT64_MAX, MAX_DECLARED_DIGITS

PROTOBUF_MEDIA_TYPE = "application/octet-stream"
PROTOBUF_MEDIA_TYPES = frozenset({PROTOBUF_MEDIA_TYPE, "application/x-protobuf"})
MAX_REQUEST_BYTES = 32 * 1024 * 1024
TOO_LARGE = "request body too large"

Int64 = Annotated[int, Field(ge=-INT64_MAX - 1, le=INT64_MAX)]


def is_protobuf(request: Request) -> bool:
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    return media_type in PROTOBUF_MEDIA_TYPES


async def read_message[M: Message](request: Request, message_type: type[M]) -> M:
    try:
        return message_type.FromString(await request.body())
    except (DecodeError, RecursionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="malformed protobuf body") from exc


async def read_json[T: BaseModel](request: Request, model: type[T]) -> T:
    try:
        return model.model_validate_json(await request.body())
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail="invalid request body") from exc


def protobuf_response(message: Message) -> Response:
    return Response(content=message.SerializeToString(), media_type=PROTOBUF_MEDIA_TYPE)


class BodySizeLimit:
    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if _declared_length(scope) > self.max_bytes:
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> AsgiMessage:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(status_code=413, detail=TOO_LARGE)
            return message

        await self.app(scope, limited_receive, send)


def _declared_length(scope: Scope) -> int:
    for name, value in scope["headers"]:
        if name == b"content-length":
            if not value.isdigit():
                return 0
            return int(value) if len(value) <= MAX_DECLARED_DIGITS else INT64_MAX
    return 0
