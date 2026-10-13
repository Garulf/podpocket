from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.wrappers_pb2 import BoolValue, Int32Value, Int64Value, StringValue

from podpocket.models import Bookmark, DeviceStats, Playlist
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import to_timestamp
from podpocket.sync.bookmarks import apply_bookmark_records, bookmark_record, bookmark_response
from podpocket.sync.devices import apply_device_records
from podpocket.sync.playlists import apply_playlist_records, playlist_list_entry, playlist_record
from podpocket.sync.writer import INT32_MAX, INT32_MIN, INT64_MAX, MAX_CLOCK_SKEW_MS
from podpocket.timefmt import epoch_ms, from_epoch_ms
from tests.records import (
    CREATED,
    bookmark_upload,
    device_upload,
    merge,
    playlist_episode,
    playlist_upload,
    unwrap,
)

NOW = 1_791_799_200_000


async def load(services, model, *key):
    async with services.sessionmaker() as session:
        return await session.get(model, key)


async def apply_devices(services, user_uuid, *uploads):
    async with services.sessionmaker() as session:
        await apply_device_records(session, user_uuid, [unwrap(upload) for upload in uploads])
        await session.commit()


async def test_smart_playlist_is_stored_and_echoed_by_original_uuid(services, alice):
    upload = unwrap(playlist_upload("AbC-1", title="Unplayed"))

    changed = await merge(services, alice.uuid, apply_playlist_records, [upload])

    assert changed == {"AbC-1"}
    stored = await load(services, Playlist, alice.uuid, "AbC-1")
    record = playlist_record(stored)
    assert (record.uuid, record.original_uuid, record.title.value) == ("AbC-1", "AbC-1", "Unplayed")
    assert record.HasField("unplayed") and record.unplayed.value is True
    assert not record.HasField("starred")
    entry = playlist_list_entry(stored)
    assert (entry.original_uuid, entry.title, entry.podcast_uuids) == ("AbC-1", "Unplayed", "")


async def test_playlist_is_keyed_and_echoed_by_original_uuid_when_uuid_differs(services, alice):
    upload = unwrap(playlist_upload("Original-1"))
    upload.uuid = "original-1"

    changed = await merge(services, alice.uuid, apply_playlist_records, [upload])

    assert changed == {"Original-1"}
    assert await load(services, Playlist, alice.uuid, "original-1") is None
    stored = await load(services, Playlist, alice.uuid, "Original-1")
    record = playlist_record(stored)
    assert (record.uuid, record.original_uuid) == ("Original-1", "Original-1")
    entry = playlist_list_entry(stored)
    assert (entry.uuid, entry.original_uuid) == ("Original-1", "Original-1")


async def test_playlist_without_original_uuid_falls_back_to_uuid(services, alice):
    upload = unwrap(playlist_upload("p1"))
    upload.original_uuid = ""

    assert await merge(services, alice.uuid, apply_playlist_records, [upload]) == {"p1"}
    assert playlist_record(await load(services, Playlist, alice.uuid, "p1")).original_uuid == "p1"


async def test_playlist_records_without_a_key_or_any_field_create_nothing(services, alice):
    keyless = pb.SyncUserPlaylist(title=StringValue(value="Lost"))
    empty = pb.SyncUserPlaylist(uuid="p1", original_uuid="p1")

    changed = await merge(services, alice.uuid, apply_playlist_records, [keyless, empty])

    assert changed == set()
    assert await load(services, Playlist, alice.uuid, "p1") is None


async def test_playlist_extreme_int32_values_survive_the_echo(services, alice):
    upload = unwrap(playlist_upload("p1"))
    upload.sort_position.CopyFrom(Int32Value(value=INT32_MAX))
    upload.filter_hours.CopyFrom(Int32Value(value=INT32_MIN))

    await merge(services, alice.uuid, apply_playlist_records, [upload])

    stored = await load(services, Playlist, alice.uuid, "p1")
    for record in (playlist_record(stored), playlist_list_entry(stored)):
        assert (record.sort_position.value, record.filter_hours.value) == (INT32_MAX, INT32_MIN)


