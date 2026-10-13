import re
from dataclasses import replace

import pytest

from podpocket.catalog import list_episodes
from podpocket.feeds import refresher as refresher_module
from podpocket.ids import podcast_uuid
from podpocket.models import Podcast
from tests.factories import item, rss

FEED = "https://feeds.test/show.xml"
GMT = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
DEVICE_FIELDS = {
    "v": "1.7",
    "av": "7.0",
    "ac": "1",
    "dt": "2",
    "c": "US",
    "l": "en",
    "m": "Pixel",
    "scope": "mobile",
    "push_on": "false",
}


def three_episodes() -> bytes:
    return rss(
        item(
            guid="ep-3",
            published="Wed, 14 Oct 2026 06:00:00 GMT",
            extra="<itunes:duration>60</itunes:duration>",
        ),
        item(guid="ep-2", published="Tue, 13 Oct 2026 06:00:00 GMT"),
        item(guid="ep-1", published="Mon, 12 Oct 2026 06:00:00 GMT"),
        title="Show",
    )


@pytest.fixture
async def show(respx_mock, services):
    respx_mock.get(FEED).respond(200, content=three_episodes())
    uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        ids = {e.guid: e.uuid for e in await list_episodes(session, uuid)}
    return uuid, ids


async def post_update(client, podcasts, last_episodes):
    return await client.post(
        "/refresh/user/update",
        data={"podcasts": podcasts, "last_episodes": last_episodes, **DEVICE_FIELDS},
    )


async def test_update_returns_newer_episodes_newest_first(client, show):
    uuid, ids = show

    response = await post_update(client, uuid, ids["ep-1"])

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    episodes = body["result"]["podcast_updates"][uuid]
    assert [e["uuid"] for e in episodes] == [ids["ep-3"], ids["ep-2"]]
    newest = episodes[0]
    assert GMT.match(newest["published_at"])
    assert newest["published_at"] == "2026-10-14 06:00:00"
    assert newest["size_in_bytes"] == 1000
    assert newest["duration_in_secs"] == 60.0
    assert newest["url"] == "https://cdn.example.com/1.mp3"


async def test_update_omits_podcasts_that_are_current(client, show):
    uuid, ids = show

    response = await post_update(client, uuid, ids["ep-3"])

    assert response.json()["result"]["podcast_updates"] == {}


@pytest.mark.parametrize("last", ["null", ""])
async def test_update_with_no_known_episode_returns_everything(client, show, last):
    uuid, ids = show

    response = await post_update(client, uuid, last)

    episodes = response.json()["result"]["podcast_updates"][uuid]
    assert [e["uuid"] for e in episodes] == [ids["ep-3"], ids["ep-2"], ids["ep-1"]]


async def test_update_with_unknown_episode_returns_newest_only(client, show):
    uuid, ids = show

    response = await post_update(client, uuid, "not-an-episode")

    assert [e["uuid"] for e in response.json()["result"]["podcast_updates"][uuid]] == [ids["ep-3"]]


async def test_update_tolerates_missing_last_episodes_and_unknown_podcasts(client, show):
    uuid, ids = show

    response = await post_update(client, f"unknown-podcast,{uuid}", "null")

    updates = response.json()["result"]["podcast_updates"]
    assert list(updates) == [uuid]
    assert len(updates[uuid]) == 3


async def test_update_marks_podcasts_requested(client, show, services):
    uuid, ids = show

    await post_update(client, uuid, ids["ep-3"])

    async with services.sessionmaker() as session:
        assert (await session.get(Podcast, uuid)).last_requested_at is not None


async def test_search_with_feed_url_adds_podcast(respx_mock, client):
    respx_mock.get("http://feeds.test/show.xml").respond(
        200, content=rss(item(), title="Pasted Show")
    )

    response = await client.post(
        "/refresh/podcasts/search", data={"q": "feed://feeds.test/show.xml", **DEVICE_FIELDS}
    )

    body = response.json()
    assert body["status"] == "ok"
    assert body["result"]["is_url"] is True
    expected = podcast_uuid("http://feeds.test/show.xml")
    assert body["result"]["podcast"] == {"uuid": expected, "title": "Pasted Show", "author": ""}


