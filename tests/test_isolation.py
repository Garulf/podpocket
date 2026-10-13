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
    add_e1 = {"action": 3, "modified": 1000, "uuid": "e1", "podcast": "p1"}
    queued = await client.post(
        "/api/up_next/sync",
        json={
            "deviceTime": 1,
            "version": "2",
            "upNext": {"serverModified": 0, "changes": [add_e1]},
        },
        headers=alice_auth,
    )
    assert queued.json()["episodes"][0]["uuid"] == "e1"
    listen = {"action": 1, "modifiedAt": "1000", "episode": "e1", "podcast": "p1"}
    listened = await client.post(
        "/api/history/sync",
        json={"changes": [listen], "deviceTime": 1, "serverModified": 0, "version": 1},
        headers=alice_auth,
    )
    assert listened.status_code == 200
    saved = await client.post(
        "/api/user/named_settings/update",
        json={"m": "Android", "v": 1, "settings": {"skipForward": 45}},
        headers=alice_auth,
    )
    assert saved.status_code == 200


async def test_another_user_sees_none_of_alices_data(client, alice_auth, bob_auth):
    await fill_alice(client, alice_auth)

    assert list((await sync_update(client, alice_auth)).records)
    assert list((await sync_update(client, bob_auth)).records) == []

    podcasts = await post_proto(
        client,
        "/api/user/podcast/list",
        pb.UserPodcastListRequest(**LIST_REQUEST),
        pb.UserPodcastListResponse,
        bob_auth,
    )
    assert (list(podcasts.podcasts), list(podcasts.folders)) == ([], [])

    episodes = await client.post(
        "/api/user/podcast/episodes", json={"uuid": "p1"}, headers=bob_auth
    )
    assert episodes.json() == {"subscribed": False, "episodes": []}

    playlists = await post_proto(
        client,
        "/api/user/playlist/list",
        pb.UserPlaylistListRequest(**LIST_REQUEST),
        pb.UserPlaylistListResponse,
        bob_auth,
    )
    assert list(playlists.playlists) == []

    bookmarks = await post_proto(
        client, "/api/user/bookmark/list", pb.BookmarkRequest(), pb.BookmarksResponse, bob_auth
    )
    assert list(bookmarks.bookmarks) == []

    starred = await client.post("/api/starred/list", content=b"", headers={**bob_auth, **PROTOBUF})
    assert starred.content == b""

    up_next = await client.post(
        "/api/up_next/sync",
        json={"deviceTime": 1, "version": "2", "upNext": {"serverModified": 0, "changes": []}},
        headers=bob_auth,
    )
    assert up_next.json() == {"serverModified": 0, "episodes": []}

    history = await client.post(
        "/api/history/sync",
        json={"changes": [], "deviceTime": 1, "serverModified": 0, "version": 1},
        headers=bob_auth,
    )
    assert (history.json()["changes"], history.json()["lastCleared"]) == ([], 0)

    year = await client.post(
        "/api/history/year", json={"version": "1", "count": True, "year": 1970}, headers=bob_auth
    )
    assert year.json() == {"count": 0}

    settings = await client.post(
        "/api/user/named_settings/update",
        json={"m": "Android", "v": 1, "settings": {}},
        headers=bob_auth,
    )
    assert settings.json() == {}

    stats = await client.post(
        "/api/user/stats/summary", json={"deviceId": "bob-phone", "deviceType": 2}, headers=bob_auth
    )
    assert stats.json()["timeListened"] == "0"


async def test_the_same_ids_from_another_user_leave_the_original_alone(
    client, alice_auth, bob_auth
):
    await sync_update(
        client,
        alice_auth,
        podcast_upload("p1"),
        episode_upload("e1", starred=(True, 1000)),
        folder_upload("f1", name="Alice"),
        bookmark_upload("b1", title="Alice"),
    )
    await sync_update(
        client,
        bob_auth,
        podcast_upload("p1", subscribed=False),
        episode_upload("e1", starred=(False, 9000)),
        folder_upload("f1", name="Bob"),
        bookmark_upload("b1", title="Bob", title_modified=9000),
    )

    alice_view = await sync_update(client, alice_auth)

    assert records_of(alice_view, "podcast")[0].subscribed.value is True
    assert records_of(alice_view, "episode")[0].starred.value is True
    assert records_of(alice_view, "folder")[0].name == "Alice"
    assert records_of(alice_view, "bookmark")[0].title.value == "Alice"


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
