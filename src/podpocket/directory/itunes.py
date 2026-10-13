import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

SEARCH_URL = "https://itunes.apple.com/search"
LOOKUP_URL = "https://itunes.apple.com/lookup"
TOP_URL = "https://rss.marketingtools.apple.com/api/v2/{country}/podcasts/top/{limit}/podcasts.json"
SEARCH_CACHE_SECONDS = 600
TOP_CACHE_SECONDS = 6 * 3600
MAX_CACHE_ENTRIES = 256
APPLE_ID = re.compile(r"(?:podcasts|itunes)\.apple\.com/.*?id(\d+)")


class DirectoryError(Exception):
    pass


@dataclass(frozen=True)
class DirectoryPodcast:
    apple_id: str
    feed_url: str
    title: str
    author: str
    artwork_url: str | None
    category: str


def apple_podcast_id(url: str) -> str | None:
    match = APPLE_ID.search(url)
    return match.group(1) if match else None


class ITunesDirectory:
    def __init__(
        self,
        http: httpx.AsyncClient,
        country: str,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._country = country
        self._clock = clock
        self._cache: dict[str, tuple[float, list[DirectoryPodcast]]] = {}

    async def search(self, term: str, limit: int = 20) -> list[DirectoryPodcast]:
        params = {
            "media": "podcast",
            "entity": "podcast",
            "term": term,
            "limit": limit,
            "country": self._country,
        }
        return await self._cached(
            f"search:{limit}:{term.lower()}",
            SEARCH_CACHE_SECONDS,
            lambda: self._podcasts(SEARCH_URL, params),
        )

    async def lookup(self, apple_ids: list[str]) -> list[DirectoryPodcast]:
        if not apple_ids:
            return []
        found = await self._podcasts(LOOKUP_URL, {"id": ",".join(apple_ids), "entity": "podcast"})
        rank = {apple_id: index for index, apple_id in enumerate(apple_ids)}
        return sorted(found, key=lambda podcast: rank.get(podcast.apple_id, len(rank)))

    async def top(self, limit: int = 50) -> list[DirectoryPodcast]:
        return await self._cached(f"top:{limit}", TOP_CACHE_SECONDS, lambda: self._top(limit))

    async def _top(self, limit: int) -> list[DirectoryPodcast]:
        data = await self._json(TOP_URL.format(country=self._country, limit=limit), {})
        entries = data.get("feed", {}).get("results", [])
        return await self.lookup([str(entry["id"]) for entry in entries if entry.get("id")])

    async def _podcasts(self, url: str, params: dict[str, Any]) -> list[DirectoryPodcast]:
        data = await self._json(url, params)
        return [
            DirectoryPodcast(
                apple_id=str(entry.get("collectionId", "")),
                feed_url=entry["feedUrl"],
                title=entry.get("collectionName", ""),
                author=entry.get("artistName", ""),
                artwork_url=entry.get("artworkUrl600") or entry.get("artworkUrl100"),
                category=entry.get("primaryGenreName", ""),
            )
            for entry in data.get("results", [])
            if entry.get("feedUrl") and entry.get("kind", "podcast") == "podcast"
        ]

    async def _json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise DirectoryError(f"{url}: {exc}") from exc
        if not isinstance(data, dict):
            raise DirectoryError(f"{url}: unexpected response")
        return data

    async def _cached(
        self,
        key: str,
        ttl_seconds: float,
        load: Callable[[], Awaitable[list[DirectoryPodcast]]],
    ) -> list[DirectoryPodcast]:
        now = self._clock()
        hit = self._cache.get(key)
        if hit is not None and hit[0] > now:
            return hit[1]
        value = await load()
        if len(self._cache) >= MAX_CACHE_ENTRIES:
            self._cache.clear()
        self._cache[key] = (now + ttl_seconds, value)
        return value
