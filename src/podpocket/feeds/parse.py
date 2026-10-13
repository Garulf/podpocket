import calendar
import html
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import feedparser

from podpocket.feeds.errors import FeedError

EPISODE_TYPES = frozenset({"full", "trailer", "bonus"})
_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class ParsedEpisode:
    guid: str | None
    title: str
    enclosure_url: str
    file_type: str
    file_size: int
    duration: float
    published: datetime | None
    season: int | None
    number: int | None
    episode_type: str
    show_notes: str
    image_url: str | None
    chapters_url: str | None
    transcripts: list[dict[str, str]]


@dataclass(frozen=True)
class ParsedFeed:
    title: str
    author: str
    description: str
    description_html: str
    website_url: str | None
    artwork_url: str | None
    category: str
    language: str
    show_type: str
    explicit: bool
    funding_url: str | None
    episodes: list[ParsedEpisode]


def parse_feed(content: bytes) -> ParsedFeed:
    parsed = feedparser.parse(content)
    if not parsed.get("version"):
        raise FeedError("not a podcast feed")
    channel = parsed.feed
    description_html = channel.get("summary") or channel.get("subtitle") or ""
    return ParsedFeed(
        title=channel.get("title", ""),
        author=channel.get("author", ""),
        description=plain_text(description_html),
        description_html=description_html,
        website_url=channel.get("link"),
        artwork_url=_attribute(channel.get("image"), "href"),
        category=_first_category(channel),
        language=channel.get("language", ""),
        show_type="serial" if channel.get("itunes_type") == "serial" else "episodic",
        explicit=channel.get("itunes_explicit") is True,
        funding_url=_attribute(channel.get("podcast_funding"), "url"),
        episodes=[episode for entry in parsed.entries if (episode := _parse_entry(entry))],
    )


def parse_duration(raw: Any) -> float:
    if not raw:
        return 0.0
    try:
        parts = [float(part) for part in str(raw).strip().split(":")]
    except ValueError:
        return 0.0
    if not all(math.isfinite(part) and part >= 0 for part in parts):
        return 0.0
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def plain_text(markup: str) -> str:
    return _WHITESPACE.sub(" ", html.unescape(_TAG.sub(" ", markup))).strip()


def _parse_entry(entry: Any) -> ParsedEpisode | None:
    enclosure = next(
        (link for link in entry.get("enclosures", []) if _clean(link.get("href"))), None
    )
    if enclosure is None:
        return None
    content_value = (entry.get("content") or [{}])[0].get("value", "")
    episode_type = str(entry.get("itunes_episodetype", "full")).lower()
    return ParsedEpisode(
        guid=entry.get("id") or None,
        title=entry.get("title", ""),
        enclosure_url=_clean(enclosure["href"]) or "",
        file_type=enclosure.get("type", ""),
        file_size=max(_integer(enclosure.get("length")) or 0, 0),
        duration=parse_duration(entry.get("itunes_duration")),
        published=_published(entry),
        season=_integer(entry.get("itunes_season")),
        number=_integer(entry.get("itunes_episode")),
        episode_type=episode_type if episode_type in EPISODE_TYPES else "full",
        show_notes=content_value or entry.get("summary", ""),
        image_url=_attribute(entry.get("image"), "href"),
        chapters_url=_attribute(entry.get("podcast_chapters"), "url"),
        transcripts=_transcripts(entry.get("podcast_transcript")),
    )


def _published(entry: Any) -> datetime | None:
    stamp = entry.get("published_parsed")
    if not stamp and "updated_parsed" in entry:
        stamp = entry["updated_parsed"]
    if not stamp:
        return None
    try:
        return datetime.fromtimestamp(calendar.timegm(stamp), UTC)
    except (OverflowError, ValueError, OSError):
        return None


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _attribute(value: Any, key: str) -> str | None:
    return _clean(value.get(key)) if isinstance(value, dict) else None


def _clean(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


def _first_category(channel: Any) -> str:
    tags = channel.get("tags") or []
    return tags[0].get("term", "") if tags else ""


def _transcripts(value: Any) -> list[dict[str, str]]:
    candidates = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [
        {
            "url": url,
            "type": candidate.get("type", ""),
            "language": candidate.get("language", ""),
        }
        for candidate in candidates
        if isinstance(candidate, dict) and (url := _clean(candidate.get("url")))
    ]
