from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value, StringValue

from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import to_timestamp
from podpocket.sync.podcasts import HOME_FOLDER
from podpocket.sync.writer import ChangeWriter
from tests.api import post_proto

SYNC_UPDATE = "/api/user/sync/update"
CREATED = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
EPISODE_WRAPPERS = {
    "playing_status": Int32Value,
    "played_up_to": Int64Value,
    "duration": Int64Value,
    "is_deleted": BoolValue,
    "starred": BoolValue,
}


def podcast_upload(
    uuid: str, *, subscribed: bool = True, folder: str = HOME_FOLDER, sort_order: int | None = 3
) -> pb.Record:
    podcast = pb.SyncUserPodcast(
        uuid=uuid,
        subscribed=BoolValue(value=subscribed),
        is_deleted=BoolValue(value=not subscribed),
        folder_uuid=StringValue(value=folder),
        auto_start_from=Int32Value(value=0),
        auto_skip_last=Int32Value(value=0),
        sort_position=Int32Value(value=0),
    )
    if sort_order is not None:
        podcast.episodes_sort_order.CopyFrom(Int32Value(value=sort_order))
    return pb.Record(podcast=podcast)


def episode_upload(uuid: str, podcast_uuid: str = "p1", **fields: tuple[Any, int]) -> pb.Record:
    episode = pb.SyncUserEpisode(uuid=uuid, podcast_uuid=podcast_uuid)
    for name, (value, modified) in fields.items():
        if name == "deselected_chapters":
            episode.deselected_chapters = value
        else:
            getattr(episode, name).CopyFrom(EPISODE_WRAPPERS[name](value=value))
        getattr(episode, f"{name}_modified").CopyFrom(Int64Value(value=modified))
    return pb.Record(episode=episode)


def folder_upload(
    uuid: str,
    *,
    name: str = "Folder",
    deleted: bool = False,
    date_added: datetime | None = CREATED,
) -> pb.Record:
    folder = pb.SyncUserFolder(
        folder_uuid=uuid,
        name=name,
        color=2,
        sort_position=1,
        podcasts_sort_type=3,
        is_deleted=deleted,
    )
    if date_added is not None:
        folder.date_added.CopyFrom(to_timestamp(date_added))
    return pb.Record(folder=folder)


def playlist_episode(
    uuid: str, podcast_uuid: str = "p1", title: str | None = None
) -> pb.SyncPlaylistEpisode:
    episode = pb.SyncPlaylistEpisode(
        episode=uuid,
        podcast=podcast_uuid,
        added=Int64Value(value=1000),
        published=to_timestamp(CREATED),
    )
    if title is not None:
        episode.title.CopyFrom(StringValue(value=title))
    return episode


def playlist_upload(
    uuid: str,
    *,
    title: str = "Playlist",
    manual: bool = False,
    deleted: bool = False,
    episode_order: Iterable[str] = (),
    episodes: Iterable[pb.SyncPlaylistEpisode] = (),
) -> pb.Record:
    return pb.Record(
        playlist=pb.SyncUserPlaylist(
            uuid=uuid,
            original_uuid=uuid,
            is_deleted=BoolValue(value=deleted),
            title=StringValue(value=title),
            podcast_uuids=StringValue(value=""),
            all_podcasts=BoolValue(value=True),
            manual=BoolValue(value=manual),
            unplayed=BoolValue(value=True),
            sort_type=Int32Value(value=0),
            episode_order=list(episode_order),
            episodes=list(episodes),
        )
    )


def bookmark_upload(
    uuid: str,
    *,
    title: str = "Bookmark",
    title_modified: int = 1000,
    time: int = 60,
    deleted: bool | None = None,
    deleted_modified: int = 1000,
) -> pb.Record:
    bookmark = pb.SyncUserBookmark(
        bookmark_uuid=uuid,
        podcast_uuid="p1",
        episode_uuid="e1",
        created_at=to_timestamp(CREATED),
        time=Int32Value(value=time),
        title=StringValue(value=title),
        title_modified=Int64Value(value=title_modified),
    )
    if deleted is not None:
        bookmark.is_deleted.CopyFrom(BoolValue(value=deleted))
        bookmark.is_deleted_modified.CopyFrom(Int64Value(value=deleted_modified))
    return pb.Record(bookmark=bookmark)


def device_upload(
    device_id: str, *, listened: int, skipping: int = 0, started_at: int | None = None
) -> pb.Record:
    device = pb.SyncUserDevice(
        device_id=StringValue(value=device_id),
        device_type=Int32Value(value=2),
        time_listened=Int64Value(value=listened),
        time_skipping=Int64Value(value=skipping),
        time_silence_removal=Int64Value(value=0),
        time_intro_skipping=Int64Value(value=0),
        time_variable_speed=Int64Value(value=0),
    )
    if started_at is not None:
        device.times_started_at.CopyFrom(Int64Value(value=started_at))
    return pb.Record(device=device)


def unwrap(record: pb.Record) -> Any:
    return getattr(record, record.WhichOneof("record"))


async def merge(
    services: Any,
    user_uuid: str,
    apply: Callable[[ChangeWriter, list[Any]], Awaitable[set[str]]],
    items: Iterable[Any],
    *,
    now: int = 1_000_000,
) -> set[str]:
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user_uuid, source="app", now_ms=now)
        changed = await apply(writer, list(items))
        await session.commit()
    return changed


async def sync_update(
    client: httpx.AsyncClient, auth: dict[str, str], *records: pb.Record, last_modified: int = 0
) -> pb.SyncUpdateResponse:
    request = pb.SyncUpdateRequest(
        device_utc_time_ms=1, last_modified=last_modified, device_id="phone", records=list(records)
    )
    return await post_proto(client, SYNC_UPDATE, request, pb.SyncUpdateResponse, auth)


def records_of(response: pb.SyncUpdateResponse, kind: str) -> list[Any]:
    return [
        getattr(record, kind) for record in response.records if record.WhichOneof("record") == kind
    ]
