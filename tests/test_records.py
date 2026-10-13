from google.protobuf.wrappers_pb2 import BoolValue, Int64Value

from podpocket.models import UserEpisodeState
from podpocket.pcapi.records import episode_change, episode_record
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.episodes import FieldUpdate


def test_episode_record_always_carries_deselected_chapters():
    state = UserEpisodeState(
        user_uuid="u", episode_uuid="e", podcast_uuid="p", starred=True, starred_modified=5
    )

    record = episode_record(state)

    assert record.deselected_chapters == ""
    assert record.HasField("starred") and record.starred_modified.value == 5
    assert not record.HasField("played_up_to")
    assert not record.HasField("is_deleted")


def test_episode_record_maps_archived_to_is_deleted():
    state = UserEpisodeState(
        user_uuid="u",
        episode_uuid="e",
        podcast_uuid="p",
        archived=True,
        archived_modified=7,
        deselected_chapters="2,5",
        deselected_chapters_modified=8,
    )

    record = episode_record(state)

    assert (record.is_deleted.value, record.is_deleted_modified.value) == (True, 7)
    assert (record.deselected_chapters, record.deselected_chapters_modified.value) == ("2,5", 8)


def test_episode_change_reads_only_fields_with_a_modified_time():
    record = pb.SyncUserEpisode(
        uuid="e",
        podcast_uuid="p",
        played_up_to=Int64Value(value=30),
        played_up_to_modified=Int64Value(value=9),
        is_deleted=BoolValue(value=True),
        is_deleted_modified=Int64Value(value=8),
        starred=BoolValue(value=True),
        deselected_chapters="2,5",
        deselected_chapters_modified=Int64Value(value=7),
    )

    change = episode_change(record)

    assert (change.episode_uuid, change.podcast_uuid) == ("e", "p")
    assert change.played_up_to == FieldUpdate(30, 9)
    assert change.archived == FieldUpdate(True, 8)
    assert change.starred is None
    assert change.deselected_chapters == FieldUpdate("2,5", 7)
