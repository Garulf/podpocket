from datetime import UTC, datetime

import pytest

from podpocket.feeds.errors import FeedError
from podpocket.feeds.parse import parse_duration, parse_feed, plain_text
from tests.factories import item, rss

CHANNEL = """
<description>Plain &lt;b&gt;desc&lt;/b&gt;</description>
<language>en-us</language>
<itunes:author>Jane</itunes:author>
<itunes:type>serial</itunes:type>
<itunes:explicit>yes</itunes:explicit>
<itunes:image href="https://example.com/art.jpg"/>
<itunes:category text="Technology"><itunes:category text="Podcasting"/></itunes:category>
<podcast:funding url="https://example.com/support">Support</podcast:funding>
"""

RICH_ITEM = item(
    guid="ep-2",
    title="Ep 2",
    url="https://cdn.example.com/2.mp3?x=1",
    extra="""
<itunes:duration>01:02:03</itunes:duration>
<itunes:season>2</itunes:season><itunes:episode>5</itunes:episode>
<itunes:episodeType>bonus</itunes:episodeType>
<itunes:image href="https://example.com/ep2.jpg"/>
<content:encoded><![CDATA[<p>Notes <b>2</b></p>]]></content:encoded>
<podcast:chapters url="https://example.com/2.json" type="application/json+chapters"/>
<podcast:transcript url="https://example.com/2.vtt" type="text/vtt" language="en"/>
""",
)


def test_parses_channel_fields():
    feed = parse_feed(rss(item(), title="Test &amp; Show", channel=CHANNEL))

    assert feed.title == "Test & Show"
    assert feed.author == "Jane"
    assert feed.description_html == "Plain <b>desc</b>"
    assert feed.description == "Plain desc"
    assert feed.website_url == "https://example.com"
    assert feed.artwork_url == "https://example.com/art.jpg"
    assert feed.category == "Technology"
    assert feed.language == "en-us"
    assert feed.show_type == "serial"
    assert feed.explicit is True
    assert feed.funding_url == "https://example.com/support"


def test_parses_episode_fields():
    episode = parse_feed(rss(RICH_ITEM)).episodes[0]

    assert episode.guid == "ep-2"
    assert episode.title == "Ep 2"
    assert episode.enclosure_url == "https://cdn.example.com/2.mp3?x=1"
    assert episode.file_type == "audio/mpeg"
    assert episode.file_size == 1000
    assert episode.duration == 3723.0
    assert episode.published == datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
    assert (episode.season, episode.number) == (2, 5)
    assert episode.episode_type == "bonus"
    assert episode.show_notes == "<p>Notes <b>2</b></p>"
    assert episode.image_url == "https://example.com/ep2.jpg"
    assert episode.chapters_url == "https://example.com/2.json"
    assert episode.transcripts == [
        {"url": "https://example.com/2.vtt", "type": "text/vtt", "language": "en"}
    ]


def test_messy_items_are_tolerated():
    feed = parse_feed(
        rss(
            item(guid="no-enclosure", url=None),
            item(guid=None, url="https://cdn.example.com/a.mp3"),
            item(guid="no-date", published=None),
            item(guid="bad-date", published="not a date"),
            item(guid="offset", published="Mon, 12 Oct 2026 08:00:00 +0200"),
            item(guid="weird-type", extra="<itunes:episodeType>weird</itunes:episodeType>"),
        )
    )

    by_guid = {episode.guid: episode for episode in feed.episodes}
    assert "no-enclosure" not in by_guid
    assert by_guid[None].enclosure_url == "https://cdn.example.com/a.mp3"
    assert by_guid["no-date"].published is None
    assert by_guid["bad-date"].published is None
    assert by_guid["offset"].published == datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
    assert by_guid["weird-type"].episode_type == "full"


def test_keeps_non_ascii_text():
    feed = parse_feed(rss(item(title="Épisode ünïcode 日本"), title="Café"))

    assert feed.title == "Café"
    assert feed.episodes[0].title == "Épisode ünïcode 日本"


@pytest.mark.parametrize(
    "content",
    [
        b"<html><head><title>Hi</title></head><body><p>hello</p></body></html>",
        b"\x00\x01binary",
        b"",
    ],
)
def test_rejects_non_feeds(content):
    with pytest.raises(FeedError, match="not a podcast feed"):
        parse_feed(content)


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [
        ("3723", 3723.0),
        ("62:03", 3723.0),
        ("01:02:03", 3723.0),
        ("1:02:03.5", 3723.5),
        ("", 0.0),
        (None, 0.0),
        ("abc", 0.0),
        ("nan", 0.0),
        ("inf", 0.0),
        ("-5", 0.0),
        ("1:-02:03", 0.0),
    ],
)
def test_parse_duration(raw, seconds):
    assert parse_duration(raw) == seconds


def test_plain_text_strips_tags_and_entities():
    assert plain_text("<p>Fish &amp;\n <b>chips</b></p>") == "Fish & chips"


@pytest.mark.parametrize("url", ["   ", "\n", ""])
def test_blank_enclosure_url_drops_the_item(url):
    feed = parse_feed(
        rss(item(guid="blank", url=None, extra=f'<enclosure url="{url}" type="audio/mpeg"/>'))
    )

    assert feed.episodes == []


def test_enclosure_url_is_stripped():
    episode = parse_feed(rss(item(url="  https://x/a.mp3\n"))).episodes[0]

    assert episode.enclosure_url == "https://x/a.mp3"


def test_blank_and_padded_urls_are_tidied():
    episode = parse_feed(
        rss(
            item(
                extra="""
<itunes:image href="  "/>
<podcast:chapters url=" https://example.com/c.json "/>
<podcast:transcript url="  " type="text/vtt"/>
<podcast:transcript url=" https://example.com/t.vtt " type="text/vtt"/>
"""
            )
        )
    ).episodes[0]

    assert episode.image_url is None
    assert episode.chapters_url == "https://example.com/c.json"
    assert [t["url"] for t in episode.transcripts] == ["https://example.com/t.vtt"]


def test_negative_file_size_is_clamped_to_zero():
    episode = parse_feed(
        rss(
            item(url=None, extra='<enclosure url="https://x/a.mp3" length="-1" type="audio/mpeg"/>')
        )
    ).episodes[0]

    assert episode.file_size == 0


def test_empty_content_encoded_falls_back_to_summary():
    episode = parse_feed(
        rss(
            item(extra="<description>Summary text</description><content:encoded></content:encoded>")
        )
    ).episodes[0]

    assert episode.show_notes == "Summary text"


def test_published_falls_back_to_updated():
    episode = parse_feed(
        rss(item(published=None, extra="<dc:date>2026-10-01T00:00:00Z</dc:date>"))
    ).episodes[0]

    assert episode.published == datetime(2026, 10, 1, tzinfo=UTC)
