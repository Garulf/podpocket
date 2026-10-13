from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value

from podpocket.models import UserEpisodeState
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import wrapped
from podpocket.sync.episodes import EpisodeChange, FieldUpdate

EPISODE_WIRE = (
    ("playing_status", "playing_status", Int32Value),
    ("played_up_to", "played_up_to", Int64Value),
    ("duration", "duration", Int64Value),
    ("archived", "is_deleted", BoolValue),
    ("starred", "starred", BoolValue),
)


def episode_change(record: pb.SyncUserEpisode) -> EpisodeChange:
    updates = {}
    for column, wire, _ in EPISODE_WIRE:
        value = wrapped(record, wire)
        modified = wrapped(record, f"{wire}_modified")
        if value is not None and modified is not None:
            updates[column] = FieldUpdate(value, modified)
    chapters_modified = wrapped(record, "deselected_chapters_modified")
    if chapters_modified is not None:
        updates["deselected_chapters"] = FieldUpdate(record.deselected_chapters, chapters_modified)
    return EpisodeChange(episode_uuid=record.uuid, podcast_uuid=record.podcast_uuid, **updates)


def episode_record(state: UserEpisodeState) -> pb.SyncUserEpisode:
    record = pb.SyncUserEpisode(
        uuid=state.episode_uuid,
        podcast_uuid=state.podcast_uuid,
        deselected_chapters=state.deselected_chapters or "",
    )
    for column, wire, wrapper in EPISODE_WIRE:
        value = getattr(state, column)
        if value is not None:
            getattr(record, wire).CopyFrom(wrapper(value=value))
            modified = getattr(state, f"{column}_modified")
            getattr(record, f"{wire}_modified").CopyFrom(Int64Value(value=modified))
    if state.deselected_chapters_modified is not None:
        record.deselected_chapters_modified.CopyFrom(
            Int64Value(value=state.deselected_chapters_modified)
        )
    return record
