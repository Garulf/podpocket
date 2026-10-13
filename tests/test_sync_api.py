import asyncio
from datetime import datetime

from google.protobuf.timestamp_pb2 import Timestamp
from google.protobuf.wrappers_pb2 import Int32Value

from podpocket.models import DeviceStats
from podpocket.pcapi import sync as sync_api
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.sync.podcasts import HOME_FOLDER
from podpocket.sync.writer import ChangeWriter
from podpocket.timefmt import epoch_ms
from tests.api import PROTOBUF
from tests.records import (
    SYNC_UPDATE,
    bookmark_upload,
    device_upload,
    episode_upload,
    folder_upload,
    playlist_upload,
    podcast_upload,
    records_of,
    sync_update,
)


async def test_uploaded_podcast_is_echoed_with_subscribed(client, alice_auth):
    response = await sync_update(client, alice_auth, podcast_upload("p1"))

    [podcast] = records_of(response, "podcast")
    assert podcast.uuid == "p1"
    assert podcast.HasField("subscribed") and podcast.subscribed.value is True
    assert podcast.folder_uuid.value == HOME_FOLDER
    assert response.last_modified > 0


async def test_unsubscribe_is_echoed_with_an_explicit_false(client, alice_auth):
    first = await sync_update(client, alice_auth, podcast_upload("p1"))

    response = await sync_update(
        client,
        alice_auth,
        podcast_upload("p1", subscribed=False),
        last_modified=first.last_modified,
    )

    [podcast] = records_of(response, "podcast")
    assert podcast.HasField("subscribed") and podcast.subscribed.value is False


async def test_other_devices_receive_changes_after_their_cursor(client, alice_auth):
    phone = await sync_update(client, alice_auth)
    await sync_update(client, alice_auth, episode_upload("e1", starred=(True, 5000)))

    caught_up = await sync_update(client, alice_auth, last_modified=phone.last_modified)
    again = await sync_update(client, alice_auth, last_modified=caught_up.last_modified)

    [episode] = records_of(caught_up, "episode")
    assert (episode.uuid, episode.starred.value, episode.starred_modified.value) == (
        "e1",
        True,
        5000,
    )
    assert list(again.records) == []
    assert again.last_modified == caught_up.last_modified


async def test_losing_upload_is_echoed_with_the_server_value(client, alice_auth):
    first = await sync_update(client, alice_auth, episode_upload("e1", played_up_to=(300, 2000)))

    stale = await sync_update(
        client,
        alice_auth,
        episode_upload("e1", played_up_to=(100, 1000)),
        last_modified=first.last_modified,
    )

    [episode] = records_of(stale, "episode")
    assert (episode.played_up_to.value, episode.played_up_to_modified.value) == (300, 2000)
    assert stale.last_modified == first.last_modified


async def test_episode_echo_keeps_deselected_chapters(client, alice_auth):
    first = await sync_update(
        client, alice_auth, episode_upload("e1", deselected_chapters=("2,5", 1000))
    )

    response = await sync_update(
        client,
        alice_auth,
        episode_upload("e1", starred=(True, 2000)),
        last_modified=first.last_modified,
    )

    [episode] = records_of(response, "episode")
    assert episode.deselected_chapters == "2,5"
    assert episode.starred.value is True


async def test_folder_echo_has_date_added_even_if_the_client_omitted_it(client, alice_auth):
    response = await sync_update(client, alice_auth, folder_upload("f1", date_added=None))

    [folder] = records_of(response, "folder")
    assert folder.HasField("date_added")
    assert folder.date_added.seconds > 0


async def test_playlist_echo_is_keyed_by_original_uuid(client, alice_auth):
    response = await sync_update(client, alice_auth, playlist_upload("MiXeD-Case"))

    [playlist] = records_of(response, "playlist")
    assert (playlist.uuid, playlist.original_uuid) == ("MiXeD-Case", "MiXeD-Case")


async def test_bookmark_is_echoed(client, alice_auth):
    response = await sync_update(client, alice_auth, bookmark_upload("b1", title="Intro"))

    [bookmark] = records_of(response, "bookmark")
    assert (bookmark.title.value, bookmark.time.value) == ("Intro", 60)


async def test_subscription_to_an_unknown_podcast_is_kept(client, alice_auth):
    await sync_update(client, alice_auth, podcast_upload("not-in-the-catalog"))

    everything = await sync_update(client, alice_auth)

    assert [podcast.uuid for podcast in records_of(everything, "podcast")] == ["not-in-the-catalog"]


async def test_device_stats_are_stored_but_not_echoed(client, services, alice, alice_auth):
    response = await sync_update(client, alice_auth, device_upload("phone", listened=60))

    assert records_of(response, "device") == []
    async with services.sessionmaker() as session:
        assert (await session.get(DeviceStats, (alice.uuid, "phone"))).time_listened == 60


async def test_new_account_gets_a_nonzero_cursor_and_no_records(client, alice_auth):
    response = await sync_update(client, alice_auth)

    assert response.last_modified > 0
    assert list(response.records) == []


