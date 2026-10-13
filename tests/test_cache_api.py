import re

import pytest

from podpocket.catalog import list_episodes
from podpocket.ids import podcast_uuid
from podpocket.models import Podcast
from tests.factories import item, rss

FEED = "https://feeds.test/show.xml"
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SHOW = rss(
    item(
        guid="ep-2",
        title="Second episode",
        url="https://cdn.example.com/2.mp3",
        published="Tue, 13 Oct 2026 06:00:00 GMT",
        extra=(
            "<itunes:duration>90</itunes:duration><itunes:season>1</itunes:season>"
            "<content:encoded><![CDATA[<p>All about 100% cotton</p>]]></content:encoded>"
            '<podcast:chapters url="https://example.com/2.json" type="application/json+chapters"/>'
            '<podcast:transcript url="https://example.com/2.vtt" type="text/vtt" language="en"/>'
            '<itunes:image href="https://example.com/ep2.jpg"/>'
        ),
    ),
    item(guid="ep-1", title="First episode"),
    title="Cotton Talk",
    channel="<itunes:type>serial</itunes:type><itunes:author>Jane</itunes:author>",
)


@pytest.fixture
async def show(respx_mock, services):
    respx_mock.get(FEED).respond(200, content=SHOW)
    uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        episodes = {episode.guid: episode.uuid for episode in await list_episodes(session, uuid)}
    return uuid, episodes


async def test_podcast_full_matches_app_contract(client, show):
    uuid, episodes = show

    response = await client.get(f"/cache/mobile/podcast/full/{uuid}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=300"
    body = response.json()
    for key, kind in [
        ("episode_frequency", str),
        ("episode_count", int),
        ("has_more_episodes", bool),
        ("has_seasons", bool),
        ("season_count", int),
    ]:
        assert isinstance(body[key], kind)
    assert body["episode_count"] == 2
    assert (body["has_seasons"], body["season_count"]) == (True, 1)
    podcast = body["podcast"]
    assert podcast["uuid"] == uuid
    assert podcast["title"] == "Cotton Talk"
    assert podcast["author"] == "Jane"
    assert podcast["show_type"] == "serial"
    assert [e["uuid"] for e in podcast["episodes"]] == [episodes["ep-2"], episodes["ep-1"]]
    newest = podcast["episodes"][0]
    assert newest["url"] == "https://cdn.example.com/2.mp3"
    assert ISO_Z.match(newest["published"])
    assert newest["published"] == "2026-10-13T06:00:00Z"
    assert newest["duration"] == 90.0
    assert newest["file_size"] == 1000
    assert newest["type"] == "full"
    assert newest["season"] == 1


async def test_podcast_full_touches_podcast(client, show, services):
    uuid, _ = show

    await client.get(f"/cache/mobile/podcast/full/{uuid}")

    async with services.sessionmaker() as session:
        assert (await session.get(Podcast, uuid)).last_requested_at is not None


async def test_podcast_full_unknown_is_404(client):
    response = await client.get("/cache/mobile/podcast/full/does-not-exist")

    assert response.status_code == 404


async def test_podcast_full_fetches_stub_on_demand(respx_mock, client, services):
    stub = podcast_uuid(FEED)
    async with services.sessionmaker() as session:
        session.add(Podcast(uuid=stub, feed_url=FEED, title="Directory title"))
        await session.commit()
    respx_mock.get(FEED).respond(200, content=SHOW)

    response = await client.get(f"/cache/mobile/podcast/full/{stub}")

    assert response.status_code == 200
    assert len(response.json()["podcast"]["episodes"]) == 2


async def test_podcast_full_for_broken_stub_is_502(respx_mock, client, services):
    stub = podcast_uuid(FEED)
    async with services.sessionmaker() as session:
        session.add(Podcast(uuid=stub, feed_url=FEED))
        await session.commit()
    respx_mock.get(FEED).respond(500)

    response = await client.get(f"/cache/mobile/podcast/full/{stub}")

    assert response.status_code == 502


async def test_find_by_episode_returns_only_that_episode(client, show):
    uuid, episodes = show

    response = await client.get(f"/cache/mobile/podcast/findbyepisode/{uuid}/{episodes['ep-1']}")

    assert response.status_code == 200
    assert [e["uuid"] for e in response.json()["podcast"]["episodes"]] == [episodes["ep-1"]]


async def test_find_by_episode_unknown_episode_returns_podcast_without_episodes(client, show):
    uuid, _ = show

    response = await client.get(f"/cache/mobile/podcast/findbyepisode/{uuid}/not-an-episode")

    assert response.status_code == 200
    podcast = response.json()["podcast"]
    assert (podcast["uuid"], podcast["title"], podcast["episodes"]) == (uuid, "Cotton Talk", [])


async def test_find_by_episode_wrong_podcast_is_404(client, show):
    _, episodes = show

    response = await client.get(f"/cache/mobile/podcast/findbyepisode/other/{episodes['ep-1']}")

    assert response.status_code == 404


async def test_episode_url_is_bare_text(client, show):
    uuid, episodes = show

    response = await client.get(f"/cache/mobile/episode/url/{uuid}/{episodes['ep-2']}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == "https://cdn.example.com/2.mp3"


async def test_show_notes_two_step(client, show):
    uuid, episodes = show

    location = await client.get(f"/cache/mobile/show_notes/full/{uuid}?disableredirect=true")
    assert location.status_code == 200
    url = location.json()["url"]
    assert url == f"http://pod.test/cache/mobile/show_notes/data/{uuid}.json"

    notes = await client.get(url.removeprefix("http://pod.test"))

    assert notes.status_code == 200
    payload = notes.json()["podcast"]
    assert payload["uuid"] == uuid
    by_uuid = {episode["uuid"]: episode for episode in payload["episodes"]}
    newest = by_uuid[episodes["ep-2"]]
    assert newest["show_notes"] == "<p>All about 100% cotton</p>"
    assert newest["image"] == "https://example.com/ep2.jpg"
    assert newest["chapters_url"] == "https://example.com/2.json"
    assert newest["transcripts"] == [
        {"url": "https://example.com/2.vtt", "type": "text/vtt", "language": "en"}
    ]


async def test_search_within_podcast(client, show):
    uuid, episodes = show

    response = await client.post(
        "/cache/mobile/podcast/episode/search", json={"podcastuuid": uuid, "searchterm": "cotton"}
    )

    assert response.json() == {"episodes": [{"uuid": episodes["ep-2"]}]}


async def test_search_terms_are_escaped(client, show):
    uuid, episodes = show

    percent = await client.post(
        "/cache/mobile/podcast/episode/search", json={"podcastuuid": uuid, "searchterm": "100%"}
    )
    wildcard = await client.post(
        "/cache/mobile/podcast/episode/search", json={"podcastuuid": uuid, "searchterm": "%"}
    )

    assert percent.json() == {"episodes": [{"uuid": episodes["ep-2"]}]}
    assert wildcard.json() == {"episodes": [{"uuid": episodes["ep-2"]}]}


async def test_global_episode_search(client, show):
    uuid, episodes = show

    response = await client.post("/cache/episode/search", json={"term": "first"})

    assert response.json() == {
        "episodes": [
            {
                "uuid": episodes["ep-1"],
                "title": "First episode",
                "duration": 0.0,
                "published_date": "2026-10-12T06:00:00Z",
                "podcast_uuid": uuid,
                "podcast_title": "Cotton Talk",
            }
        ]
    }
