from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from podpocket.accounts.dependencies import current_user

router = APIRouter(prefix="/api", dependencies=[Depends(current_user)])


def no_files() -> dict[str, int]:
    return {"totalFiles": 0, "totalSize": 0, "usedSize": 0}


def empty_protobuf() -> Response:
    return Response(content=b"", media_type="application/octet-stream")


@router.get("/files")
async def files() -> dict[str, Any]:
    return {"files": [], "account": no_files()}


@router.get("/files/usage")
async def files_usage() -> dict[str, int]:
    return no_files()


@router.post("/files")
async def update_files() -> Response:
    return Response(status_code=200)


@router.get("/user/podcast_rating/list")
async def podcast_ratings() -> Response:
    return empty_protobuf()


@router.post("/user/whats_new/read_state/list")
async def whats_new_read_state() -> Response:
    return empty_protobuf()


@router.put("/user/whats_new/read")
async def whats_new_read() -> Response:
    return Response(status_code=200)


@router.put("/user/whats_new/unread")
async def whats_new_unread() -> Response:
    return Response(status_code=200)
