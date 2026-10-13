import io

import pytest
from PIL import Image

from podpocket.artwork import RETRY_FAILED_AFTER_SECONDS
from podpocket.ids import podcast_uuid
from podpocket.models import Podcast
from tests.factories import png

ART = "https://img.test/cover.png"


@pytest.fixture
async def podcast_with_art(services):
    async with services.sessionmaker() as session:
        session.add(Podcast(uuid="p1", feed_url="https://feeds.test/p1", artwork_url=ART))
        await session.commit()
    return "p1"


@pytest.mark.parametrize("size", [200, 480, 960])
async def test_artwork_is_square_webp(respx_mock, client, podcast_with_art, size):
    respx_mock.get(ART).respond(200, content=png())

    response = await client.get(f"/static/discover/images/webp/{size}/{podcast_with_art}.webp")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(response.content)) as image:
        assert (image.format, image.size) == ("WEBP", (size, size))


async def test_artwork_is_fetched_once_for_all_sizes(respx_mock, client, podcast_with_art):
    route = respx_mock.get(ART).respond(200, content=png())

    for size in (200, 480, 960):
        await client.get(f"/static/discover/images/webp/{size}/{podcast_with_art}.webp")
    await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert route.call_count == 1


async def test_unsupported_size_is_404(client, podcast_with_art):
    response = await client.get(f"/static/discover/images/webp/300/{podcast_with_art}.webp")

    assert response.status_code == 404


async def test_missing_artwork_is_404_but_colors_default(client):
    image = await client.get("/static/discover/images/webp/480/unknown.webp")
    colors = await client.get("/static/discover/images/metadata/unknown.json")

    assert image.status_code == 404
    assert colors.status_code == 200
    assert colors.json() == {
        "colors": {"background": "#3D3D3D", "tintForDarkBg": "#B3B3B3", "tintForLightBg": "#3D3D3D"}
    }


async def test_colors_come_from_the_artwork(respx_mock, client, podcast_with_art):
    respx_mock.get(ART).respond(200, content=png(color=(200, 40, 40)))

    response = await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert response.json() == {
        "colors": {"background": "#C82828", "tintForDarkBg": "#E9A9A9", "tintForLightBg": "#781818"}
    }


async def test_corrupt_artwork_is_not_refetched_immediately(respx_mock, client, podcast_with_art):
    route = respx_mock.get(ART).respond(200, content=b"not an image")

    first = await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")
    second = await client.get(f"/static/discover/images/webp/200/{podcast_with_art}.webp")

    assert (first.status_code, second.status_code) == (404, 404)
    assert route.call_count == 1


@pytest.mark.parametrize("document", ["content_v3", "content_v4", "content"])
async def test_discover_layout_contract(client, document):
    response = await client.get(f"/static/discover/android/{document}.json")

    assert response.status_code == 200
    layout = response.json()
    assert set(layout) >= {
        "layout",
        "regions",
        "region_code_token",
        "region_name_token",
        "default_region_code",
    }
    assert layout["default_region_code"] == "us"
    assert layout["regions"]["us"]["code"] == "us"
    for row in layout["layout"]:
        assert set(row) >= {"type", "summary_style", "title", "regions", "source"}
        assert row["source"].startswith("http://pod.test/static/")
    assert [row["type"] for row in layout["layout"]] == ["categories", "podcast_list"]


async def test_categories_are_empty(client):
    assert (await client.get("/static/discover/json/categories.json")).json() == []


async def test_top_list_follows_chart_order(respx_mock, client, services):
    respx_mock.get(host="rss.marketingtools.apple.com").respond(
        json={"feed": {"results": [{"id": "2", "name": "B"}, {"id": "1", "name": "A"}]}}
    )
    respx_mock.get(host="itunes.apple.com", path="/lookup").respond(
        json={
            "results": [
                {
                    "kind": "podcast",
                    "collectionId": 1,
                    "collectionName": "A",
                    "artistName": "a",
                    "feedUrl": "https://feeds.test/a",
                },
                {
                    "kind": "podcast",
                    "collectionId": 2,
                    "collectionName": "B",
                    "artistName": "b",
                    "feedUrl": "https://feeds.test/b",
                },
            ]
        }
    )

    response = await client.get("/static/discover/json/top.json")

    body = response.json()
    assert body["title"] == "Top podcasts"
    assert body["list_id"] == "top"
    assert body["podcasts"] == [
        {"uuid": podcast_uuid("https://feeds.test/b"), "title": "B", "author": "b"},
        {"uuid": podcast_uuid("https://feeds.test/a"), "title": "A", "author": "a"},
    ]