async def test_manual_playlist_keeps_every_episode_in_order(services, alice):
    first = playlist_upload(
        "m1",
        manual=True,
        episode_order=["e1", "e2"],
        episodes=[playlist_episode("e1", title="One"), playlist_episode("e2")],
    )
    later = playlist_upload(
        "m1", manual=True, episode_order=["e3", "e1"], episodes=[playlist_episode("e3")]
    )

    await merge(services, alice.uuid, apply_playlist_records, [unwrap(first)])
    await merge(services, alice.uuid, apply_playlist_records, [unwrap(later)])

    record = playlist_record(await load(services, Playlist, alice.uuid, "m1"))
    assert list(record.episode_order) == ["e3", "e1"]
    assert [episode.episode for episode in record.episodes] == ["e3", "e1"]
    assert record.episodes[1].title.value == "One"
    assert record.episodes[0].published == to_timestamp(CREATED)


async def test_manual_playlist_drops_episodes_without_a_uuid(services, alice):
    upload = playlist_upload(
        "m1",
        manual=True,
        episode_order=["", "e1"],
        episodes=[playlist_episode(""), playlist_episode("e1")],
    )

    await merge(services, alice.uuid, apply_playlist_records, [unwrap(upload)])

    record = playlist_record(await load(services, Playlist, alice.uuid, "m1"))
    assert [episode.episode for episode in record.episodes] == ["e1"]


async def test_two_records_for_one_new_playlist_in_a_batch_merge(services, alice):
    first = unwrap(playlist_upload("p1", title="First"))
    second = unwrap(playlist_upload("p1", title="Second"))

    changed = await merge(services, alice.uuid, apply_playlist_records, [first, second])

    assert changed == {"p1"}
    assert (await load(services, Playlist, alice.uuid, "p1")).title == "Second"


async def test_one_users_playlist_upload_leaves_another_users_playlist_alone(services, alice, bob):
    await merge(services, alice.uuid, apply_playlist_records, [unwrap(playlist_upload("p1"))])

    changed = await merge(
        services, bob.uuid, apply_playlist_records, [unwrap(playlist_upload("p1", title="Mine"))]
    )

    assert changed == {"p1"}
    assert (await load(services, Playlist, alice.uuid, "p1")).title == "Playlist"
    assert (await load(services, Playlist, bob.uuid, "p1")).title == "Mine"


async def test_impossible_timestamps_in_uploads_are_dropped(services, alice):
    episode = playlist_episode("e1")
    episode.published.CopyFrom(Timestamp(seconds=2**62))
    bookmark = unwrap(bookmark_upload("b1"))
    bookmark.created_at.CopyFrom(Timestamp(seconds=-(2**62)))
    playlist = unwrap(playlist_upload("m1", manual=True, episode_order=["e1"], episodes=[episode]))

    await merge(services, alice.uuid, apply_playlist_records, [playlist], now=NOW)
    await merge(services, alice.uuid, apply_bookmark_records, [bookmark], now=NOW)

    record = playlist_record(await load(services, Playlist, alice.uuid, "m1"))
    assert not record.episodes[0].HasField("published")
    assert (await load(services, Bookmark, alice.uuid, "b1")).created_at == from_epoch_ms(NOW)


async def test_deleted_playlist_is_kept_as_a_tombstone(services, alice):
    await merge(services, alice.uuid, apply_playlist_records, [unwrap(playlist_upload("p"))])

    await merge(
        services, alice.uuid, apply_playlist_records, [unwrap(playlist_upload("p", deleted=True))]
    )

    assert playlist_record(await load(services, Playlist, alice.uuid, "p")).is_deleted.value is True