async def test_search_with_apple_url_uses_lookup(respx_mock, client):
    respx_mock.get(host="itunes.apple.com", path="/lookup").respond(
        json={
            "results": [
                {
                    "kind": "podcast",
                    "collectionId": 99,
                    "collectionName": "Apple Show",
                    "artistName": "A",
                    "feedUrl": FEED,
                }
            ]
        }
    )
    respx_mock.get(FEED).respond(200, content=rss(item(), title="Apple Show"))

    response = await client.post(
        "/refresh/podcasts/search",
        data={"q": "https://podcasts.apple.com/us/podcast/apple-show/id99"},
    )

    assert response.json()["result"]["podcast"]["uuid"] == podcast_uuid(FEED)


@pytest.mark.parametrize(
    ("status", "content"), [(200, b"<html><body>not a feed</body></html>"), (404, b"")]
)
async def test_search_with_broken_url_returns_error_envelope(
    respx_mock, client, services, status, content
):
    respx_mock.get("https://example.test/page").respond(status, content=content)

    response = await client.post(
        "/refresh/podcasts/search", data={"q": "https://example.test/page"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "error"
    assert body["message"]
    async with services.sessionmaker() as session:
        assert await session.get(Podcast, podcast_uuid("https://example.test/page")) is None


async def test_search_with_feed_that_fails_to_store_returns_error_envelope(
    respx_mock, client, services, monkeypatch
):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    real_parse = refresher_module.parse_feed
    monkeypatch.setattr(
        refresher_module, "parse_feed", lambda content: replace(real_parse(content), category=None)
    )

    response = await client.post("/refresh/podcasts/search", data={"q": FEED})

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    async with services.sessionmaker() as session:
        assert await session.get(Podcast, podcast_uuid(FEED)) is None


async def test_search_with_text_returns_directory_results(respx_mock, client):
    respx_mock.get(host="itunes.apple.com", path="/search").respond(
        json={
            "results": [
                {
                    "kind": "podcast",
                    "collectionId": 1,
                    "collectionName": "Found",
                    "artistName": "Someone",
                    "feedUrl": "https://feeds.test/found",
                }
            ]
        }
    )

    response = await client.post("/refresh/podcasts/search", data={"q": "found"})

    result = response.json()["result"]
    assert result["is_url"] is False
    assert result["search_results"] == [
        {"uuid": podcast_uuid("https://feeds.test/found"), "title": "Found", "author": "Someone"}
    ]


async def test_opml_import_resolves_feeds(respx_mock, client):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    respx_mock.get("https://feeds.test/broken").respond(500)

    response = await client.post(
        "/refresh/import/opml",
        json={"urls": [FEED, "https://feeds.test/broken"], "v": "1.7", "dt": "2"},
    )

    assert response.json() == {
        "status": "ok",
        "result": {"uuids": [podcast_uuid(FEED)], "poll_uuids": [], "failed": 1},
    }


async def test_export_feed_urls(client, show):
    uuid, _ = show

    response = await client.post("/refresh/import/export_feed_urls", data={"uuids": f"{uuid},nope"})

    assert response.json() == {"status": "ok", "result": {uuid: FEED}}


async def test_update_podcast_reports_new_episode(client, show):
    uuid, ids = show

    newer = await client.get(
        "/refresh/api/v1/update_podcast",
        params={"podcast_uuid": uuid, "last_episode_uuid": ids["ep-2"]},
    )
    same = await client.get(
        "/refresh/api/v1/update_podcast",
        params={"podcast_uuid": uuid, "last_episode_uuid": ids["ep-3"]},
    )
    unknown = await client.get("/refresh/api/v1/update_podcast", params={"podcast_uuid": "nope"})

    assert (newer.status_code, same.status_code, unknown.status_code) == (200, 204, 404)
