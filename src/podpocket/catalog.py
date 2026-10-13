from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.directory.itunes import DirectoryPodcast
from podpocket.ids import normalize_feed_url, podcast_uuid
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


@dataclass(frozen=True)
class PodcastSummary:
    uuid: str
    title: str
    author: str


async def search_podcasts(session: AsyncSession, term: str, limit: int = 10) -> list[Podcast]:
    return list(
        await session.scalars(
            select(Podcast)
            .where(
                Podcast.fetched_at.is_not(None),
                or_(contains(Podcast.title, term), contains(Podcast.author, term)),
            )
            .order_by(Podcast.title)
            .limit(limit)
        )
    )


async def upsert_stubs(session: AsyncSession, items: Iterable[DirectoryPodcast]) -> list[Podcast]:
    podcasts: dict[str, Podcast] = {}
    for found in items:
        try:
            url = normalize_feed_url(found.feed_url)
        except ValueError:
            continue
        podcast = await find_podcast_by_url(session, url)
        if podcast is None:
            podcast = Podcast(uuid=podcast_uuid(url), feed_url=url)
            session.add(podcast)
        if podcast.fetched_at is None:
            podcast.title = found.title
            podcast.author = found.author
            podcast.artwork_url = found.artwork_url
            podcast.category = found.category
        podcasts.setdefault(podcast.uuid, podcast)
    await session.flush()
    return list(podcasts.values())


async def save_stubs(
    sessionmaker: async_sessionmaker[AsyncSession], items: Iterable[DirectoryPodcast]
) -> list[PodcastSummary]:
    items = list(items)
    for attempt in range(2):
        async with sessionmaker() as session:
            try:
                podcasts = await upsert_stubs(session, items)
                summaries = [PodcastSummary(p.uuid, p.title, p.author) for p in podcasts]
                await session.commit()
                return summaries
            except IntegrityError:
                if attempt:
                    raise
    return []


async def episodes_after(
    session: AsyncSession, podcast_uuid: str, last_episode_uuid: str | None
) -> list[Episode]:
    if last_episode_uuid is None:
        return list(await session.scalars(newest_first(podcast_uuid)))
    last = await find_episode(session, podcast_uuid, last_episode_uuid)
    if last is None:
        return list(await session.scalars(newest_first(podcast_uuid).limit(1)))
    return list(
        await session.scalars(
            newest_first(podcast_uuid).where(
                Episode.published >= last.published, Episode.uuid != last.uuid
            )
        )
    )


async def newest_episode(session: AsyncSession, podcast_uuid: str) -> Episode | None:
    return await session.scalar(newest_first(podcast_uuid).limit(1))
