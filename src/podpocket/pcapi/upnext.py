from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from podpocket.accounts.dependencies import CurrentUser
from podpocket.pcapi.wire import Int64, is_protobuf, protobuf_response, read_json, read_message
from podpocket.protocol import from_timestamp, to_timestamp
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.services import Services, get_services
from podpocket.sync.upnext import (
    QueueChange,
    QueueEntry,
    QueueState,
    sync_up_next,
    with_catalog_metadata,
)
from podpocket.timefmt import iso_z, now_ms, parse_iso

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]


class JsonEpisode(BaseModel):
    uuid: str
    title: str | None = None
    url: str | None = None
    podcast: str | None = None
    published: str | None = None


class JsonChange(BaseModel):
    action: int
    modified: Int64
    uuid: str | None = None
    title: str | None = None
    url: str | None = None
    podcast: str | None = None
    published: str | None = None
    episodes: list[JsonEpisode] | None = None


class JsonUpNext(BaseModel):
    serverModified: int = 0
    changes: list[JsonChange] = []


class JsonUpNextRequest(BaseModel):
    deviceTime: int = 0
    version: str = "2"
    upNext: JsonUpNext = Field(default_factory=JsonUpNext)


@router.post("/up_next/sync")
async def up_next_sync(request: Request, user: CurrentUser, services: ServicesDep) -> Response:
    if is_protobuf(request):
        message = await read_message(request, pb.UpNextSyncRequest)
        changes = [_proto_change(change) for change in message.up_next.changes]
        return protobuf_response(_proto_response(await _sync(services, user.uuid, changes)))
    body = await read_json(request, JsonUpNextRequest)
    changes = [_json_change(change) for change in body.upNext.changes]
    return JSONResponse(_json_response(await _sync(services, user.uuid, changes)))


async def _sync(services: Services, user_uuid: str, changes: Iterable[QueueChange]) -> QueueState:
    async with services.sessionmaker() as session:
        state = await sync_up_next(session, user_uuid, changes, now_ms())
        await session.commit()
        return await with_catalog_metadata(session, state)


def _json_entry(item: JsonChange | JsonEpisode) -> QueueEntry:
    return QueueEntry(
        uuid=item.uuid or "",
        podcast_uuid=item.podcast or None,
        title=item.title or None,
        url=item.url or None,
        published=parse_iso(item.published),
    )


def _json_change(change: JsonChange) -> QueueChange:
    return QueueChange(
        action=change.action,
        modified_ms=change.modified,
        entry=_json_entry(change) if change.uuid else None,
        entries=tuple(_json_entry(episode) for episode in change.episodes or []),
    )


def _proto_entry(item: pb.UpNextChanges.Change | pb.UpNextEpisodeRequest) -> QueueEntry:
    return QueueEntry(
        uuid=item.uuid,
        podcast_uuid=item.podcast or None,
        title=item.title or None,
        url=item.url or None,
        published=from_timestamp(item.published) if item.HasField("published") else None,
    )


def _proto_change(change: pb.UpNextChanges.Change) -> QueueChange:
    return QueueChange(
        action=change.action,
        modified_ms=change.modified,
        entry=_proto_entry(change) if change.uuid else None,
        entries=tuple(_proto_entry(episode) for episode in change.episodes),
    )


def _json_response(state: QueueState) -> dict[str, Any]:
    return {
        "serverModified": state.server_modified,
        "episodes": [_json_episode(entry) for entry in state.entries],
    }


def _json_episode(entry: QueueEntry) -> dict[str, Any]:
    body: dict[str, Any] = {"uuid": entry.uuid}
    fields = (
        ("title", entry.title),
        ("url", entry.url),
        ("podcast", entry.podcast_uuid),
        ("published", iso_z(entry.published) if entry.published else None),
    )
    for key, value in fields:
        if value is not None:
            body[key] = value
    return body


def _proto_response(state: QueueState) -> pb.UpNextResponse:
    response = pb.UpNextResponse(server_modified=state.server_modified)
    for entry in state.entries:
        episode = response.episodes.add(
            uuid=entry.uuid,
            title=entry.title or "",
            url=entry.url or "",
            podcast=entry.podcast_uuid or "",
        )
        if entry.published is not None:
            episode.published.CopyFrom(to_timestamp(entry.published))
    return response
