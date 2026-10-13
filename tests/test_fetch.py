import asyncio
import gzip
import tracemalloc

import httpcore
import httpx
import pytest

from podpocket.feeds import fetch as fetch_module
from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy, GuardedNetworkBackend, fetch, guarded_transport

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


async def test_network_error_without_text_names_its_type(respx_mock, http):
    respx_mock.get("https://feeds.test/slow").mock(side_effect=httpx.ReadTimeout(""))

    with pytest.raises(FeedError, match="could not fetch https://feeds.test/slow: ReadTimeout"):
        await fetch(http, "https://feeds.test/slow", OPEN)


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


@pytest.fixture
def compressed_client():
    requests = []

    def build(body, encoding="gzip"):
        def handler(request):
            requests.append(request)
            return httpx.Response(
                200, headers={"Content-Encoding": encoding}, stream=httpx.ByteStream(body)
            )

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    build.requests = requests
    return build


async def test_decodes_gzip_body_and_requests_it(compressed_client):
    feed = b"<rss/>" * 100
    compressed = gzip.compress(feed)
    assert compressed != feed
    assert len(compressed) < len(feed)

    async with compressed_client(compressed) as client:
        result = await fetch(
            client, "https://feeds.test/gz", FetchPolicy(max_bytes=10_000, allow_private=True)
        )

    assert result.content == feed
    assert compressed_client.requests[0].headers["Accept-Encoding"] == "gzip"


async def test_rejects_gzip_bomb_without_materializing_it(compressed_client):
    bomb = gzip.compress(b"\0" * 10_000_000)

    async with compressed_client(bomb) as client:
        tracemalloc.start()
        try:
            with pytest.raises(FeedError, match="larger than"):
                await fetch(client, "https://feeds.test/bomb", OPEN)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

    assert peak < 1_000_000


async def test_rejects_chained_content_encodings(compressed_client):
    layered = gzip.compress(gzip.compress(b"\0" * 10_000_000))

    async with compressed_client(layered, "gzip, gzip") as client:
        with pytest.raises(FeedError, match="unsupported content encoding"):
            await fetch(client, "https://feeds.test/double", OPEN)


async def test_rejects_unsupported_content_encoding(compressed_client):
    async with compressed_client(b"data", "br") as client:
        with pytest.raises(FeedError, match="unsupported content encoding"):
            await fetch(client, "https://feeds.test/br", OPEN)


@pytest.mark.parametrize(
    "body",
    [
        b"not gzip",
        gzip.compress(b"<rss/>" * 100)[:-10],
        gzip.compress(b"<rss/>") + b"trailing",
    ],
    ids=["corrupt", "truncated", "trailing-data"],
)
async def test_rejects_invalid_gzip_bodies(compressed_client, body):
    async with compressed_client(body) as client:
        with pytest.raises(FeedError, match="invalid gzip"):
            await fetch(client, "https://feeds.test/bad", OPEN)


async def test_malformed_redirect_location_is_a_feed_error(respx_mock, http):
    respx_mock.get("https://feeds.test/a").respond(302, headers={"Location": "http://["})

    with pytest.raises(FeedError, match="unsupported URL"):
        await fetch(http, "https://feeds.test/a", OPEN)


async def test_redirect_without_location_is_an_http_error(respx_mock, http):
    respx_mock.get("https://feeds.test/a").respond(302)

    with pytest.raises(FeedError, match="returned HTTP 302"):
        await fetch(http, "https://feeds.test/a", OPEN)


async def test_redirect_to_file_url_is_refused(respx_mock, http):
    respx_mock.get("https://feeds.test/a").respond(302, headers={"Location": "file:///etc/passwd"})

    with pytest.raises(FeedError, match="unsupported URL"):
        await fetch(http, "https://feeds.test/a", OPEN)


async def test_invalid_idna_host_is_a_feed_error(http):
    with pytest.raises(FeedError):
        await fetch(http, "https://xn--a.test/", OPEN)


async def test_unresolvable_host_is_a_feed_error(monkeypatch, http):
    async def failing(host, port):
        raise OSError("no such host")

    monkeypatch.setattr(fetch_module, "resolve_host", failing)

    with pytest.raises(FeedError, match="cannot resolve"):
        await fetch(http, "https://gone.test/feed", GUARDED)


