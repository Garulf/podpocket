from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.wrappers_pb2 import BoolValue
from sqlalchemy import select

from podpocket.models import ChangeLog, Folder, Subscription
from podpocket.pcapi.records import podcast_change, podcast_list_entry, podcast_record
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import to_timestamp
from podpocket.sync.folders import apply_folder_records, folder_record, podcast_folder
from podpocket.sync.podcasts import HOME_FOLDER, PodcastChange, apply_podcast_changes
from podpocket.timefmt import from_epoch_ms
from tests.records import CREATED, folder_upload, merge, unwrap

NOW = 1_791_799_200_000


async def load(services, model, *key):
    async with services.sessionmaker() as session:
        return await session.get(model, key)


async def test_subscribing_stores_settings_and_a_date_added(services, alice):
    change = PodcastChange(
        "p1", subscribed=True, folder_uuid="f1", episodes_sort_order=2, auto_start_from=30
    )

    changed = await merge(services, alice.uuid, apply_podcast_changes, [change], now=NOW)

    assert changed == {"p1"}
    stored = await load(services, Subscription, alice.uuid, "p1")
    assert (stored.subscribed, stored.folder_uuid, stored.episodes_sort_order) == (True, "f1", 2)
    assert stored.auto_start_from == 30
    assert stored.date_added == from_epoch_ms(NOW)


async def test_home_folder_clears_the_folder(services, alice):
    await merge(services, alice.uuid, apply_podcast_changes, [PodcastChange("p1", True, "f1")])

    await merge(
        services, alice.uuid, apply_podcast_changes, [PodcastChange("p1", folder_uuid=HOME_FOLDER)]
    )

    assert (await load(services, Subscription, alice.uuid, "p1")).folder_uuid is None


async def test_an_unchanged_upload_records_nothing(services, alice):
    change = PodcastChange("p1", subscribed=True, date_added=CREATED)
    await merge(services, alice.uuid, apply_podcast_changes, [change])

    assert await merge(services, alice.uuid, apply_podcast_changes, [change]) == set()


async def test_unsubscribing_is_logged(services, alice):
    await merge(services, alice.uuid, apply_podcast_changes, [PodcastChange("p1", subscribed=True)])

    changed = await merge(
        services, alice.uuid, apply_podcast_changes, [PodcastChange("p1", subscribed=False)]
    )

    assert changed == {"p1"}
    assert (await load(services, Subscription, alice.uuid, "p1")).subscribed is False
    async with services.sessionmaker() as session:
        fields = (await session.scalars(select(ChangeLog.field))).all()
    assert fields.count("subscribed") == 2


def test_podcast_change_falls_back_to_is_deleted():
    assert (
        podcast_change(pb.SyncUserPodcast(uuid="p1", is_deleted=BoolValue(value=True))).subscribed
        is False
    )
    assert podcast_change(pb.SyncUserPodcast(uuid="p1")).subscribed is None


def test_podcast_record_always_states_subscription_and_folder():
    record = podcast_record(Subscription(user_uuid="u", podcast_uuid="p1", subscribed=False))

    assert record.HasField("subscribed") and record.subscribed.value is False
    assert record.is_deleted.value is True
    assert record.folder_uuid.value == HOME_FOLDER
    assert record.episodes_sort_order.value == 3
    assert not record.HasField("date_added")


def test_podcast_list_entry_defaults_sort_order_to_newest_first():
    entry = podcast_list_entry(
        Subscription(
            user_uuid="u",
            podcast_uuid="p1",
            subscribed=True,
            folder_uuid="f1",
            sort_position=4,
            date_added=CREATED,
        )
    )

    assert (entry.uuid, entry.episodes_sort_order, entry.auto_start_from) == ("p1", 3, 0)
    assert (entry.folder_uuid.value, entry.sort_position.value) == ("f1", 4)
    assert entry.date_added == to_timestamp(CREATED)


async def test_folder_upload_creates_and_updates(services, alice):
    await merge(
        services, alice.uuid, apply_folder_records, [unwrap(folder_upload("f1", name="News"))]
    )

    changed = await merge(
        services, alice.uuid, apply_folder_records, [unwrap(folder_upload("f1", name="Daily"))]
    )

    assert changed == {"f1"}
    stored = await load(services, Folder, alice.uuid, "f1")
    assert (stored.name, stored.color, stored.podcasts_sort_type) == ("Daily", 2, 3)
    assert stored.date_added == CREATED


async def test_folder_without_date_added_gets_one(services, alice):
    upload = unwrap(folder_upload("f1", date_added=None))

    await merge(services, alice.uuid, apply_folder_records, [upload], now=NOW)

    assert (await load(services, Folder, alice.uuid, "f1")).date_added == from_epoch_ms(NOW)


async def test_folder_with_an_impossible_date_added_gets_one(services, alice):
    upload = unwrap(folder_upload("f1", date_added=None))
    upload.date_added.CopyFrom(Timestamp(seconds=2**62))

    changed = await merge(services, alice.uuid, apply_folder_records, [upload], now=NOW)

    assert changed == {"f1"}
    assert (await load(services, Folder, alice.uuid, "f1")).date_added == from_epoch_ms(NOW)


async def test_deleted_folder_is_kept_as_a_tombstone(services, alice):
    await merge(services, alice.uuid, apply_folder_records, [unwrap(folder_upload("f1"))])

    await merge(
        services, alice.uuid, apply_folder_records, [unwrap(folder_upload("f1", deleted=True))]
    )

    record = folder_record(await load(services, Folder, alice.uuid, "f1"))
    assert record.is_deleted is True
    assert record.HasField("date_added")


def test_podcast_folder_entry_has_date_added():
    folder = Folder(
        user_uuid="u",
        folder_uuid="f1",
        name="News",
        color=1,
        sort_position=0,
        podcasts_sort_type=0,
        date_added=CREATED,
        is_deleted=False,
    )

    entry = podcast_folder(folder)

    assert (entry.folder_uuid, entry.name) == ("f1", "News")
    assert entry.date_added == to_timestamp(CREATED)
