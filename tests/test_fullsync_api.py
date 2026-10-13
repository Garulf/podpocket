import pytest

from podpocket.catalog import list_episodes
from podpocket.protocol import sync_api_pb2 as pb
from tests.api import PROTOBUF, post_proto
from tests.factories import item, rss
from tests.records import (
    bookmark_upload,
    episode_upload,
    folder_upload,
    playlist_episode,
    playlist_upload,
    podcast_upload,
    sync_update,
)

FEED = "https://feeds.test/show.xml"
INT32_MAX = 2**31 - 1
LIST_REQUEST = {"v": "2", "m": "mobile"}
FULL_SYNC_PATHS = [
    "/api/user/podcast/list",
    "/api/user/podcast/episodes",
    "/api/user/episodes",
    "/api/user/playlist/list",
    "/api/user/bookmark/list",
    "/api/starred/list",
]


async def test_podcast_list_has_subscribed_podcasts_and_live_folders(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        podcast_upload("p1", folder="f1", sort_order=None),
        podcast_upload("p2", subscribed=False),
        podcast_upload("not-in-the-catalog"),
        folder_upload("f1", name="News"),
        folder_upload("f2", deleted=True),
    )

    response = await post_proto(
        client,
        "/api/user/podcast/list",
        pb.UserPodcastListRequest(**LIST_REQUEST),
        pb.UserPodcastListResponse,
        alice_auth,
    )

    assert [podcast.uuid for podcast in response.podcasts] == ["not-in-the-catalog", "p1"]
    first_party = response.podcasts[1]
    assert (first_party.folder_uuid.value, first_party.episodes_sort_order) == ("f1", 3)
    assert first_party.HasField("date_added")
    assert [(folder.folder_uuid, folder.name) for folder in response.folders] == [("f1", "News")]
    assert response.folders[0].HasField("date_added")


async def test_podcast_episodes_returns_only_known_fields(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        podcast_upload("p1"),
        episode_upload("e1", "p1", played_up_to=(95, 1000), starred=(True, 1000)),
        episode_upload("e2", "p1", is_deleted=(True, 1000), deselected_chapters=("1", 1000)),
        episode_upload("x1", "other", starred=(True, 1000)),
    )

    response = await client.post(
        "/api/user/podcast/episodes", json={"uuid": "p1"}, headers=alice_auth
    )

    body = response.json()
    assert (body["subscribed"], body["episodesSortOrder"], body["autoStartFrom"]) == (True, 3, 0)
    assert body["episodes"] == [
        {"uuid": "e1", "playedUpTo": 95, "starred": True},
        {"uuid": "e2", "isDeleted": True, "deselectedChapters": "1"},
    ]


async def test_podcast_episodes_for_an_unknown_podcast_is_empty(client, alice_auth):
    response = await client.post(
        "/api/user/podcast/episodes", json={"uuid": "nope"}, headers=alice_auth
    )

    assert response.json() == {"subscribed": False, "episodes": []}


@pytest.fixture
async def show(respx_mock, services):
    respx_mock.get(FEED).respond(200, content=rss(item(guid="ep-1", title="First"), title="Show"))
    podcast_uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        [episode] = await list_episodes(session, podcast_uuid)
    return podcast_uuid, episode.uuid


async def test_episodes_returns_catalog_metadata_with_user_state(client, alice_auth, show):
    podcast_uuid, episode_uuid = show
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode_uuid, podcast_uuid, played_up_to=(42, 1000), starred=(True, 1000)),
    )
    request = pb.PodcastsEpisodesRequest(
        podcast_uuids=[podcast_uuid, podcast_uuid], episode_uuids=[episode_uuid, "missing"]
    )

    response = await post_proto(
        client, "/api/user/episodes", request, pb.EpisodesResponse, alice_auth
    )

    assert response.total == 1
    [episode] = response.episodes
    assert (episode.uuid, episode.podcast_uuid) == (episode_uuid, podcast_uuid)
    assert (episode.title, episode.podcast_title) == ("First", "Show")
    assert episode.url == "https://cdn.example.com/1.mp3"
    assert (episode.played_up_to, episode.starred, episode.playing_status) == (42, True, 1)
    assert episode.published.seconds > 0


