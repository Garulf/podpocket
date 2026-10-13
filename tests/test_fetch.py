import asyncio

import httpx
import pytest

from podpocket.feeds import fetch as fetch_module
from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy, fetch

OPEN = FetchPolicy(max_bytes=1000, allow_private=True)
GUARDED = FetchPolicy(max_bytes=1000, allow_private=False)


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def fake_dns(mapping):
    async def resolve(host, port):
        return mapping[host]

    return resolve


async def test_returns_body_and_validators(respx_mock, http):
    respx_mock.get("https://feeds.test/a").respond(
        200, content=b"<rss/>", headers={"ETag": '"v1"', "Last-Modified": "Mon, 12 Oct 2026"}
    )

    result = await fetch(http, "https://feeds.test/a", OPEN)

    assert result.content == b"<rss/>"
    assert result.final_url == "https://feeds.test/a"
    assert result.moved_permanently_to is None
    assert result.etag == '"v1"'
    assert result.last_modified == "Mon, 12 Oct 2026"


async def test_sends_validators_and_reports_not_modified(respx_mock, http):
    route = respx_mock.get("https://feeds.test/a").respond(304)

    result = await fetch(http, "https://feeds.test/a", OPEN, etag='"v1"', last_modified="Mon")

    assert result.content is None
    assert route.calls.last.request.headers["If-None-Match"] == '"v1"'
    assert route.calls.last.request.headers["If-Modified-Since"] == "Mon"


async def test_permanent_redirect_reports_new_location(respx_mock, http):
    respx_mock.get("https://old.test/feed").respond(
        301, headers={"Location": "https://new.test/feed"}
    )
    respx_mock.get("https://new.test/feed").respond(200, content=b"x")

    result = await fetch(http, "https://old.test/feed", OPEN)

    assert result.final_url == "https://new.test/feed"
    assert result.moved_permanently_to == "https://new.test/feed"


async def test_chain_with_temporary_hop_is_not_a_move(respx_mock, http):
    respx_mock.get("https://old.test/feed").respond(301, headers={"Location": "https://mid.test/f"})
    respx_mock.get("https://mid.test/f").respond(302, headers={"Location": "/final"})
    respx_mock.get("https://mid.test/final").respond(200, content=b"x")

    result = await fetch(http, "https://old.test/feed", OPEN)

    assert result.final_url == "https://mid.test/final"
    assert result.moved_permanently_to is None


async def test_redirect_loop_is_refused(respx_mock, http):
    respx_mock.get("https://loop.test/a").respond(301, headers={"Location": "https://loop.test/a"})

    with pytest.raises(FeedError, match="too many redirects"):
        await fetch(http, "https://loop.test/a", OPEN)


async def test_error_status_raises(respx_mock, http):
    respx_mock.get("https://feeds.test/missing").respond(404)

    with pytest.raises(FeedError, match="404"):
        await fetch(http, "https://feeds.test/missing", OPEN)


async def test_rejects_declared_oversized_body(respx_mock, http):
    respx_mock.get("https://feeds.test/big").respond(200, content=b"x" * 2000)

    with pytest.raises(FeedError, match="larger than"):
        await fetch(http, "https://feeds.test/big", OPEN)


async def test_rejects_streamed_oversized_body(respx_mock, http):
    respx_mock.get("https://feeds.test/stream").mock(
        return_value=httpx.Response(200, stream=httpx.ByteStream(b"x" * 2000))
    )

    with pytest.raises(FeedError, match="larger than"):
        await fetch(http, "https://feeds.test/stream", OPEN)


async def test_times_out(respx_mock, http):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, content=b"late")

    respx_mock.get("https://feeds.test/slow").mock(side_effect=slow)
    policy = FetchPolicy(max_bytes=1000, allow_private=True, timeout_seconds=0.05)

    with pytest.raises(FeedError, match="timed out"):
        await fetch(http, "https://feeds.test/slow", policy)


async def test_network_error_becomes_feed_error(respx_mock, http):
    respx_mock.get("https://feeds.test/down").mock(side_effect=httpx.ConnectError("refused"))

    with pytest.raises(FeedError, match="could not fetch"):
        await fetch(http, "https://feeds.test/down", OPEN)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://feeds.test/a", "https:///nohost"])
async def test_rejects_non_http_urls(http, url):
    with pytest.raises(FeedError, match="unsupported URL"):
        await fetch(http, url, OPEN)


async def test_blocks_private_addresses(monkeypatch, http):
    monkeypatch.setattr(fetch_module, "resolve_host", fake_dns({"intranet.test": ["10.0.0.5"]}))

    with pytest.raises(FeedError, match="not a public address"):
        await fetch(http, "https://intranet.test/feed", GUARDED)


async def test_blocks_redirect_into_private_network(respx_mock, monkeypatch, http):
    monkeypatch.setattr(
        fetch_module,
        "resolve_host",
        fake_dns({"public.test": ["93.184.216.34"], "internal.test": ["127.0.0.1"]}),
    )
    respx_mock.get("https://public.test/feed").respond(
        302, headers={"Location": "http://internal.test/admin"}
    )

    with pytest.raises(FeedError, match="not a public address"):
        await fetch(http, "https://public.test/feed", GUARDED)


async def test_allows_public_addresses(respx_mock, monkeypatch, http):
    monkeypatch.setattr(fetch_module, "resolve_host", fake_dns({"public.test": ["93.184.216.34"]}))
    respx_mock.get("https://public.test/feed").respond(200, content=b"ok")

    result = await fetch(http, "https://public.test/feed", GUARDED)

    assert result.content == b"ok"
