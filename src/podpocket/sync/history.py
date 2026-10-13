from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import HistoryEntry
from podpocket.sync.upnext import EARLIEST_PUBLISHED
from podpocket.sync.writer import MAX_CLOCK_SKEW_MS, load_by_keys, lock_user, wins
from podpocket.timefmt import epoch_ms

ADD = 1
DELETE = 2
CLEAR = 3
FIRST_YEAR = 1970
LAST_YEAR = 9998


@dataclass(frozen=True)
class HistoryChange:
    action: int
    modified_ms: int
    episode_uuid: str | None = None
    podcast_uuid: str | None = None
    title: str | None = None
    url: str | None = None
    published: datetime | None = None


@dataclass(frozen=True)
class HistoryState:
    server_modified: int
    last_cleared: int
    entries: list[HistoryEntry]


def _bounded(change: HistoryChange, now_ms: int) -> HistoryChange:
    published = change.published
    return replace(
        change,
        modified_ms=min(change.modified_ms, now_ms + MAX_CLOCK_SKEW_MS),
        published=published if published is not None and published >= EARLIEST_PUBLISHED else None,
    )


def _latest_per_episode(changes: Iterable[HistoryChange]) -> dict[str, HistoryChange]:
    latest: dict[str, HistoryChange] = {}
    for change in changes:
        uuid = change.episode_uuid
        if change.action not in (ADD, DELETE) or not uuid:
            continue
        known = latest.get(uuid)
        if known is None or wins(
            change.modified_ms, change.action, known.modified_ms, known.action
        ):
            latest[uuid] = change
    return latest


async def sync_history(
    session: AsyncSession,
    user_uuid: str,
    changes: Iterable[HistoryChange],
    since_ms: int,
    now_ms: int,
) -> HistoryState:
    user = await lock_user(session, user_uuid)
    bounded = [_bounded(change, now_ms) for change in changes]
    stamp = max(now_ms, user.history_modified_ms + 1)
    accepted = False
    cleared = max((c.modified_ms for c in bounded if c.action == CLEAR), default=0)
    if cleared > user.history_cleared_ms:
        user.history_cleared_ms = cleared
        await session.execute(
            delete(HistoryEntry).where(
                HistoryEntry.user_uuid == user_uuid, HistoryEntry.modified_ms <= cleared
            )
        )
        accepted = True
    latest = _latest_per_episode(
        change for change in bounded if change.modified_ms > user.history_cleared_ms
    )
    stored = {
        entry.episode_uuid: entry
        for entry in await load_by_keys(
            session, HistoryEntry, HistoryEntry.episode_uuid, user_uuid, latest
        )
    }
    for uuid, change in latest.items():
        entry = stored.get(uuid)
        if entry is not None and not wins(
            change.modified_ms, change.action, entry.modified_ms, entry.action
        ):
            continue
        if entry is None:
            entry = HistoryEntry(user_uuid=user_uuid, episode_uuid=uuid)
            session.add(entry)
        entry.action = change.action
        entry.modified_ms = change.modified_ms
        entry.podcast_uuid = change.podcast_uuid or entry.podcast_uuid
        entry.title = change.title or entry.title
        entry.url = change.url or entry.url
        entry.published = change.published or entry.published
        entry.server_modified_ms = stamp
        accepted = True
    if accepted:
        user.history_modified_ms = stamp
    await session.flush()
    rows = await session.scalars(
        select(HistoryEntry)
        .where(HistoryEntry.user_uuid == user_uuid, HistoryEntry.server_modified_ms > since_ms)
        .order_by(HistoryEntry.modified_ms, HistoryEntry.episode_uuid)
    )
    return HistoryState(user.history_modified_ms, user.history_cleared_ms, list(rows))


async def history_for_year(session: AsyncSession, user_uuid: str, year: int) -> list[HistoryEntry]:
    if not FIRST_YEAR <= year <= LAST_YEAR:
        return []
    start = epoch_ms(datetime(year, 1, 1, tzinfo=UTC))
    end = epoch_ms(datetime(year + 1, 1, 1, tzinfo=UTC))
    return list(
        await session.scalars(
            select(HistoryEntry)
            .where(
                HistoryEntry.user_uuid == user_uuid,
                HistoryEntry.action == ADD,
                HistoryEntry.modified_ms >= start,
                HistoryEntry.modified_ms < end,
            )
            .order_by(HistoryEntry.modified_ms, HistoryEntry.episode_uuid)
        )
    )
