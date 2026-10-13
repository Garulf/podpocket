import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket import ids
from podpocket.catalog import find_podcast_by_url
from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy, FetchResult, fetch
from podpocket.feeds.ingest import apply_feed
from podpocket.feeds.parse import ParsedFeed, parse_feed
from podpocket.models import Podcast, PodcastAlias, Subscription

logger = logging.getLogger(__name__)

ACTIVE_WINDOW = timedelta(days=30)
MAX_BACKOFF = timedelta(hours=24)
MAX_BACKOFF_EXPONENT = 10
SCHEDULER_TICK_SECONDS = 60
REFRESH_CONCURRENCY = 8


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Download:
    result: FetchResult
    feed: ParsedFeed | None


@dataclass(frozen=True)
class RefreshOutcome:
    new_episodes: int
    moved_to_owner: str | None


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
        return (await self._refresh_exclusively(podcast_uuid)).new_episodes

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
            outcome = await self._refresh_exclusively(uuid)
        except FeedError:
            if created:
                await self._discard(uuid, only_unfetched=True)
            raise
        if created and outcome.moved_to_owner is not None:
            await self._discard(uuid)
            return outcome.moved_to_owner
        return uuid

    async def ensure_fetched(self, podcast_uuid: str) -> Podcast | None:
        podcast = await self._load(podcast_uuid)
        if podcast is None or podcast.fetched_at is not None:
            return podcast
        async with self._locks[podcast_uuid]:
            podcast = await self._load(podcast_uuid)
            if podcast is None or podcast.fetched_at is not None:
                return podcast
            if self._backing_off(podcast):
                raise FeedError(podcast.last_error or "feed unavailable")
            await self._refresh_locked(podcast_uuid)
        return await self._load(podcast_uuid)

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
        subscribed = select(Subscription.podcast_uuid).where(Subscription.subscribed.is_(True))
        async with self._sessionmaker() as session:
            due = list(
                await session.scalars(
                    select(Podcast.uuid).where(
                        or_(
                            Podcast.last_requested_at >= now - ACTIVE_WINDOW,
                            Podcast.uuid.in_(subscribed),
                        ),
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

    async def _load(self, podcast_uuid: str) -> Podcast | None:
        async with self._sessionmaker() as session:
            return await session.get(Podcast, podcast_uuid)

    def _backing_off(self, podcast: Podcast) -> bool:
        return (
            podcast.failure_count > 0
            and podcast.next_refresh_at is not None
            and podcast.next_refresh_at > self._clock()
        )

    async def _refresh_exclusively(self, podcast_uuid: str) -> RefreshOutcome:
        async with self._locks[podcast_uuid]:
            return await self._refresh_locked(podcast_uuid)

    async def _refresh_locked(self, podcast_uuid: str) -> RefreshOutcome:
        source = await self._load(podcast_uuid)
        if source is None:
            raise LookupError(podcast_uuid)
        now = self._clock()
        try:
            download = await self._download(source)
            return await self._apply(podcast_uuid, download, now)
        except FeedError as exc:
            await self._record_failure(podcast_uuid, now, str(exc))
            raise
        except Exception as exc:
            logger.exception("refresh of %s failed unexpectedly", podcast_uuid)
            error = FeedError(f"could not process {source.feed_url}: {type(exc).__name__}")
            await self._record_failure(podcast_uuid, now, str(error))
            raise error from exc

    async def _download(self, source: Podcast) -> Download:
        result = await fetch(
            self._http,
            source.feed_url,
            self._policy,
            etag=source.etag,
            last_modified=source.last_modified,
        )
        feed = None
        if result.content is not None:
            feed = await asyncio.to_thread(parse_feed, result.content)
        return Download(result, feed)

    async def _apply(self, podcast_uuid: str, download: Download, now: datetime) -> RefreshOutcome:
        async with self._sessionmaker() as session:
            podcast = await session.get(Podcast, podcast_uuid)
            if podcast is None:
                return RefreshOutcome(new_episodes=0, moved_to_owner=None)
            inserted = []
            if download.feed is not None:
                inserted = await apply_feed(session, podcast, download.feed, now=now)
                podcast.etag = download.result.etag
                podcast.last_modified = download.result.last_modified
            moved_to_owner = None
            if download.result.moved_permanently_to:
                moved_to_owner = await self._move(
                    session, podcast, download.result.moved_permanently_to
                )
            podcast.fetched_at = now
            podcast.failure_count = 0
            podcast.last_error = None
            podcast.next_refresh_at = now + self._interval
            await session.commit()
            return RefreshOutcome(new_episodes=len(inserted), moved_to_owner=moved_to_owner)

    async def _move(self, session: AsyncSession, podcast: Podcast, new_url: str) -> str | None:
        try:
            target = ids.normalize_feed_url(new_url)
        except ValueError:
            return None
        if target == podcast.feed_url:
            return None
        owner = await find_podcast_by_url(session, target)
        if owner is not None and owner.uuid != podcast.uuid:
            return owner.uuid
        await session.merge(PodcastAlias(url=podcast.feed_url, podcast_uuid=podcast.uuid))
        podcast.feed_url = target
        return None

    async def _record_failure(self, podcast_uuid: str, now: datetime, error: str) -> None:
        async with self._sessionmaker() as session:
            podcast = await session.get(Podcast, podcast_uuid)
            if podcast is None:
                return
            podcast.failure_count += 1
            podcast.last_error = error
            exponent = min(podcast.failure_count, MAX_BACKOFF_EXPONENT)
            podcast.next_refresh_at = now + min(self._interval * 2**exponent, MAX_BACKOFF)
            await session.commit()

    async def _discard(self, podcast_uuid: str, *, only_unfetched: bool = False) -> None:
        statement = delete(Podcast).where(Podcast.uuid == podcast_uuid)
        if only_unfetched:
            statement = statement.where(Podcast.fetched_at.is_(None))
        async with self._sessionmaker() as session:
            await session.execute(statement)
            await session.commit()
