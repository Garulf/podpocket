import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from podpocket.feeds.errors import FeedError

PERMANENT_REDIRECTS = frozenset({301, 308})
MAX_REDIRECTS = 5


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


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    policy: FetchPolicy,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> FetchResult:
    headers = {}
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


async def _follow_redirects(
    client: httpx.AsyncClient, url: str, policy: FetchPolicy, headers: dict[str, str]
) -> FetchResult:
    current = url
    all_hops_permanent = True
    for _ in range(MAX_REDIRECTS + 1):
        await _check_destination(current, policy.allow_private)
        async with client.stream("GET", current, headers=headers) as response:
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
                    headers.get("If-None-Match"),
                    headers.get("If-Modified-Since"),
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
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
        raise FeedError(f"{parts.hostname} is not a public address")


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise FeedError(f"{response.url} is larger than {max_bytes} bytes")
    chunks = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise FeedError(f"{response.url} is larger than {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