async def test_bookmark_title_uses_last_writer_wins(services, alice):
    newer = unwrap(bookmark_upload("b1", title="New", title_modified=2000))
    older = unwrap(bookmark_upload("b1", title="Old", title_modified=1000))

    await merge(services, alice.uuid, apply_bookmark_records, [newer])
    changed = await merge(services, alice.uuid, apply_bookmark_records, [older])

    assert changed == set()
    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert (stored.title, stored.title_modified, stored.time) == ("New", 2000, 60)
    assert stored.created_at == CREATED


async def test_bookmark_created_at_is_set_once(services, alice):
    await merge(services, alice.uuid, apply_bookmark_records, [unwrap(bookmark_upload("b1"))])
    later = unwrap(bookmark_upload("b1", title="Renamed", title_modified=2000))
    later.created_at.CopyFrom(to_timestamp(from_epoch_ms(NOW)))

    await merge(services, alice.uuid, apply_bookmark_records, [later])

    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert (stored.title, stored.created_at) == ("Renamed", CREATED)


async def test_bookmark_delete_is_echoed_with_its_time(services, alice):
    await merge(services, alice.uuid, apply_bookmark_records, [unwrap(bookmark_upload("b1"))])

    deletion = unwrap(bookmark_upload("b1", deleted=True, deleted_modified=3000))
    await merge(services, alice.uuid, apply_bookmark_records, [deletion])

    record = bookmark_record(await load(services, Bookmark, alice.uuid, "b1"))
    assert (record.is_deleted.value, record.is_deleted_modified.value) == (True, 3000)
    assert record.title.value == "Bookmark"


async def test_bookmark_passage_group_is_replaced_as_a_unit(services, alice):
    first = unwrap(bookmark_upload("b1"))
    first.passage.CopyFrom(StringValue(value="old line"))
    first.passage_location.CopyFrom(Int32Value(value=5))
    first.passage_modified.CopyFrom(Int64Value(value=2000))
    second = unwrap(bookmark_upload("b1"))
    second.passage.CopyFrom(StringValue(value="new line"))
    second.passage_modified.CopyFrom(Int64Value(value=3000))

    await merge(services, alice.uuid, apply_bookmark_records, [first])
    await merge(services, alice.uuid, apply_bookmark_records, [second])

    record = bookmark_record(await load(services, Bookmark, alice.uuid, "b1"))
    assert (record.passage.value, record.passage_modified.value) == ("new line", 3000)
    assert not record.HasField("passage_location")


async def test_bookmark_reference_time_uses_its_own_modified_time(services, alice):
    upload = unwrap(bookmark_upload("b1"))
    upload.reference_time.CopyFrom(Int32Value(value=INT32_MAX))
    upload.reference_time_modified.CopyFrom(Int64Value(value=7000))

    await merge(services, alice.uuid, apply_bookmark_records, [upload])

    record = bookmark_record(await load(services, Bookmark, alice.uuid, "b1"))
    assert (record.reference_time.value, record.reference_time_modified.value) == (INT32_MAX, 7000)
    assert record.title_modified.value == 1000


async def test_bookmark_value_without_its_modified_time_is_ignored(services, alice):
    await merge(services, alice.uuid, apply_bookmark_records, [unwrap(bookmark_upload("b1"))])
    orphan = pb.SyncUserBookmark(
        bookmark_uuid="b1",
        title=StringValue(value="Orphan"),
        is_deleted=BoolValue(value=True),
        passage=StringValue(value="ignored"),
    )

    changed = await merge(services, alice.uuid, apply_bookmark_records, [orphan])

    assert changed == set()
    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert (stored.title, stored.is_deleted, stored.passage) == ("Bookmark", False, None)


async def test_bookmark_modified_time_without_its_value_is_ignored(services, alice):
    await merge(services, alice.uuid, apply_bookmark_records, [unwrap(bookmark_upload("b1"))])
    orphan = pb.SyncUserBookmark(
        bookmark_uuid="b1",
        title_modified=Int64Value(value=9000),
        is_deleted_modified=Int64Value(value=9000),
    )

    changed = await merge(services, alice.uuid, apply_bookmark_records, [orphan])

    assert changed == set()
    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert (stored.title, stored.title_modified, stored.is_deleted_modified) == (
        "Bookmark",
        1000,
        None,
    )


