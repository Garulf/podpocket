from collections.abc import Iterable, Iterator

from google.protobuf.message import Message
from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value, StringValue

from podpocket.models import Bookmark
from podpocket.protocol import from_timestamp, to_timestamp, wrapped
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.writer import (
    BOOKMARK,
    MAX_CLOCK_SKEW_MS,
    ChangeWriter,
    load_by_keys,
    wins,
)
from podpocket.timefmt import epoch_ms, from_epoch_ms

GROUPS = {
    "title": ("title",),
    "is_deleted": ("is_deleted",),
    "passage": ("passage", "passage_location"),
    "reference_time": ("reference_time",),
}
GROUPS_NEEDING_A_VALUE = ("title", "is_deleted")


def _group_updates(
    record: pb.SyncUserBookmark, latest_accepted_ms: int
) -> Iterator[tuple[str, int, tuple[object, ...]]]:
    for group, columns in GROUPS.items():
        modified = wrapped(record, f"{group}_modified")
        if modified is None:
            continue
        if group in GROUPS_NEEDING_A_VALUE and wrapped(record, group) is None:
            continue
        yield (
            group,
            min(modified, latest_accepted_ms),
            tuple(wrapped(record, column) for column in columns),
        )


def _valid_time(record: pb.SyncUserBookmark) -> int | None:
    time = wrapped(record, "time")
    return time if time is not None and time >= 0 else None


async def apply_bookmark_records(
    writer: ChangeWriter, records: Iterable[pb.SyncUserBookmark]
) -> set[str]:
    batch = [record for record in records if record.bookmark_uuid]
    bookmarks: dict[str, Bookmark] = {
        bookmark.bookmark_uuid: bookmark
        for bookmark in await load_by_keys(
            writer.session,
            Bookmark,
            Bookmark.bookmark_uuid,
            writer.user_uuid,
            (record.bookmark_uuid for record in batch),
        )
    }
    latest_accepted_ms = writer.now_ms + MAX_CLOCK_SKEW_MS
    changed: set[str] = set()
    for record in batch:
        uuid = record.bookmark_uuid
        time = _valid_time(record)
        groups = list(_group_updates(record, latest_accepted_ms))
        bookmark = bookmarks.get(uuid)
        if bookmark is None:
            if time is None and not groups:
                continue
            bookmark = _new_bookmark(writer, record)
            writer.session.add(bookmark)
            bookmarks[uuid] = bookmark
            writer.record(BOOKMARK, uuid, "created", writer.now_ms)
            changed.add(uuid)
        if time is not None and time != bookmark.time:
            bookmark.time = time
            writer.record(BOOKMARK, uuid, "time", writer.now_ms)
            changed.add(uuid)
        for group, modified, incoming in groups:
            columns = GROUPS[group]
            current = tuple(getattr(bookmark, column) for column in columns)
            if wins(modified, incoming, getattr(bookmark, f"{group}_modified"), current):
                for column, value in zip(columns, incoming, strict=True):
                    setattr(bookmark, column, value)
                setattr(bookmark, f"{group}_modified", modified)
                writer.record(BOOKMARK, uuid, group, modified)
                changed.add(uuid)
    return changed


def _new_bookmark(writer: ChangeWriter, record: pb.SyncUserBookmark) -> Bookmark:
    created = from_timestamp(record.created_at) if record.HasField("created_at") else None
    return Bookmark(
        user_uuid=writer.user_uuid,
        bookmark_uuid=record.bookmark_uuid,
        podcast_uuid=record.podcast_uuid,
        episode_uuid=record.episode_uuid,
        created_at=created or from_epoch_ms(writer.now_ms),
        time=0,
        title="",
        is_deleted=False,
    )


def bookmark_record(bookmark: Bookmark) -> pb.SyncUserBookmark:
    created_ms = epoch_ms(bookmark.created_at)
    title_modified = created_ms if bookmark.title_modified is None else bookmark.title_modified
    deleted_modified = (
        created_ms if bookmark.is_deleted_modified is None else bookmark.is_deleted_modified
    )
    record = pb.SyncUserBookmark(
        bookmark_uuid=bookmark.bookmark_uuid,
        podcast_uuid=bookmark.podcast_uuid,
        episode_uuid=bookmark.episode_uuid,
        created_at=to_timestamp(bookmark.created_at),
        time=Int32Value(value=bookmark.time),
        title=StringValue(value=bookmark.title),
        title_modified=Int64Value(value=title_modified),
        is_deleted=BoolValue(value=bookmark.is_deleted),
        is_deleted_modified=Int64Value(value=deleted_modified),
    )
    _add_optional_groups(record, bookmark)
    return record


def bookmark_response(bookmark: Bookmark) -> pb.BookmarkResponse:
    response = pb.BookmarkResponse(
        bookmark_uuid=bookmark.bookmark_uuid,
        podcast_uuid=bookmark.podcast_uuid,
        episode_uuid=bookmark.episode_uuid,
        time=bookmark.time,
        title=bookmark.title,
        createdAt=to_timestamp(bookmark.created_at),
    )
    _add_optional_groups(response, bookmark)
    return response


def _add_optional_groups(message: Message, bookmark: Bookmark) -> None:
    if bookmark.passage_modified is not None:
        message.passage_modified.CopyFrom(Int64Value(value=bookmark.passage_modified))
        if bookmark.passage is not None:
            message.passage.CopyFrom(StringValue(value=bookmark.passage))
        if bookmark.passage_location is not None:
            message.passage_location.CopyFrom(Int32Value(value=bookmark.passage_location))
    if bookmark.reference_time_modified is not None:
        message.reference_time_modified.CopyFrom(Int64Value(value=bookmark.reference_time_modified))
        if bookmark.reference_time is not None:
            message.reference_time.CopyFrom(Int32Value(value=bookmark.reference_time))
