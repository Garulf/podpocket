from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from sqlalchemy import Row, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import Episode, UpNextItem
from podpocket.sync.writer import KEY_CHUNK, MAX_CLOCK_SKEW_MS, lock_user
from podpocket.timefmt import plausible_published

PLAY_NOW = 1
PLAY_NEXT = 2
PLAY_LAST = 3
REMOVE = 4
REPLACE = 5
SINGLE_EPISODE_ACTIONS = frozenset({PLAY_NOW, PLAY_NEXT, PLAY_LAST, REMOVE})
MAX_QUEUE = 10_000


@dataclass(frozen=True)
class QueueEntry:
    uuid: str
    podcast_uuid: str | None = None
    title: str | None = None
    url: str | None = None
    published: datetime | None = None


@dataclass(frozen=True)
class QueueChange:
    action: int
    modified_ms: int
    entry: QueueEntry | None = None
    entries: tuple[QueueEntry, ...] = ()


@dataclass(frozen=True)
class QueueState:
    server_modified: int
    entries: list[QueueEntry]


def apply_changes(
    queue: list[QueueEntry], changes: Iterable[QueueChange], replaced_ms: int
) -> tuple[list[QueueEntry], int]:
    order = OrderedDict((entry.uuid, entry) for entry in queue)
    for change in sorted(changes, key=lambda change: change.modified_ms):
        if change.action == REPLACE:
            if change.modified_ms >= replaced_ms:
                order = OrderedDict((entry.uuid, entry) for entry in _deduplicated(change.entries))
                replaced_ms = change.modified_ms
            continue
        if change.action not in SINGLE_EPISODE_ACTIONS or change.entry is None:
            continue
        if not change.entry.uuid or change.modified_ms < replaced_ms:
            continue
        _apply_single(order, change.action, change.entry)
        while len(order) > MAX_QUEUE:
            order.popitem()
    return list(order.values()), replaced_ms


def _apply_single(order: OrderedDict[str, QueueEntry], action: int, new: QueueEntry) -> None:
    entry = _with_known_metadata(new, order.pop(new.uuid, None))
    if action == REMOVE:
        return
    head = next(iter(order), None)
    order[entry.uuid] = entry
    if action == PLAY_LAST:
        return
    order.move_to_end(entry.uuid, last=False)
    if action == PLAY_NEXT and head is not None:
        order.move_to_end(head, last=False)


def _deduplicated(entries: Iterable[QueueEntry]) -> list[QueueEntry]:
    seen: set[str] = set()
    unique: list[QueueEntry] = []
    for entry in entries:
        if entry.uuid and entry.uuid not in seen:
            seen.add(entry.uuid)
            unique.append(entry)
            if len(unique) == MAX_QUEUE:
                break
    return unique


def _with_known_metadata(new: QueueEntry, old: QueueEntry | None) -> QueueEntry:
    if old is None:
        return new
    return QueueEntry(
        uuid=new.uuid,
        podcast_uuid=new.podcast_uuid or old.podcast_uuid,
        title=new.title or old.title,
        url=new.url or old.url,
        published=new.published or old.published,
    )


def _bounded(change: QueueChange, now_ms: int) -> QueueChange:
    return replace(
        change,
        modified_ms=min(change.modified_ms, now_ms + MAX_CLOCK_SKEW_MS),
        entry=_plausible(change.entry),
        entries=tuple(
            replace(entry, published=plausible_published(entry.published))
            for entry in change.entries
        ),
    )


def _plausible(entry: QueueEntry | None) -> QueueEntry | None:
    if entry is None:
        return None
    return replace(entry, published=plausible_published(entry.published))


async def sync_up_next(
    session: AsyncSession, user_uuid: str, changes: Iterable[QueueChange], now_ms: int
) -> QueueState:
    user = await lock_user(session, user_uuid)
    accepted = [_bounded(change, now_ms) for change in changes]
    rows = await session.execute(
        select(
            UpNextItem.episode_uuid,
            UpNextItem.podcast_uuid,
            UpNextItem.title,
            UpNextItem.url,
            UpNextItem.published,
        )
        .where(UpNextItem.user_uuid == user_uuid)
        .order_by(UpNextItem.position)
    )
    current = [QueueEntry(*row) for row in rows]
    updated, replaced_ms = apply_changes(current, accepted, user.up_next_replaced_ms)
    user.up_next_replaced_ms = replaced_ms
    if updated != current:
        await session.execute(delete(UpNextItem).where(UpNextItem.user_uuid == user_uuid))
        session.add_all(
            UpNextItem(
                user_uuid=user_uuid,
                position=position,
                episode_uuid=entry.uuid,
                podcast_uuid=entry.podcast_uuid,
                title=entry.title,
                url=entry.url,
                published=entry.published,
            )
            for position, entry in enumerate(updated)
        )
        user.up_next_modified_ms = max(now_ms, user.up_next_modified_ms + 1)
    return QueueState(user.up_next_modified_ms, updated)


async def with_catalog_metadata(session: AsyncSession, state: QueueState) -> QueueState:
    entries = state.entries
    incomplete = sorted(
        {
            entry.uuid
            for entry in entries
            if None in (entry.podcast_uuid, entry.title, entry.url, entry.published)
        }
    )
    episodes: dict[str, Row[Any]] = {}
    for start in range(0, len(incomplete), KEY_CHUNK):
        chunk = incomplete[start : start + KEY_CHUNK]
        rows = await session.execute(
            select(
                Episode.uuid,
                Episode.podcast_uuid,
                Episode.title,
                Episode.enclosure_url,
                Episode.published,
            ).where(Episode.uuid.in_(chunk))
        )
        episodes.update((row.uuid, row) for row in rows)
    return QueueState(
        state.server_modified, [_filled(entry, episodes.get(entry.uuid)) for entry in entries]
    )


def _filled(entry: QueueEntry, episode: Row[Any] | None) -> QueueEntry:
    if episode is None:
        return entry
    return QueueEntry(
        uuid=entry.uuid,
        podcast_uuid=entry.podcast_uuid or episode.podcast_uuid,
        title=entry.title or episode.title,
        url=entry.url or episode.enclosure_url,
        published=entry.published or plausible_published(episode.published),
    )