async def test_bookmark_without_a_usable_field_creates_nothing(services, alice):
    bare = pb.SyncUserBookmark(bookmark_uuid="b1", podcast_uuid="p1", episode_uuid="e1")
    negative = pb.SyncUserBookmark(bookmark_uuid="b2", time=Int32Value(value=-1))
    keyless = pb.SyncUserBookmark(title=StringValue(value="Lost"))

    changed = await merge(services, alice.uuid, apply_bookmark_records, [bare, negative, keyless])

    assert changed == set()
    assert await load(services, Bookmark, alice.uuid, "b1") is None
    assert await load(services, Bookmark, alice.uuid, "b2") is None


async def test_bookmark_negative_time_is_dropped_but_the_rest_is_kept(services, alice):
    upload = unwrap(bookmark_upload("b1", time=-5))

    await merge(services, alice.uuid, apply_bookmark_records, [upload])

    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert (stored.time, stored.title) == (0, "Bookmark")


async def test_a_far_future_bookmark_clock_cannot_make_a_field_unbeatable(services, alice):
    wild = unwrap(bookmark_upload("b1", title="Wild", title_modified=INT64_MAX))
    await merge(services, alice.uuid, apply_bookmark_records, [wild], now=NOW)
    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert stored.title_modified == NOW + MAX_CLOCK_SKEW_MS

    later = NOW + 1000
    honest = unwrap(bookmark_upload("b1", title="Honest", title_modified=later))
    await merge(services, alice.uuid, apply_bookmark_records, [honest], now=later)

    assert (await load(services, Bookmark, alice.uuid, "b1")).title == "Honest"


async def test_bookmark_unrepresentable_modified_time_survives_the_echo(services, alice):
    upload = unwrap(bookmark_upload("b1", title_modified=INT64_MAX))
    upload.passage_modified.CopyFrom(Int64Value(value=INT64_MAX))

    await merge(services, alice.uuid, apply_bookmark_records, [upload], now=NOW)

    stored = await load(services, Bookmark, alice.uuid, "b1")
    assert bookmark_record(stored).title_modified.value == NOW + MAX_CLOCK_SKEW_MS
    assert bookmark_response(stored).passage_modified.value == NOW + MAX_CLOCK_SKEW_MS


async def test_two_records_for_one_new_bookmark_in_a_batch_merge(services, alice):
    first = unwrap(bookmark_upload("b1", title="First", title_modified=1000))
    second = unwrap(bookmark_upload("b1", title="Second", title_modified=2000))

    changed = await merge(services, alice.uuid, apply_bookmark_records, [second, first])

    assert changed == {"b1"}
    assert (await load(services, Bookmark, alice.uuid, "b1")).title == "Second"


async def test_one_users_bookmark_upload_leaves_another_users_bookmark_alone(services, alice, bob):
    await merge(services, alice.uuid, apply_bookmark_records, [unwrap(bookmark_upload("b1"))])

    mine = unwrap(bookmark_upload("b1", title="Mine", title_modified=5000))
    await merge(services, bob.uuid, apply_bookmark_records, [mine])

    assert (await load(services, Bookmark, alice.uuid, "b1")).title == "Bookmark"
    assert (await load(services, Bookmark, bob.uuid, "b1")).title == "Mine"


def test_bookmark_response_carries_created_at_and_plain_fields():
    bookmark = Bookmark(
        user_uuid="u",
        bookmark_uuid="b1",
        podcast_uuid="p1",
        episode_uuid="e1",
        time=75,
        created_at=CREATED,
        title="Intro",
        is_deleted=False,
        reference_time=12,
        reference_time_modified=9,
    )

    response = bookmark_response(bookmark)

    assert (response.bookmark_uuid, response.time, response.title) == ("b1", 75, "Intro")
    assert response.createdAt == to_timestamp(CREATED)
    assert (response.reference_time.value, response.reference_time_modified.value) == (12, 9)
    assert not response.HasField("passage_modified")


