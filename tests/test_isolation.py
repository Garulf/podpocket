import pytest

from podpocket.catalog import list_episodes
from podpocket.protocol import sync_api_pb2 as pb
from tests.api import PROTOBUF, post_proto
from tests.factories import item, rss
from tests.records import (
    bookmark_upload,
    device_upload,
    episode_upload,
    folder_upload,
    playlist_upload,
    podcast_upload,
    records_of,
    sync_update,
)

FEED = "https://feeds.test/show.xml"
LIST_REQUEST = {"v": "2", "m": "mobile"}


async def up_next(client, auth, *episode_uuids):
    changes = [
        {"action": 3, "modified": 1000 + index, "uuid": uuid, "podcast": "p1"}
        for index, uuid in enumerate(episode_uuids)
    ]
    response = await client.post(
        "/api/up_next/sync",
        json={
            "deviceTime": 1,
            "version": "2",
            "upNext": {"serverModified": 0, "changes": changes},
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def history(client, auth, *listens):
    changes = [
        {"action": 1, "modifiedAt": str(modified), "episode": "e1", "podcast": "p1"}
        for modified in listens
    ]
    response = await client.post(
        "/api/history/sync",
        json={"changes": changes, "deviceTime": 1, "serverModified": 0, "version": 1},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()["changes"]


async def named_settings(client, auth, **settings):
    response = await client.post(
        "/api/user/named_settings/update",
        json={"m": "Android", "v": 1, "settings": settings},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def fill_alice(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        podcast_upload("p1"),
        episode_upload("e1", starred=(True, 1000)),
        folder_upload("f1"),
        playlist_upload("pl1"),
        bookmark_upload("b1"),
        device_upload("alice-phone", listened=3600),
    )
    await up_next(client, alice_auth, "e1")
    await history(client, alice_auth, 1000)
    await named_settings(client, alice_auth, skipForward=45)


@pytest.fixture
async def filled_alice(client, alice_auth, bob_auth):
    await fill_alice(client, alice_auth)


async def read_sync_update(client, auth):
    response = await sync_update(client, auth)
    return [record.WhichOneof("record") for record in response.records]


async def read_podcast_list(client, auth):
    response = await post_proto(
        client,
        "/api/user/podcast/list",
        pb.UserPodcastListRequest(**LIST_REQUEST),
        pb.UserPodcastListResponse,
        auth,
    )
    return len(response.podcasts), len(response.folders)


async def read_podcast_episodes(client, auth):
    response = await client.post("/api/user/podcast/episodes", json={"uuid": "p1"}, headers=auth)
    return [episode["uuid"] for episode in response.json()["episodes"]]


async def read_playlists(client, auth):
    response = await post_proto(
        client,
        "/api/user/playlist/list",
        pb.UserPlaylistListRequest(**LIST_REQUEST),
        pb.UserPlaylistListResponse,
        auth,
    )
    return [playlist.uuid for playlist in response.playlists]


async def read_bookmarks(client, auth):
    response = await post_proto(
        client, "/api/user/bookmark/list", pb.BookmarkRequest(), pb.BookmarksResponse, auth
    )
    return [bookmark.bookmark_uuid for bookmark in response.bookmarks]


async def read_starred(client, auth):
    response = await client.post("/api/starred/list", content=b"", headers={**auth, **PROTOBUF})
    return pb.StarredEpisodesResponse.FromString(response.content).episodes[:]


async def read_up_next(client, auth):
    return [entry["uuid"] for entry in (await up_next(client, auth))["episodes"]]


async def read_history(client, auth):
    return await history(client, auth)


async def read_history_year(client, auth):
    response = await client.post(
        "/api/history/year", json={"version": "1", "count": True, "year": 1970}, headers=auth
    )
    return response.json()["count"]


async def read_named_settings(client, auth):
    return await named_settings(client, auth)


async def read_stats_of_other_devices(client, auth):
    response = await client.post(
        "/api/user/stats/summary", json={"deviceId": "reader", "deviceType": 2}, headers=auth
    )
    return response.json()["timeListened"]


READERS = {
    "sync_update": (read_sync_update, []),
    "podcast_list": (read_podcast_list, (0, 0)),
    "podcast_episodes": (read_podcast_episodes, []),
    "playlist_list": (read_playlists, []),
    "bookmark_list": (read_bookmarks, []),
    "starred_list": (read_starred, []),
    "up_next": (read_up_next, []),
    "history_sync": (read_history, []),
    "history_year": (read_history_year, 0),
    "named_settings": (read_named_settings, {}),
    "stats_summary": (read_stats_of_other_devices, "0"),
}


@pytest.mark.parametrize("reader, nothing", READERS.values(), ids=READERS.keys())
async def test_another_user_sees_none_of_alices_data(
    client, filled_alice, alice_auth, bob_auth, reader, nothing
):
    assert await reader(client, alice_auth) != nothing

    assert await reader(client, bob_auth) == nothing


async def test_the_same_ids_from_another_user_leave_the_original_alone(
    client, alice_auth, bob_auth
):
    await sync_update(
        client,
        alice_auth,
        podcast_upload("p1"),
        episode_upload("e1", starred=(True, 1000)),
        folder_upload("f1", name="Alice"),
        playlist_upload("pl1", title="Alice"),
        bookmark_upload("b1", title="Alice"),
    )
    await up_next(client, alice_auth, "e1")
    await history(client, alice_auth, 1000)
    await named_settings(client, alice_auth, skipForward=45)

    await sync_update(
        client,
        bob_auth,
        podcast_upload("p1", subscribed=False),
        episode_upload("e1", starred=(False, 9000)),
        folder_upload("f1", name="Bob"),
        playlist_upload("pl1", title="Bob"),
        bookmark_upload("b1", title="Bob", title_modified=9000),
    )
    await up_next(client, bob_auth, "e1", "e2")
    await history(client, bob_auth, 9000)
    await named_settings(client, bob_auth, skipForward=90)

    alice_view = await sync_update(client, alice_auth)
    bob_view = await sync_update(client, bob_auth)

    assert records_of(alice_view, "podcast")[0].subscribed.value is True
    assert records_of(alice_view, "episode")[0].starred.value is True
    assert records_of(alice_view, "folder")[0].name == "Alice"
    assert records_of(alice_view, "playlist")[0].title.value == "Alice"
    assert records_of(alice_view, "bookmark")[0].title.value == "Alice"
    assert [entry["uuid"] for entry in (await up_next(client, alice_auth))["episodes"]] == ["e1"]
    assert [change["modifiedAt"] for change in await history(client, alice_auth)] == ["1000"]
    assert (await named_settings(client, alice_auth))["skipForward"]["value"] == 45

    assert records_of(bob_view, "podcast")[0].subscribed.value is False
    assert records_of(bob_view, "episode")[0].starred.value is False
    assert records_of(bob_view, "folder")[0].name == "Bob"
    assert records_of(bob_view, "playlist")[0].title.value == "Bob"
    assert records_of(bob_view, "bookmark")[0].title.value == "Bob"
    assert sorted(entry["uuid"] for entry in (await up_next(client, bob_auth))["episodes"]) == [
        "e1",
        "e2",
    ]
    assert [change["modifiedAt"] for change in await history(client, bob_auth)] == ["9000"]
    assert (await named_settings(client, bob_auth))["skipForward"]["value"] == 90


async def test_episode_progress_from_another_user_leaves_the_original_alone(
    client, alice_auth, bob_auth
):
    await sync_update(
        client, alice_auth, episode_upload("e1", played_up_to=(99, 1000), duration=(600, 1000))
    )
    progress = {"uuid": "e1", "podcast": "p1", "position": 500, "duration": 600, "status": 2}

    response = await client.post("/api/sync/update_episode", json=progress, headers=bob_auth)

    assert response.status_code == 204
    [alice_episode] = records_of(await sync_update(client, alice_auth), "episode")
    [bob_episode] = records_of(await sync_update(client, bob_auth), "episode")
    assert alice_episode.played_up_to.value == 99
    assert bob_episode.played_up_to.value == 500


async def test_the_sync_cursor_belongs_to_each_user(client, alice_auth, bob_auth):
    async def last_sync_at(auth):
        response = await client.post(
            "/api/user/last_sync_at", json={"m": "mobile", "v": 2}, headers=auth
        )
        return response.json()["lastSyncAt"]

    bob_before = await last_sync_at(bob_auth)
    alice_before = await last_sync_at(alice_auth)

    await sync_update(client, alice_auth, podcast_upload("p1"))

    assert await last_sync_at(bob_auth) == bob_before
    assert await last_sync_at(alice_auth) > alice_before


async def test_catalog_episodes_carry_only_the_callers_state(
    client, services, respx_mock, alice_auth, bob_auth
):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    podcast_uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        [episode] = await list_episodes(session, podcast_uuid)
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode.uuid, podcast_uuid, starred=(True, 1000), played_up_to=(99, 1000)),
    )
    request = pb.PodcastsEpisodesRequest(podcast_uuids=[podcast_uuid], episode_uuids=[episode.uuid])

    response = await post_proto(
        client, "/api/user/episodes", request, pb.EpisodesResponse, bob_auth
    )

    [seen] = response.episodes
    assert (seen.starred, seen.played_up_to) == (False, 0)