async def test_not_modified_prefers_validators_from_response(respx_mock, http):
    respx_mock.get("https://feeds.test/a").respond(304, headers={"ETag": '"v2"'})

    result = await fetch(http, "https://feeds.test/a", OPEN, etag='"v1"', last_modified="Mon")

    assert result.etag == '"v2"'
    assert result.last_modified == "Mon"


async def test_client_that_follows_redirects_cannot_bypass_the_guard(respx_mock, monkeypatch):
    monkeypatch.setattr(
        fetch_module,
        "resolve_host",
        fake_dns({"public.test": ["93.184.216.34"], "internal.test": ["127.0.0.1"]}),
    )
    respx_mock.get("https://public.test/feed").respond(
        302, headers={"Location": "http://internal.test/admin"}
    )
    respx_mock.get("http://internal.test/admin").respond(200, content=b"secret")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        with pytest.raises(FeedError, match="not a public address"):
            await fetch(client, "https://public.test/feed", GUARDED)


async def test_rebinding_between_check_and_connect_is_blocked(monkeypatch):
    connections = []

    async def on_connect(reader, writer):
        connections.append(writer)
        writer.close()

    server = await asyncio.start_server(on_connect, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    lookups = []

    async def rebinding_dns(host, _port):
        lookups.append(host)
        return ["93.184.216.34"] if len(lookups) == 1 else ["127.0.0.1"]

    monkeypatch.setattr(fetch_module, "resolve_host", rebinding_dns)
    try:
        async with httpx.AsyncClient(transport=guarded_transport()) as client:
            with pytest.raises(FeedError, match="not a public address"):
                await fetch(client, f"http://rebind.test:{port}/feed", GUARDED)
    finally:
        server.close()
        await server.wait_closed()

    assert len(lookups) == 2
    assert connections == []


class RecordingBackend(httpcore.AsyncNetworkBackend):
    def __init__(self):
        self.connected = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        return object()


async def test_backend_connects_to_the_vetted_address(monkeypatch):
    monkeypatch.setattr(
        fetch_module, "resolve_host", fake_dns({"pub.test": ["93.184.216.34", "1.1.1.1"]})
    )
    inner = RecordingBackend()

    await GuardedNetworkBackend(inner).connect_tcp("pub.test", 443)

    assert inner.connected == [("93.184.216.34", 443)]


class FailingFirstBackend(RecordingBackend):
    def __init__(self, failing):
        super().__init__()
        self.failing = failing

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append((host, port))
        if host in self.failing:
            raise httpcore.ConnectError(f"cannot reach {host}")
        return object()


async def test_backend_falls_back_to_the_next_vetted_address(monkeypatch):
    monkeypatch.setattr(
        fetch_module, "resolve_host", fake_dns({"pub.test": ["93.184.216.34", "1.1.1.1"]})
    )
    inner = FailingFirstBackend({"93.184.216.34"})

    await GuardedNetworkBackend(inner).connect_tcp("pub.test", 443)

    assert inner.connected == [("93.184.216.34", 443), ("1.1.1.1", 443)]


async def test_backend_raises_the_last_error_when_every_address_fails(monkeypatch):
    monkeypatch.setattr(
        fetch_module, "resolve_host", fake_dns({"pub.test": ["93.184.216.34", "1.1.1.1"]})
    )
    inner = FailingFirstBackend({"93.184.216.34", "1.1.1.1"})

    with pytest.raises(httpcore.ConnectError, match="cannot reach 1.1.1.1"):
        await GuardedNetworkBackend(inner).connect_tcp("pub.test", 443)

    assert inner.connected == [("93.184.216.34", 443), ("1.1.1.1", 443)]


async def test_backend_refuses_when_any_address_is_private(monkeypatch):
    monkeypatch.setattr(
        fetch_module, "resolve_host", fake_dns({"mixed.test": ["93.184.216.34", "10.0.0.1"]})
    )
    inner = RecordingBackend()

    with pytest.raises(httpcore.ConnectError, match="not a public address"):
        await GuardedNetworkBackend(inner).connect_tcp("mixed.test", 443)

    assert inner.connected == []


async def test_backend_refuses_unix_sockets():
    with pytest.raises(httpcore.ConnectError):
        await GuardedNetworkBackend(RecordingBackend()).connect_unix_socket("/var/run/docker.sock")
