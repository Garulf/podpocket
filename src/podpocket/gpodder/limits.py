import json
from typing import Annotated, Any

from fastapi import HTTPException, Request
from pydantic import AfterValidator

from podpocket.sync.writer import is_clean_text

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_ACTIONS = 1000
MAX_SUBSCRIPTION_CHANGES = 1000
MAX_URL_LENGTH = 2048
TOO_LARGE = "request body too large"


def _storable(text: str) -> str:
    if not is_clean_text(text):
        raise ValueError("text cannot be stored")
    return text


StorableText = Annotated[str, AfterValidator(_storable)]


def _declared_length(request: Request) -> int:
    declared = request.headers.get("content-length", "")
    return int(declared) if declared.isascii() and declared.isdigit() else 0


async def read_json(request: Request) -> Any:
    if _declared_length(request) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail=TOO_LARGE)
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail=TOO_LARGE)
    try:
        return json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="request body is not valid JSON") from exc
