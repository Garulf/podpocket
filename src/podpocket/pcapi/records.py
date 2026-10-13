from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value, StringValue

from podpocket.models import Subscription, UserEpisodeState
from podpocket.protocol import from_timestamp, to_timestamp, wrapped
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.episodes import EpisodeChange, FieldUpdate
from podpocket.sync.podcasts import DEFAULT_EPISODES_SORT_ORDER, HOME_FOLDER, PodcastChange

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


def podcast_change(record: pb.SyncUserPodcast) -> PodcastChange:
    subscribed = wrapped(record, "subscribed")
    if subscribed is None and record.HasField("is_deleted"):
        subscribed = not record.is_deleted.value
    return PodcastChange(
        podcast_uuid=record.uuid,
        subscribed=subscribed,
        folder_uuid=wrapped(record, "folder_uuid"),
        sort_position=wrapped(record, "sort_position"),
        episodes_sort_order=wrapped(record, "episodes_sort_order"),
        auto_start_from=wrapped(record, "auto_start_from"),
        auto_skip_last=wrapped(record, "auto_skip_last"),
        date_added=from_timestamp(record.date_added) if record.HasField("date_added") else None,
    )


def podcast_record(subscription: Subscription) -> pb.SyncUserPodcast:
    record = pb.SyncUserPodcast(
        uuid=subscription.podcast_uuid,
        subscribed=BoolValue(value=subscription.subscribed),
        is_deleted=BoolValue(value=not subscription.subscribed),
        folder_uuid=StringValue(value=subscription.folder_uuid or HOME_FOLDER),
        episodes_sort_order=Int32Value(value=_sort_order(subscription)),
        auto_start_from=Int32Value(value=subscription.auto_start_from or 0),
        auto_skip_last=Int32Value(value=subscription.auto_skip_last or 0),
    )
    if subscription.sort_position is not None:
        record.sort_position.CopyFrom(Int32Value(value=subscription.sort_position))
    if subscription.date_added is not None:
        record.date_added.CopyFrom(to_timestamp(subscription.date_added))
    return record


def podcast_list_entry(subscription: Subscription) -> pb.UserPodcastResponse:
    entry = pb.UserPodcastResponse(
        uuid=subscription.podcast_uuid,
        episodes_sort_order=_sort_order(subscription),
        auto_start_from=subscription.auto_start_from or 0,
        auto_skip_last=subscription.auto_skip_last or 0,
        folder_uuid=StringValue(value=subscription.folder_uuid or HOME_FOLDER),
    )
    if subscription.sort_position is not None:
        entry.sort_position.CopyFrom(Int32Value(value=subscription.sort_position))
    if subscription.date_added is not None:
        entry.date_added.CopyFrom(to_timestamp(subscription.date_added))
    return entry


def _sort_order(subscription: Subscription) -> int:
    if subscription.episodes_sort_order is None:
        return DEFAULT_EPISODES_SORT_ORDER
    return subscription.episodes_sort_order
