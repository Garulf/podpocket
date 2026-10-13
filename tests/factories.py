NAMESPACES = (
    'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
    'xmlns:podcast="https://podcastindex.org/namespace/1.0" '
    'xmlns:content="http://purl.org/rss/1.0/modules/content/"'
)


def rss(*items: str, title: str = "Test Show", channel: str = "") -> bytes:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0" {NAMESPACES}><channel>'
        f"<title>{title}</title><link>https://example.com</link>{channel}{''.join(items)}"
        "</channel></rss>"
    ).encode()


def item(
    guid: str | None = "ep-1",
    title: str = "Episode 1",
    url: str | None = "https://cdn.example.com/1.mp3",
    published: str | None = "Mon, 12 Oct 2026 06:00:00 GMT",
    extra: str = "",
) -> str:
    parts = [f"<title>{title}</title>"]
    if guid is not None:
        parts.append(f'<guid isPermaLink="false">{guid}</guid>')
    if url is not None:
        parts.append(f'<enclosure url="{url}" length="1000" type="audio/mpeg"/>')
    if published is not None:
        parts.append(f"<pubDate>{published}</pubDate>")
    parts.append(extra)
    return f"<item>{''.join(parts)}</item>"
