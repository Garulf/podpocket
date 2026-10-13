from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.feeds.parse import ParsedEpisode, ParsedFeed
from podpocket.ids import episode_uuid
from podpocket.models import Episode, Podcast


async def apply_feed(
    session: AsyncSession, podcast: Podcast, feed: ParsedFeed, *, now: datetime
) -> list[Episode]:
    _apply_channel(podcast, feed)
    existing = {
        episode.uuid: episode
        for episode in await session.scalars(
            select(Episode).where(Episode.podcast_uuid == podcast.uuid)
        )
    }
    seen: set[str] = set()
    inserted: list[Episode] = []
    for parsed in feed.episodes:
        uuid = episode_uuid(podcast.uuid, parsed.guid, parsed.enclosure_url)
        if uuid in seen:
            continue
        seen.add(uuid)
        episode = existing.get(uuid)
        if episode is None:
            episode = Episode(
                uuid=uuid,
                podcast_uuid=podcast.uuid,
                first_seen_at=now,
                published=parsed.published or now,
            )
            session.add(episode)
            inserted.append(episode)
        elif parsed.published is not None:
            episode.published = parsed.published
        _apply_episode(episode, parsed)
    return inserted


def _apply_channel(podcast: Podcast, feed: ParsedFeed) -> None:
    podcast.title = feed.title
    podcast.author = feed.author
    podcast.description = feed.description
    podcast.description_html = feed.description_html
    podcast.website_url = feed.website_url
    podcast.artwork_url = feed.artwork_url or podcast.artwork_url
    podcast.category = feed.category
    podcast.language = feed.language
    podcast.show_type = feed.show_type
    podcast.explicit = feed.explicit
    podcast.funding_url = feed.funding_url


def _apply_episode(episode: Episode, parsed: ParsedEpisode) -> None:
    episode.guid = parsed.guid
    episode.title = parsed.title
    episode.enclosure_url = parsed.enclosure_url
    episode.file_type = parsed.file_type
    episode.file_size = parsed.file_size
    episode.duration = parsed.duration
    episode.season = parsed.season
    episode.number = parsed.number
    episode.episode_type = parsed.episode_type
    episode.show_notes = parsed.show_notes
    episode.image_url = parsed.image_url
    episode.chapters_url = parsed.chapters_url
    episode.transcripts = parsed.transcripts
