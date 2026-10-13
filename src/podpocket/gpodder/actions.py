import logging
import math
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.catalog import find_podcast_by_url
from podpocket.ids import normalize_feed_url
from podpocket.models import ChangeLog, Episode, Podcast, Subscription, UserEpisodeState
from podpocket.sync.episodes import COMPLETED, IN_PROGRESS, UNPLAYED
from podpocket.sync.writer import EPISODE, INT32_MAX
from podpocket.timefmt import from_epoch_ms

logger = logging.getLogger(__name__)

PROGRESS_FIELDS = ("played_up_to", "playing_status")
MAX_RESPONSE_ACTIONS = 50_000
EPOCH_TEXT = "1970-01-01T00:00:00"


def gpodder_time(modified_ms: int) -> str:
    try:
        return f"{from_epoch_ms(modified_ms):%Y-%m-%dT%H:%M:%S}" if modified_ms > 0 else EPOCH_TEXT
    except OverflowError:
        return EPOCH_TEXT


def _seconds(value: float | int | None) -> int:
    if value is None or not math.isfinite(value):
        return 0
    return max(0, min(int(value), INT32_MAX))


@dataclass(frozen=True)
class EpisodeRef:
    feed_url: str
    enclosure_url: str
    guid: str | None
    duration: float


class Progress(Protocol):
    playing_status: int | None
    playing_status_modified: int | None
    played_up_to: int | None
    played_up_to_modified: int | None
    duration: int | None


@dataclass(frozen=True)
class ProgressState:
    playing_status: int | None
    playing_status_modified: int | None
    played_up_to: int | None
    played_up_to_modified: int | None
    duration: int | None


def _marked_unplayed_last(state: Progress) -> bool:
    return state.playing_status == UNPLAYED and (state.playing_status_modified or 0) >= (
        state.played_up_to_modified or 0
    )


def episode_action(state: Progress, ref: EpisodeRef) -> dict[str, Any] | None:
    stamps = [
        stamp
        for stamp in (state.played_up_to_modified, state.playing_status_modified)
        if stamp is not None
    ]
    if not stamps:
        return None
    action: dict[str, Any] = {
        "podcast": ref.feed_url,
        "episode": ref.enclosure_url,
        "timestamp": gpodder_time(max(stamps)),
    }
    if ref.guid:
        action["guid"] = ref.guid
    position = _seconds(state.played_up_to)
    duration = _seconds(state.duration) or _seconds(ref.duration)
    if state.playing_status == COMPLETED:
        total = max(duration, position, 1)
        return {**action, "action": "play", "started": 0, "position": total, "total": total}
    if _marked_unplayed_last(state):
        return {**action, "action": "new"}
    if position > 0 or state.playing_status == IN_PROGRESS:
        total = duration if duration > position else position + 1
        if total > INT32_MAX:
            position, total = INT32_MAX - 1, INT32_MAX
        return {**action, "action": "play", "started": 0, "position": position, "total": total}
    if state.playing_status == UNPLAYED:
        return {**action, "action": "new"}
    return None


async def _podcast_uuid_for(session: AsyncSession, feed_url: str) -> str | None:
    try:
        podcast = await find_podcast_by_url(session, normalize_feed_url(feed_url))
    except ValueError:
        return None
    return None if podcast is None else podcast.uuid


def _last_modified():
    played = func.coalesce(UserEpisodeState.played_up_to_modified, 0)
    status = func.coalesce(UserEpisodeState.playing_status_modified, 0)
    return case((played >= status, played), else_=status)


async def episode_actions_since(
    session: AsyncSession,
    user_uuid: str,
    since_ms: int,
    cursor_ms: int,
    podcast_url: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        select(
            UserEpisodeState.playing_status,
            UserEpisodeState.playing_status_modified,
            UserEpisodeState.played_up_to,
            UserEpisodeState.played_up_to_modified,
            UserEpisodeState.duration,
            Podcast.feed_url,
            Episode.enclosure_url,
            Episode.guid,
            Episode.duration.label("feed_duration"),
        )
        .join(Episode, Episode.uuid == UserEpisodeState.episode_uuid)
        .join(Podcast, Podcast.uuid == Episode.podcast_uuid)
        .join(
            Subscription,
            (Subscription.user_uuid == UserEpisodeState.user_uuid)
            & (Subscription.podcast_uuid == Episode.podcast_uuid),
        )
        .where(
            UserEpisodeState.user_uuid == user_uuid,
            Subscription.subscribed.is_(True),
            or_(
                UserEpisodeState.played_up_to_modified.is_not(None),
                UserEpisodeState.playing_status_modified.is_not(None),
            ),
        )
        .order_by(_last_modified().desc(), UserEpisodeState.episode_uuid)
        .limit(MAX_RESPONSE_ACTIONS + 1)
    )
    if podcast_url:
        podcast_uuid = await _podcast_uuid_for(session, podcast_url)
        if podcast_uuid is None:
            return []
        query = query.where(Episode.podcast_uuid == podcast_uuid)
    if since_ms > 0:
        changed = select(ChangeLog.entity_id).where(
            ChangeLog.user_uuid == user_uuid,
            ChangeLog.entity == EPISODE,
            ChangeLog.field.in_(PROGRESS_FIELDS),
            ChangeLog.server_ms > since_ms,
            ChangeLog.server_ms <= cursor_ms,
        )
        query = query.where(UserEpisodeState.episode_uuid.in_(changed))
    rows = (await session.execute(query)).mappings().all()
    if len(rows) > MAX_RESPONSE_ACTIONS:
        logger.warning("gpodder episode actions truncated to the newest %d", MAX_RESPONSE_ACTIONS)
        rows = rows[:MAX_RESPONSE_ACTIONS]
    actions = []
    for row in rows:
        state = ProgressState(
            playing_status=row["playing_status"],
            playing_status_modified=row["playing_status_modified"],
            played_up_to=row["played_up_to"],
            played_up_to_modified=row["played_up_to_modified"],
            duration=row["duration"],
        )
        ref = EpisodeRef(
            feed_url=row["feed_url"],
            enclosure_url=row["enclosure_url"],
            guid=row["guid"],
            duration=row["feed_duration"],
        )
        if (action := episode_action(state, ref)) is not None:
            actions.append(action)
    return actions
