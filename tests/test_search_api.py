import asyncio
import re

import pytest

from podpocket.ids import podcast_uuid
from podpocket.models import Podcast
from tests.factories import item, rss

ITUNES_RESULTS = {
    "resultCount": 3,
    "results": [
        {
            "kind": "podcast",
            "collectionId": 1,
            "collectionName": "Radio Lab",
            "artistName": "WNYC",
            "feedUrl": "https://feeds.test/radiolab",
            "artworkUrl600": "https://img.test/radiolab.jpg",
            "primaryGenreName": "Science",
        },
        {"kind": "podcast", "collectionId": 2, "collectionName": "No Feed", "artistName": "X"},
        {
            "kind": "podcast",
            "collectionId": 3,
            "collectionName": "Local Copy",
            "artistName": "Me",
            "feedUrl": "https://feeds.test/local",
        },
    ],
}


@pytest.fixture
def itunes_search(respx_mock):
    return respx_mock.get(host="itunes.apple.com", path="/search").respond(json=ITUNES_RESULTS)


@pytest.fixture
async def local_show(respx_mock, services):
    respx_mock.get("https://feeds.test/local").respond(
        200, content=rss(item(title="Radio waves explained"), title="Local radio show")
    )
    return await services.refresher.resolve("https://feeds.test/local")


async def test_autocomplete_returns_directory_podcasts_as_stubs(client, services, itunes_search):
    response = await client.get("/search/autocomplete/search", params={"q": "radio"})

    assert response.status_code == 200
    results = response.json()["results"]
    radiolab = podcast_uuid("https://feeds.test/radiolab")
    assert results[0] == {
        "type": "podcast",
        "value": {"uuid": radiolab, "title": "Radio Lab", "author": "WNYC"},
    }
    assert all(result["value"]["title"] != "No Feed" for result in results)
    async with services.sessionmaker() as session:
        stub = await session.get(Podcast, radiolab)
    assert stub.fetched_at is None
    assert stub.artwork_url == "https://img.test/radiolab.jpg"
    assert itunes_search.calls.last.request.url.params["term"] == "radio"


async def test_local_podcasts_come_first_and_duplicates_collapse(client, local_show, itunes_search):
    response = await client.get("/search/autocomplete/search", params={"q": "radio"})

    uuids = [result["value"]["uuid"] for result in response.json()["results"]]
    assert uuids[0] == local_show
    assert uuids.count(local_show) == 1


async def test_short_terms_skip_the_directory(client, local_show):
    response = await client.get("/search/autocomplete/search", params={"q": "lo"})

    assert [r["value"]["uuid"] for r in response.json()["results"]] == [local_show]


async def test_directory_failure_still_returns_local_results(respx_mock, client, local_show):
    respx_mock.get(host="itunes.apple.com", path="/search").respond(500)

    response = await client.get("/search/autocomplete/search", params={"q": "radio"})

    assert response.status_code == 200
    assert [r["value"]["uuid"] for r in response.json()["results"]] == [local_show]


async def test_directory_searches_are_cached(client, itunes_search):
    await client.get("/search/autocomplete/search", params={"q": "radio"})
    await client.get("/search/autocomplete/search", params={"q": "Radio"})

    assert itunes_search.call_count == 1


async def test_concurrent_searches_creating_same_stubs_succeed(client, itunes_search):
    first, second = await asyncio.gather(
        client.post("/cache/v2/search/combined", json={"term": "radio"}),
        client.post("/cache/v2/search/combined", json={"term": "radio"}),
    )

    assert (first.status_code, second.status_code) == (200, 200)


async def test_combined_search_returns_podcasts_and_episodes(client, local_show, itunes_search):
    response = await client.post("/cache/v2/search/combined", json={"term": "radio"})

    results = response.json()["results"]
    podcasts = [r for r in results if r["type"] == "podcast"]
    episodes = [r for r in results if r["type"] == "episode"]
    assert podcasts[0] == {
        "type": "podcast",
        "uuid": local_show,
        "title": "Local radio show",
        "author": "",
    }
    assert episodes[0]["title"] == "Radio waves explained"
    assert episodes[0]["podcast_uuid"] == local_show
    assert episodes[0]["podcast_title"] == "Local radio show"
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", episodes[0]["published_date"])
    assert isinstance(episodes[0]["duration"], int)


async def test_blank_terms_return_nothing(client):
    autocomplete = await client.get("/search/autocomplete/search", params={"q": "  "})
    combined = await client.post("/cache/v2/search/combined", json={"term": " "})

    assert autocomplete.json() == {"results": []}
    assert combined.json() == {"results": []}
