from collections import defaultdict
from collections.abc import Iterable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.accounts.dependencies import AuthenticatedRoute, CurrentUser
from podpocket.models import Bookmark, Folder, Playlist, Subscription, UserEpisodeState
from podpocket.pcapi.records import episode_change, episode_record, podcast_change, podcast_record
from podpocket.pcapi.wire import protobuf_response, read_message
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.services import Services, get_services
from podpocket.sync.bookmarks import apply_bookmark_records, bookmark_record
from podpocket.sync.devices import apply_device_records
from podpocket.sync.episodes import apply_episode_changes
from podpocket.sync.folders import apply_folder_records, folder_record
from podpocket.sync.playlists import apply_playlist_records, playlist_record
from podpocket.sync.podcasts import apply_podcast_changes
from podpocket.sync.writer import (
    APP_SOURCE,
    BOOKMARK,
    EPISODE,
    FOLDER,
    PLAYLIST,
    PODCAST,
    ChangeWriter,
    changed_since,
    load_by_keys,
)
from podpocket.timefmt import from_epoch_ms, iso_ms, now_ms

router = APIRouter(prefix="/api", route_class=AuthenticatedRoute)
ServicesDep = Annotated[Services, Depends(get_services)]
DEVICE = "device"
RECORD_SOURCES = (
    (PODCAST, Subscription, Subscription.podcast_uuid, podcast_record),
    (EPISODE, UserEpisodeState, UserEpisodeState.episode_uuid, episode_record),
    (FOLDER, Folder, Folder.folder_uuid, folder_record),
    (PLAYLIST, Playlist, Playlist.uuid, playlist_record),
    (BOOKMARK, Bookmark, Bookmark.bookmark_uuid, bookmark_record),
)


@router.post("/user/sync/update")
async def sync_update(request: Request, user: CurrentUser, services: ServicesDep) -> Response:
    message = await read_message(request, pb.SyncUpdateRequest)
    uploaded = _group(message.records)
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user.uuid, source=APP_SOURCE, now_ms=now_ms())
        await apply_podcast_changes(writer, [podcast_change(r) for r in uploaded[PODCAST]])
        await apply_folder_records(writer, uploaded[FOLDER])
        await apply_episode_changes(writer, [episode_change(r) for r in uploaded[EPISODE]])
        await apply_playlist_records(writer, uploaded[PLAYLIST])
        await apply_bookmark_records(writer, uploaded[BOOKMARK])
        await apply_device_records(session, user.uuid, uploaded[DEVICE])
        await session.flush()
        await writer.flush()
        touched = await changed_since(session, user.uuid, message.last_modified)
        for entity, keys in _uploaded_keys(uploaded).items():
            touched[entity] |= keys
        records = await _records(session, user.uuid, touched)
        cursor = writer.cursor
        await writer.commit()
    return protobuf_response(pb.SyncUpdateResponse(last_modified=cursor, records=records))


@router.post("/user/last_sync_at")
async def last_sync_at(user: CurrentUser) -> dict[str, str]:
    return {"lastSyncAt": iso_ms(from_epoch_ms(user.last_change_ms))}


def _group(records: Iterable[pb.Record]) -> defaultdict[str, list[Any]]:
    grouped: defaultdict[str, list[Any]] = defaultdict(list)
    for record in records:
        kind = record.WhichOneof("record")
        if kind is not None:
            grouped[kind].append(getattr(record, kind))
    return grouped


def _uploaded_keys(uploaded: defaultdict[str, list[Any]]) -> dict[str, set[str]]:
    return {
        PODCAST: {record.uuid for record in uploaded[PODCAST]},
        EPISODE: {record.uuid for record in uploaded[EPISODE]},
        FOLDER: {record.folder_uuid for record in uploaded[FOLDER]},
        PLAYLIST: {record.original_uuid or record.uuid for record in uploaded[PLAYLIST]},
        BOOKMARK: {record.bookmark_uuid for record in uploaded[BOOKMARK]},
    }


async def _records(
    session: AsyncSession, user_uuid: str, touched: defaultdict[str, set[str]]
) -> list[pb.Record]:
    records: list[pb.Record] = []
    for entity, model, key_column, serialize in RECORD_SOURCES:
        keys = touched[entity] - {""}
        for row in await load_by_keys(session, model, key_column, user_uuid, keys):
            records.append(pb.Record(**{entity: serialize(row)}))
    return records