async def test_episodes_clamp_values_that_do_not_fit_the_wire(
    client, services, respx_mock, alice_auth
):
    huge = (
        "<itunes:duration>99999999999</itunes:duration><itunes:season>9999999999999</itunes:season>"
    )
    respx_mock.get(FEED).respond(200, content=rss(item(guid="ep-1", extra=huge)))
    podcast_uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        [episode] = await list_episodes(session, podcast_uuid)
    await sync_update(
        client, alice_auth, episode_upload(episode.uuid, podcast_uuid, played_up_to=(2**40, 1000))
    )
    request = pb.PodcastsEpisodesRequest(podcast_uuids=[podcast_uuid], episode_uuids=[episode.uuid])

    response = await post_proto(
        client, "/api/user/episodes", request, pb.EpisodesResponse, alice_auth
    )

    [found] = response.episodes
    assert (found.duration, found.episode_season, found.played_up_to) == (INT32_MAX,) * 3


async def test_playlist_list_returns_protobuf_playlists(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        playlist_upload("pl1", title="Smart"),
        playlist_upload("m1", manual=True, episode_order=["e1"], episodes=[playlist_episode("e1")]),
    )

    response = await post_proto(
        client,
        "/api/user/playlist/list",
        pb.UserPlaylistListRequest(**LIST_REQUEST),
        pb.UserPlaylistListResponse,
        alice_auth,
    )

    by_uuid = {playlist.original_uuid: playlist for playlist in response.playlists}
    assert by_uuid["pl1"].title == "Smart"
    assert [episode.episode for episode in by_uuid["m1"].episodes] == ["e1"]


async def test_playlist_list_json_variant_is_empty(client, alice_auth):
    response = await client.post(
        "/api/user/playlist/list", json={"m": "mobile", "v": 2}, headers=alice_auth
    )

    assert response.json() == {"playlists": []}


async def test_bookmark_list_skips_deleted_bookmarks(client, alice_auth):
    await sync_update(
        client, alice_auth, bookmark_upload("b1", title="Keep"), bookmark_upload("b2", deleted=True)
    )

    response = await post_proto(
        client, "/api/user/bookmark/list", pb.BookmarkRequest(), pb.BookmarksResponse, alice_auth
    )

    assert [(b.bookmark_uuid, b.title, b.time) for b in response.bookmarks] == [("b1", "Keep", 60)]
    assert response.bookmarks[0].createdAt.seconds > 0


async def test_starred_list_clamps_values_that_do_not_fit_the_wire(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        episode_upload(
            "e1", starred=(True, 7000), duration=(2**40, 7000), played_up_to=(2**40, 7000)
        ),
    )

    response = await client.post(
        "/api/starred/list", content=b"", headers={**alice_auth, **PROTOBUF}
    )

    [episode] = pb.StarredEpisodesResponse.FromString(response.content).episodes
    assert (episode.duration, episode.played_up_to) == (INT32_MAX, INT32_MAX)


async def test_starred_list_accepts_an_empty_body(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        episode_upload("e1", starred=(True, 7000)),
        episode_upload("e2", starred=(False, 7000)),
    )

    response = await client.post(
        "/api/starred/list", content=b"", headers={**alice_auth, **PROTOBUF}
    )

    starred = pb.StarredEpisodesResponse.FromString(response.content)
    assert [(e.uuid, e.podcast_uuid, e.starred_modified) for e in starred.episodes] == [
        ("e1", "p1", 7000)
    ]


@pytest.mark.parametrize("path", FULL_SYNC_PATHS)
async def test_full_sync_lists_need_a_token(client, path):
    assert (await client.post(path, content=b"", headers=PROTOBUF)).status_code == 401
