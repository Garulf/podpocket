from sqlalchemy import or_, select
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


async def find_episode(
    session: AsyncSession, podcast_uuid: str, episode_uuid: str
) -> Episode | None:
    episode = await session.get(Episode, episode_uuid)
    return episode if episode is not None and episode.podcast_uuid == podcast_uuid else None


def contains(column, term: str):
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return column.ilike(f"%{escaped}%", escape="\\")


async def search_episodes(
    session: AsyncSession,
    term: str,
    *,
    podcast_uuid: str | None = None,
    limit: int | None = 50,
) -> list[tuple[Episode, Podcast]]:
    query = (
        select(Episode, Podcast)
        .join(Podcast, Podcast.uuid == Episode.podcast_uuid)
        .where(or_(contains(Episode.title, term), contains(Episode.show_notes, term)))
        .order_by(Episode.published.desc())
        .limit(limit)
    )
    if podcast_uuid is not None:
        query = query.where(Episode.podcast_uuid == podcast_uuid)
    return [(episode, podcast) for episode, podcast in (await session.execute(query)).all()]
