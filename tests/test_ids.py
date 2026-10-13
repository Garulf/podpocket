import uuid

import pytest

from podpocket.ids import episode_uuid, normalize_feed_url, podcast_uuid


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://Example.COM/feed.xml", "https://example.com/feed.xml"),
        ("  https://example.com/feed.xml#top ", "https://example.com/feed.xml"),
        ("https://example.com", "https://example.com/"),
        ("HTTP://example.com/rss?id=1", "http://example.com/rss?id=1"),
        ("feed://example.com/rss", "http://example.com/rss"),
        ("feed:https://example.com/rss", "https://example.com/rss"),
        ("pcast://example.com/rss", "http://example.com/rss"),
        ("itpc://example.com/rss", "http://example.com/rss"),
        ("rss://example.com/rss", "http://example.com/rss"),
    ],
)
def test_normalize_feed_url(raw, expected):
    assert normalize_feed_url(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "example.com/rss", "ftp://example.com/rss", "javascript:alert(1)", "https://"]
)
def test_normalize_feed_url_rejects_non_http(raw):
    with pytest.raises(ValueError):
        normalize_feed_url(raw)


def test_podcast_uuid_is_pinned_and_ignores_url_spelling():
    assert podcast_uuid("https://example.com/feed.xml") == "d2816aca-6b72-56da-90da-7b6baa1636ca"
    assert podcast_uuid("HTTPS://EXAMPLE.com/feed.xml#x") == podcast_uuid(
        "https://example.com/feed.xml"
    )
    assert uuid.UUID(podcast_uuid("https://example.com/feed.xml")).version == 5


def test_episode_uuid_prefers_guid_and_is_pinned():
    show = podcast_uuid("https://example.com/feed.xml")

    assert episode_uuid(show, "ep-1", "https://cdn/1.mp3") == "e4b913d5-8160-5d2f-b0c3-f3b087e9a2e3"
    assert episode_uuid(show, "ep-1", "https://cdn/other.mp3") == episode_uuid(
        show, "ep-1", "https://cdn/1.mp3"
    )


def test_episode_uuid_falls_back_to_enclosure_and_is_scoped_to_podcast():
    first = podcast_uuid("https://a.example/feed")
    second = podcast_uuid("https://b.example/feed")

    assert episode_uuid(first, None, "https://cdn/1.mp3") == episode_uuid(
        first, "", "https://cdn/1.mp3"
    )
    assert episode_uuid(first, "ep-1", "x") != episode_uuid(second, "ep-1", "x")
