from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.models import Episode, Podcast, PodcastAlias


async def find_podcast_by_url(session: AsyncSession, normalized_url: str) -> Podcast | None:
    podcast = await session.scalar(select(Podcast).where(Podcast.feed_url == normalized_url))
    if podcast is not None:
        return podcast
    return await session.scalar(
        select(Podcast)
        .join(PodcastAlias, PodcastAlias.podcast_uuid == Podcast.uuid)
        .where(PodcastAlias.url == normalized_url)
    )


def newest_first(podcast_uuid: str):
    return (
        select(Episode)
        .where(Episode.podcast_uuid == podcast_uuid)
        .order_by(Episode.published.desc(), Episode.first_seen_at.desc())
    )


async def list_episodes(session: AsyncSession, podcast_uuid: str) -> list[Episode]:
    return list(await session.scalars(newest_first(podcast_uuid)))
