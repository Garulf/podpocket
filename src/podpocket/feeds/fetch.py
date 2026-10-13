import asyncio
import ipaddress
import socket
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from podpocket.feeds.errors import FeedError

PERMANENT_REDIRECTS = frozenset({301, 308})
MAX_REDIRECTS = 5
GZIP_ENCODINGS = frozenset({"gzip", "x-gzip"})
GZIP_WINDOW_BITS = 16 + zlib.MAX_WBITS


@dataclass(frozen=True)
class FetchPolicy:
    max_bytes: int
    allow_private: bool
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class FetchResult:
    content: bytes | None
    final_url: str
    moved_permanently_to: str | None
    etag: str | None
    last_modified: str | None


async def resolve_host(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0].split("%")[0] for info in infos]


def _all_public(addresses: Iterable[str]) -> bool:
    return all(ipaddress.ip_address(address).is_global for address in addresses)


class GuardedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            addresses = await resolve_host(host, port)
        except (OSError, ValueError) as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}") from exc
        if not addresses or not _all_public(addresses):
            raise httpcore.ConnectError(f"{host} is not a public address")
        return await self._inner.connect_tcp(
            addresses[0], port, timeout, local_address, socket_options
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


def guarded_transport() -> httpx.AsyncHTTPTransport:
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=httpx.create_ssl_context(),
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,
        http1=True,
        http2=False,
        network_backend=GuardedNetworkBackend(),
    )
    return transport


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    policy: FetchPolicy,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    headers = {"Accept-Encoding": "gzip"}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        async with asyncio.timeout(policy.timeout_seconds):
            return await _follow_redirects(client, url, policy, headers)
    except TimeoutError as exc:
        raise FeedError(f"timed out fetching {url}") from exc
    except httpx.HTTPError as exc:
        raise FeedError(f"could not fetch {url}: {exc}") from exc
    except (ValueError, httpx.InvalidURL) as exc:
        raise FeedError(f"unsupported URL {url}") from exc


async def _follow_redirects(
    client: httpx.AsyncClient, url: str, policy: FetchPolicy, headers: dict[str, str]
) -> FetchResult:
    current = url
    all_hops_permanent = True
    for _ in range(MAX_REDIRECTS + 1):
        await _check_destination(current, policy.allow_private)
        async with client.stream(
            "GET", current, headers=headers, follow_redirects=False
        ) as response:
            if response.has_redirect_location:
                all_hops_permanent = all_hops_permanent and (
                    response.status_code in PERMANENT_REDIRECTS
                )
                current = urljoin(current, response.headers["location"])
                continue
            moved = current if all_hops_permanent and current != url else None
            if response.status_code == 304:
                return FetchResult(
                    None,
                    current,
                    moved,
                    response.headers.get("etag", headers.get("If-None-Match")),
                    response.headers.get("last-modified", headers.get("If-Modified-Since")),
                )
            if not response.is_success:
                raise FeedError(f"{current} returned HTTP {response.status_code}")
            content = await _read_limited(response, policy.max_bytes)
            return FetchResult(
                content,
                current,
                moved,
                response.headers.get("etag"),
                response.headers.get("last-modified"),
            )
    raise FeedError(f"too many redirects fetching {url}")


async def _check_destination(url: str, allow_private: bool) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FeedError(f"unsupported URL {url}")
    if allow_private:
        return
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = await resolve_host(parts.hostname, port)
    except (OSError, ValueError) as exc:
        raise FeedError(f"cannot resolve {parts.hostname}") from exc
    if not addresses or not _all_public(addresses):
        raise FeedError(f"{parts.hostname} is not a public address")


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise _too_large(response, max_bytes)
    inflater = _inflater_for(response)
    chunks = []
    total = 0
    async for raw in response.aiter_raw():
        data = raw if inflater is None else _inflate(inflater, raw, max_bytes - total, response)
        total += len(data)
        if total > max_bytes:
            raise _too_large(response, max_bytes)
        chunks.append(data)
    if inflater is not None and not inflater.eof:
        raise FeedError(f"{response.url} has an invalid gzip body")
    return b"".join(chunks)


def _inflater_for(response: httpx.Response):
    encoding = response.headers.get("content-encoding", "identity").strip().lower()
    if encoding in ("", "identity"):
        return None
    if encoding in GZIP_ENCODINGS:
        return zlib.decompressobj(GZIP_WINDOW_BITS)
    raise FeedError(f"unsupported content encoding {encoding} from {response.url}")


def _inflate(inflater, chunk: bytes, remaining: int, response: httpx.Response) -> bytes:
    if inflater.eof:
        raise FeedError(f"{response.url} has an invalid gzip body")
    pieces = []
    produced = 0
    pending = chunk
    try:
        while pending and produced <= remaining and not inflater.eof:
            piece = inflater.decompress(pending, remaining + 1 - produced)
            pieces.append(piece)
            produced += len(piece)
            pending = inflater.unconsumed_tail
    except zlib.error as exc:
        raise FeedError(f"{response.url} has an invalid gzip body") from exc
    if inflater.unused_data:
        raise FeedError(f"{response.url} has an invalid gzip body")
    return b"".join(pieces)


def _too_large(response: httpx.Response, max_bytes: int) -> FeedError:
    return FeedError(f"{response.url} is larger than {max_bytes} bytes")
