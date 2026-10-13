import uuid
from urllib.parse import SplitResult, urlsplit, urlunsplit

NAMESPACE = uuid.UUID("6b1f3c1e-5d0a-4c7e-9a0e-2f6d8c4b7a10")
DEFAULT_PORTS = {"http": 80, "https": 443}
PSEUDO_SCHEMES = frozenset({"feed", "pcast", "itpc", "rss"})


def normalize_feed_url(raw: str) -> str:
    url = raw.strip()
    scheme, separator, rest = url.partition(":")
    if separator and scheme.lower() in PSEUDO_SCHEMES:
        rest = rest.removeprefix("//")
        url = rest if rest.lower().startswith(("http://", "https://")) else f"http://{rest}"
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in DEFAULT_PORTS or not parts.hostname:
        raise ValueError(f"not an http(s) URL: {raw!r}")
    return urlunsplit(
        (scheme, _normalized_netloc(parts, scheme), parts.path or "/", parts.query, "")
    )


def _normalized_netloc(parts: SplitResult, scheme: str) -> str:
    userinfo, has_userinfo, _ = parts.netloc.rpartition("@")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    port = parts.port
    suffix = f":{port}" if port is not None and port != DEFAULT_PORTS[scheme] else ""
    return f"{userinfo}{has_userinfo}{host}{suffix}"


def podcast_uuid(feed_url: str) -> str:
    return str(uuid.uuid5(NAMESPACE, normalize_feed_url(feed_url)))


def episode_uuid(podcast_uuid: str, guid: str | None, enclosure_url: str) -> str:
    return str(uuid.uuid5(uuid.UUID(podcast_uuid), guid or enclosure_url))
