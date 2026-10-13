from collections.abc import Iterable
from dataclasses import dataclass

from podpocket.models import UserEpisodeState
from podpocket.sync.writer import (
    EPISODE,
    INT64_MAX,
    INT64_MIN,
    MAX_CLOCK_SKEW_MS,
    ChangeWriter,
    load_by_keys,
    wins,
)

FIELDS = (
    "playing_status",
    "played_up_to",
    "duration",
    "archived",
    "starred",
    "deselected_chapters",
)
UNPLAYED = 1
IN_PROGRESS = 2
COMPLETED = 3
PLAYING_STATUSES = frozenset({UNPLAYED, IN_PROGRESS, COMPLETED})


@dataclass(frozen=True)
class FieldUpdate[T]:
    value: T
    modified_ms: int


@dataclass(frozen=True)
class EpisodeChange:
    episode_uuid: str
    podcast_uuid: str
    playing_status: FieldUpdate[int] | None = None
    played_up_to: FieldUpdate[int] | None = None
    duration: FieldUpdate[int] | None = None
    archived: FieldUpdate[bool] | None = None
    starred: FieldUpdate[bool] | None = None
    deselected_chapters: FieldUpdate[str] | None = None


def _is_int64(value: object) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool) and INT64_MIN <= value <= INT64_MAX
    )


def _acceptable(field: str, value: object) -> bool:
    match field:
        case "playing_status":
            return _is_int64(value) and value in PLAYING_STATUSES
        case "duration":
            return _is_int64(value) and value > 0
        case "played_up_to":
            return _is_int64(value) and value >= 0
        case "archived" | "starred":
            return isinstance(value, bool)
        case "deselected_chapters":
            return isinstance(value, str)
    return False


async def apply_episode_changes(writer: ChangeWriter, changes: Iterable[EpisodeChange]) -> set[str]:
    batch = [change for change in changes if change.episode_uuid]
    states: dict[str, UserEpisodeState] = {
        state.episode_uuid: state
        for state in await load_by_keys(
            writer.session,
            UserEpisodeState,
            UserEpisodeState.episode_uuid,
            writer.user_uuid,
            (change.episode_uuid for change in batch),
        )
    }
    latest_accepted_ms = writer.now_ms + MAX_CLOCK_SKEW_MS
    changed: set[str] = set()
    for change in batch:
        uuid = change.episode_uuid
        for field in FIELDS:
            update = getattr(change, field)
            if update is None or not _is_int64(update.modified_ms):
                continue
            if not _acceptable(field, update.value):
                continue
            modified_ms = min(update.modified_ms, latest_accepted_ms)
            state = states.get(uuid)
            modified_column = f"{field}_modified"
            current_ms = getattr(state, modified_column) if state else None
            current_value = getattr(state, field) if state else None
            if not wins(modified_ms, update.value, current_ms, current_value):
                continue
            if state is None:
                state = UserEpisodeState(
                    user_uuid=writer.user_uuid, episode_uuid=uuid, podcast_uuid=change.podcast_uuid
                )
                writer.session.add(state)
                states[uuid] = state
            if change.podcast_uuid:
                state.podcast_uuid = change.podcast_uuid
            setattr(state, field, update.value)
            setattr(state, modified_column, modified_ms)
            writer.record(EPISODE, uuid, field, modified_ms)
            changed.add(uuid)
    return changed
