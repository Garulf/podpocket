import math
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import ChangeLog, Episode, Podcast, UserEpisodeState
from podpocket.sync.episodes import COMPLETED, IN_PROGRESS, UNPLAYED
from podpocket.sync.writer import EPISODE, INT32_MAX
from podpocket.timefmt import from_epoch_ms

PROGRESS_FIELDS = ("played_up_to", "playing_status")
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


def episode_action(state: UserEpisodeState, ref: EpisodeRef) -> dict[str, Any] | None:
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
    if position > 0 or state.playing_status == IN_PROGRESS:
        total = duration if duration > position else position + 1
        return {**action, "action": "play", "started": 0, "position": position, "total": total}
    if state.playing_status == UNPLAYED:
        return {**action, "action": "new"}
    return None


async def episode_actions_since(
    session: AsyncSession, user_uuid: str, since_ms: int, cursor_ms: int
) -> list[dict[str, Any]]:
    query = (
        select(
            UserEpisodeState,
            Podcast.feed_url,
            Episode.enclosure_url,
            Episode.guid,
            Episode.duration,
        )
        .join(Episode, Episode.uuid == UserEpisodeState.episode_uuid)
        .join(Podcast, Podcast.uuid == Episode.podcast_uuid)
        .where(UserEpisodeState.user_uuid == user_uuid)
        .order_by(UserEpisodeState.episode_uuid)
    )
    if since_ms > 0:
        changed = select(ChangeLog.entity_id).where(
            ChangeLog.user_uuid == user_uuid,
            ChangeLog.entity == EPISODE,
            ChangeLog.field.in_(PROGRESS_FIELDS),
            ChangeLog.server_ms > since_ms,
            ChangeLog.server_ms <= cursor_ms,
        )
        query = query.where(UserEpisodeState.episode_uuid.in_(changed))
    rows = await session.execute(query)
    return [
        action
        for state, *ref in rows
        if (action := episode_action(state, EpisodeRef(*ref))) is not None
    ]