def test_bookmark_record_without_modified_times_uses_created_at():
    bookmark = Bookmark(
        user_uuid="u",
        bookmark_uuid="b1",
        podcast_uuid="p1",
        episode_uuid="e1",
        time=0,
        created_at=CREATED,
        title="",
        is_deleted=False,
    )

    record = bookmark_record(bookmark)

    assert record.title_modified.value == epoch_ms(CREATED)
    assert record.is_deleted_modified.value == epoch_ms(CREATED)


async def test_device_stats_are_replaced_not_added(services, alice):
    await apply_devices(
        services, alice.uuid, device_upload("phone", listened=100, started_at=1_700_000_000)
    )
    await apply_devices(services, alice.uuid, device_upload("phone", listened=250))

    stored = await load(services, DeviceStats, alice.uuid, "phone")
    assert (stored.time_listened, stored.times_started_at, stored.device_type) == (
        250,
        1_700_000_000,
        2,
    )


async def test_device_update_without_a_type_keeps_the_stored_type(services, alice):
    await apply_devices(services, alice.uuid, device_upload("phone", listened=1))
    update = pb.SyncUserDevice(
        device_id=StringValue(value="phone"), time_listened=Int64Value(value=9)
    )
    async with services.sessionmaker() as session:
        row = await session.get(DeviceStats, (alice.uuid, "phone"))
        row.device_type = 1
        await session.commit()

    async with services.sessionmaker() as session:
        await apply_device_records(session, alice.uuid, [update])
        await session.commit()

    stored = await load(services, DeviceStats, alice.uuid, "phone")
    assert (stored.device_type, stored.time_listened) == (1, 9)


async def test_device_stats_hold_the_largest_totals(services, alice):
    await apply_devices(services, alice.uuid, device_upload("phone", listened=INT64_MAX))

    assert (await load(services, DeviceStats, alice.uuid, "phone")).time_listened == INT64_MAX


async def test_negative_device_stats_are_dropped(services, alice):
    await apply_devices(services, alice.uuid, device_upload("phone", listened=100, skipping=40))

    await apply_devices(
        services, alice.uuid, device_upload("phone", listened=-1, skipping=50, started_at=-7)
    )

    stored = await load(services, DeviceStats, alice.uuid, "phone")
    assert (stored.time_listened, stored.time_skipping, stored.times_started_at) == (100, 50, None)


async def test_device_records_without_an_id_or_any_field_create_nothing(services, alice):
    anonymous = pb.SyncUserDevice(time_listened=Int64Value(value=5))
    bare = pb.SyncUserDevice(device_id=StringValue(value="phone"))
    negative = pb.SyncUserDevice(
        device_id=StringValue(value="tablet"), time_listened=Int64Value(value=-5)
    )

    async with services.sessionmaker() as session:
        await apply_device_records(session, alice.uuid, [anonymous, bare, negative])
        await session.commit()

    assert await load(services, DeviceStats, alice.uuid, "phone") is None
    assert await load(services, DeviceStats, alice.uuid, "tablet") is None


async def test_one_users_device_upload_leaves_another_users_device_alone(services, alice, bob):
    await apply_devices(services, alice.uuid, device_upload("phone", listened=100))

    await apply_devices(services, bob.uuid, device_upload("phone", listened=7))

    assert (await load(services, DeviceStats, alice.uuid, "phone")).time_listened == 100
    assert (await load(services, DeviceStats, bob.uuid, "phone")).time_listened == 7


async def test_two_records_for_one_new_device_in_a_batch_merge(services, alice):
    async with services.sessionmaker() as session:
        await apply_device_records(
            session,
            alice.uuid,
            [
                unwrap(device_upload("phone", listened=100)),
                unwrap(device_upload("phone", listened=300)),
            ],
        )
        await session.commit()

    assert (await load(services, DeviceStats, alice.uuid, "phone")).time_listened == 300
