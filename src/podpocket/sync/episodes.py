from collections.abc import Iterable
from dataclasses import dataclass

from podpocket.models import UserEpisodeState
from podpocket.sync.writer import EPISODE, ChangeWriter, wins

FIELDS = (
    "playing_status",
    "played_up_to",
    "duration",
    "archived",
    "starred",
    "deselected_chapters",
)
PLAYING_STATUSES = frozenset({1, 2, 3})


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


def _acceptable(field: str, value: object) -> bool:
    match field:
        case "playing_status":
            return value in PLAYING_STATUSES
        case "duration":
            return isinstance(value, int) and value > 0
        case "played_up_to":
            return isinstance(value, int) and value >= 0
    return True


async def apply_episode_changes(writer: ChangeWriter, changes: Iterable[EpisodeChange]) -> set[str]:
    states: dict[str, UserEpisodeState] = {}
    changed: set[str] = set()
    for change in changes:
        uuid = change.episode_uuid
        if not uuid:
            continue
        state = states.get(uuid) or await writer.session.get(
            UserEpisodeState, (writer.user_uuid, uuid)
        )
        if state is None:
            state = UserEpisodeState(
                user_uuid=writer.user_uuid, episode_uuid=uuid, podcast_uuid=change.podcast_uuid
            )
            writer.session.add(state)
        states[uuid] = state
        if change.podcast_uuid:
            state.podcast_uuid = change.podcast_uuid
        for field in FIELDS:
            update = getattr(change, field)
            if update is None or not _acceptable(field, update.value):
                continue
            modified_column = f"{field}_modified"
            current_ms = getattr(state, modified_column)
            if wins(update.modified_ms, update.value, current_ms, getattr(state, field)):
                setattr(state, field, update.value)
                setattr(state, modified_column, update.modified_ms)
                writer.record(EPISODE, uuid, field, update.modified_ms)
                changed.add(uuid)
    return changed
