import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket import ids
from podpocket.catalog import find_podcast_by_url
from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy, fetch
from podpocket.feeds.ingest import apply_feed
from podpocket.feeds.parse import parse_feed
from podpocket.models import Podcast, PodcastAlias

logger = logging.getLogger(__name__)

ACTIVE_WINDOW = timedelta(days=30)
MAX_BACKOFF = timedelta(hours=24)
MAX_BACKOFF_EXPONENT = 10
SCHEDULER_TICK_SECONDS = 60
REFRESH_CONCURRENCY = 8


def utc_now() -> datetime:
    return datetime.now(UTC)


class FeedRefresher:
    def __init__(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        policy: FetchPolicy,
        interval: timedelta,
        clock: Callable[[], datetime] = utc_now,
    ):
        self._sessionmaker = sessionmaker
        self._http = http
        self._policy = policy
        self._interval = interval
        self._clock = clock
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def refresh(self, podcast_uuid: str) -> int:
        async with self._locks[podcast_uuid], self._sessionmaker() as session:
            podcast = await session.get(Podcast, podcast_uuid)
            if podcast is None:
                raise LookupError(podcast_uuid)
            now = self._clock()
            try:
                new_episodes = await self._fetch_into(session, podcast, now)
            except FeedError as exc:
                await self._record_failure(session, podcast, now, exc)
                raise
            podcast.fetched_at = now
            podcast.failure_count = 0
            podcast.last_error = None
            podcast.next_refresh_at = now + self._interval
            await session.commit()
            return new_episodes

    async def resolve(self, raw_url: str) -> str:
        try:
            url = ids.normalize_feed_url(raw_url)
        except ValueError as exc:
            raise FeedError(str(exc)) from exc
        async with self._sessionmaker() as session:
            existing = await find_podcast_by_url(session, url)
            if existing is not None and existing.fetched_at is not None:
                return existing.uuid
            created = existing is None
            uuid = ids.podcast_uuid(url) if created else existing.uuid
            if created:
                session.add(Podcast(uuid=uuid, feed_url=url))
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    created = False
        try:
            await self.refresh(uuid)
        except FeedError:
            if created:
                await self._discard_unfetched(uuid)
            raise
        return uuid

    async def ensure_fetched(self, podcast_uuid: str) -> Podcast | None:
        async with self._sessionmaker() as session:
            podcast = await session.get(Podcast, podcast_uuid)
        if podcast is None or podcast.fetched_at is not None:
            return podcast
        await self.refresh(podcast_uuid)
        async with self._sessionmaker() as session:
            return await session.get(Podcast, podcast_uuid)

    async def touch(self, podcast_uuids: Iterable[str]) -> None:
        uuids = list(podcast_uuids)
        if not uuids:
            return
        async with self._sessionmaker() as session:
            await session.execute(
                update(Podcast)
                .where(Podcast.uuid.in_(uuids))
                .values(last_requested_at=self._clock())
            )
            await session.commit()

    async def refresh_due(self) -> int:
        now = self._clock()
        async with self._sessionmaker() as session:
            due = list(
                await session.scalars(
                    select(Podcast.uuid).where(
                        Podcast.last_requested_at >= now - ACTIVE_WINDOW,
                        or_(Podcast.next_refresh_at.is_(None), Podcast.next_refresh_at <= now),
                    )
                )
            )
        limit = asyncio.Semaphore(REFRESH_CONCURRENCY)

        async def refresh_one(uuid: str) -> None:
            async with limit:
                try:
                    await self.refresh(uuid)
                except FeedError as exc:
                    logger.warning("refresh of %s failed: %s", uuid, exc)
                except Exception:
                    logger.exception("refresh of %s failed unexpectedly", uuid)

        await asyncio.gather(*(refresh_one(uuid) for uuid in due))
        return len(due)

    async def run_forever(self) -> None:
        while True:
            try:
                await self.refresh_due()
            except Exception:
                logger.exception("scheduled refresh failed")
            await asyncio.sleep(SCHEDULER_TICK_SECONDS)

    async def _fetch_into(self, session: AsyncSession, podcast: Podcast, now: datetime) -> int:
        result = await fetch(
            self._http,
            podcast.feed_url,
            self._policy,
            etag=podcast.etag,
            last_modified=podcast.last_modified,
        )
        inserted = []
        if result.content is not None:
            feed = await asyncio.to_thread(parse_feed, result.content)
            inserted = await apply_feed(session, podcast, feed, now=now)
            podcast.etag = result.etag
            podcast.last_modified = result.last_modified
        if result.moved_permanently_to:
            await self._move(session, podcast, result.moved_permanently_to)
        return len(inserted)

    async def _move(self, session: AsyncSession, podcast: Podcast, new_url: str) -> None:
        try:
            target = ids.normalize_feed_url(new_url)
        except ValueError:
            return
        if target == podcast.feed_url:
            return
        owner = await find_podcast_by_url(session, target)
        if owner is not None and owner.uuid != podcast.uuid:
            return
        await session.merge(PodcastAlias(url=podcast.feed_url, podcast_uuid=podcast.uuid))
        podcast.feed_url = target

    async def _record_failure(
        self, session: AsyncSession, podcast: Podcast, now: datetime, error: FeedError
    ) -> None:
        podcast.failure_count += 1
        podcast.last_error = str(error)
        exponent = min(podcast.failure_count, MAX_BACKOFF_EXPONENT)
        podcast.next_refresh_at = now + min(self._interval * 2**exponent, MAX_BACKOFF)
        await session.commit()

    async def _discard_unfetched(self, podcast_uuid: str) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                delete(Podcast).where(Podcast.uuid == podcast_uuid, Podcast.fetched_at.is_(None))
            )
            await session.commit()