async def test_top_list_survives_directory_failure(respx_mock, client):
    respx_mock.get(host="rss.marketingtools.apple.com").respond(503)

    response = await client.get("/static/discover/json/top.json")

    assert response.status_code == 200
    assert response.json()["podcasts"] == []
    assert response.headers["cache-control"] == "public, max-age=60"


async def test_startup_documents_are_harmless(client):
    blaze = await client.get("/static/discover/blaze/promotions.json")
    whats_new = await client.get("/static/whats-new/v1/android/en.json")

    assert blaze.json() == {"promotions": []}
    assert whats_new.json() == {"schemaVersion": 1, "messages": []}


async def test_unknown_discover_documents_are_404(client):
    response = await client.get("/static/discover/android/something_else.json")

    assert response.status_code == 404


DEFAULT_COLORS_BODY = {
    "colors": {"background": "#3D3D3D", "tintForDarkBg": "#B3B3B3", "tintForLightBg": "#3D3D3D"}
}


async def test_oversized_image_is_refused_without_decoding(
    respx_mock, client, podcast_with_art, monkeypatch
):
    huge = io.BytesIO()
    Image.new("1", (6000, 5000)).save(huge, "PNG")
    respx_mock.get(ART).respond(200, content=huge.getvalue())

    def fail_decode(self, *args, **kwargs):
        raise AssertionError("oversized image was decoded")

    monkeypatch.setattr(Image.Image, "load", fail_decode)

    image = await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")
    colors = await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert image.status_code == 404
    assert colors.json() == DEFAULT_COLORS_BODY


async def test_failing_image_host_gives_404_and_default_colors(
    respx_mock, client, podcast_with_art
):
    respx_mock.get(ART).respond(503)

    image = await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")
    colors = await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert image.status_code == 404
    assert colors.status_code == 200
    assert colors.json() == DEFAULT_COLORS_BODY


async def test_corrupt_colors_file_falls_back_to_defaults(
    respx_mock, client, podcast_with_art, settings
):
    respx_mock.get(ART).respond(200, content=png())
    await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")
    [rendered] = [p for p in (settings.cache_dir / "artwork").iterdir() if p.is_dir()]
    (rendered / "colors.json").write_text("{not json")

    colors = await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert colors.status_code == 200
    assert colors.json() == DEFAULT_COLORS_BODY


async def test_unwritable_cache_gives_404_not_500(respx_mock, client, podcast_with_art, settings):
    respx_mock.get(ART).respond(200, content=png())
    (settings.cache_dir / "artwork").parent.mkdir(parents=True, exist_ok=True)
    (settings.cache_dir / "artwork").write_text("a file where the cache directory should be")

    image = await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")
    colors = await client.get(f"/static/discover/images/metadata/{podcast_with_art}.json")

    assert image.status_code == 404
    assert colors.json() == DEFAULT_COLORS_BODY


async def test_non_image_formats_are_rejected(respx_mock, client, podcast_with_art):
    respx_mock.get(ART).respond(200, content=b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n")

    response = await client.get(f"/static/discover/images/webp/480/{podcast_with_art}.webp")

    assert response.status_code == 404


async def test_failed_artwork_is_retried_after_the_cooldown(
    respx_mock, client, services, podcast_with_art
):
    now = [1_000_000.0]
    services.artwork._clock = lambda: now[0]
    route = respx_mock.get(ART).respond(200, content=b"not an image")
    url = f"/static/discover/images/webp/480/{podcast_with_art}.webp"

    await client.get(url)
    now[0] += RETRY_FAILED_AFTER_SECONDS - 1
    await client.get(url)
    assert route.call_count == 1

    now[0] += 2
    await client.get(url)
    assert route.call_count == 2


async def test_region_flag_is_a_png(client):
    response = await client.get("/static/discover/images/flags/us.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=604800"
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.format == "PNG"