async def test_cursor_moves_only_when_something_changes(client, alice_auth):
    first = await sync_update(client, alice_auth)
    second = await sync_update(client, alice_auth, last_modified=first.last_modified)
    third = await sync_update(
        client, alice_auth, podcast_upload("p1"), last_modified=second.last_modified
    )

    assert first.last_modified == second.last_modified < third.last_modified


async def test_corrupt_body_is_rejected(client, alice_auth):
    response = await client.post(
        SYNC_UPDATE, content=b"\xff\xff", headers={**alice_auth, **PROTOBUF}
    )

    assert response.status_code == 400


async def test_sync_needs_a_token(client):
    response = await client.post(SYNC_UPDATE, content=b"", headers=PROTOBUF)

    assert response.status_code == 401


async def test_last_sync_at_is_the_incremental_cursor(client, alice_auth):
    synced = await sync_update(client, alice_auth, podcast_upload("p1"))

    response = await client.post(
        "/api/user/last_sync_at", json={"m": "mobile", "v": 2}, headers=alice_auth
    )

    last_sync_at = response.json()["lastSyncAt"]
    assert last_sync_at.endswith("Z")
    assert epoch_ms(datetime.fromisoformat(last_sync_at)) == synced.last_modified

    await sync_update(client, alice_auth, episode_upload("e1", starred=(True, 1)))
    incremental = await sync_update(client, alice_auth, last_modified=synced.last_modified)

    assert [episode.uuid for episode in records_of(incremental, "episode")] == ["e1"]
    assert records_of(incremental, "podcast") == []


async def test_duplicate_uploads_in_one_request_do_not_collide(client, alice_auth):
    response = await sync_update(
        client,
        alice_auth,
        podcast_upload("p1"),
        podcast_upload("p1", subscribed=False),
        episode_upload("e1", starred=(True, 1000)),
        episode_upload("e1", starred=(False, 2000)),
        folder_upload("f1"),
        folder_upload("f1", name="Renamed"),
        playlist_upload("pl1"),
        playlist_upload("pl1", title="Again"),
        bookmark_upload("b1"),
        bookmark_upload("b1", title="Again", title_modified=2000),
    )

    assert [podcast.uuid for podcast in records_of(response, "podcast")] == ["p1"]
    assert [episode.uuid for episode in records_of(response, "episode")] == ["e1"]
    assert [folder.folder_uuid for folder in records_of(response, "folder")] == ["f1"]
    assert [playlist.uuid for playlist in records_of(response, "playlist")] == ["pl1"]
    assert [bookmark.bookmark_uuid for bookmark in records_of(response, "bookmark")] == ["b1"]


async def test_records_with_odd_values_never_fail_the_sync(client, alice_auth):
    huge = 2**62
    response = await sync_update(
        client,
        alice_auth,
        pb.Record(),
        pb.Record(podcast=pb.SyncUserPodcast()),
        pb.Record(episode=pb.SyncUserEpisode()),
        pb.Record(folder=pb.SyncUserFolder()),
        pb.Record(playlist=pb.SyncUserPlaylist()),
        pb.Record(bookmark=pb.SyncUserBookmark()),
        pb.Record(device=pb.SyncUserDevice()),
        pb.Record(
            podcast=pb.SyncUserPodcast(
                uuid="odd",
                sort_position=Int32Value(value=-(2**31)),
                date_added=Timestamp(seconds=huge),
            )
        ),
        episode_upload("odd", played_up_to=(huge, huge), duration=(-huge, -huge)),
        episode_upload("odd2", playing_status=(7, -1), starred=(True, huge)),
        pb.Record(
            bookmark=pb.SyncUserBookmark(
                bookmark_uuid="odd", time=Int32Value(value=-5), created_at=Timestamp(seconds=huge)
            )
        ),
        pb.Record(folder=pb.SyncUserFolder(folder_uuid="odd", date_added=Timestamp(seconds=huge))),
        last_modified=-huge,
    )

    assert response.last_modified > 0
    again = await sync_update(client, alice_auth, last_modified=2**63 - 1)
    assert list(again.records) == []


async def test_cursor_never_skips_a_concurrent_change(client, alice_auth):
    uploads = [episode_upload(f"e{n}", starred=(True, n + 1)) for n in range(8)]

    responses = await asyncio.gather(
        *(sync_update(client, alice_auth, upload) for upload in uploads)
    )

    for response in responses:
        later = await sync_update(client, alice_auth, last_modified=response.last_modified)
        seen = {e.uuid for e in records_of(response, "episode")}
        seen |= {e.uuid for e in records_of(later, "episode")}
        assert seen == {f"e{n}" for n in range(8)}


async def test_changes_are_read_after_the_user_is_locked(client, alice_auth, monkeypatch):
    calls: list[str] = []
    real_begin, real_changed = ChangeWriter.begin, sync_api.changed_since

    async def begin(*args, **kwargs):
        calls.append("lock")
        return await real_begin(*args, **kwargs)

    async def changed_since(*args, **kwargs):
        calls.append("read")
        return await real_changed(*args, **kwargs)

    monkeypatch.setattr(ChangeWriter, "begin", begin)
    monkeypatch.setattr(sync_api, "changed_since", changed_since)

    await sync_update(client, alice_auth)

    assert calls == ["lock", "read"]
