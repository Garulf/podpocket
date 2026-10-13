from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.catalog import find_podcast_by_url
from podpocket.gpodder.limits import MAX_URL_LENGTH
from podpocket.ids import normalize_feed_url, podcast_uuid
from podpocket.models import ChangeLog, Podcast, PodcastAlias, Subscription
from podpocket.sync.podcasts import PodcastChange, apply_podcast_changes
from podpocket.sync.writer import PODCAST, ChangeWriter


class AddedAndRemoved(ValueError):
    pass


@dataclass(frozen=True)
class SubscriptionDelta:
    add: list[str]
    remove: list[str]


@dataclass(frozen=True)
class SubscriptionUpload:
    add: list[str]
    remove: list[str]
    update_urls: list[list[str]]


async def subscription_delta(
    session: AsyncSession, user_uuid: str, since_ms: int, cursor_ms: int
) -> SubscriptionDelta:
    rows = (
        select(Subscription.subscribed, Podcast.feed_url)
        .join(Podcast, Podcast.uuid == Subscription.podcast_uuid)
        .where(Subscription.user_uuid == user_uuid)
    )
    aliases = (
        select(PodcastAlias.url)
        .join(Subscription, Subscription.podcast_uuid == PodcastAlias.podcast_uuid)
        .where(Subscription.user_uuid == user_uuid)
    )
    if since_ms > 0:
        changed = select(ChangeLog.entity_id).where(
            ChangeLog.user_uuid == user_uuid,
            ChangeLog.entity == PODCAST,
            ChangeLog.field == "subscribed",
            ChangeLog.server_ms > since_ms,
            ChangeLog.server_ms <= cursor_ms,
        )
        rows = rows.where(Subscription.podcast_uuid.in_(changed))
        aliases = aliases.where(Subscription.podcast_uuid.in_(changed))
    add: set[str] = set()
    remove: set[str] = set()
    for subscribed, feed_url in await session.execute(rows):
        (add if subscribed else remove).add(feed_url)
    remove.update(await session.scalars(aliases))
    return SubscriptionDelta(add=sorted(add), remove=sorted(remove - add))


def _cleaned(raw: str, update_urls: list[list[str]]) -> str:
    if len(raw) > MAX_URL_LENGTH:
        return ""
    try:
        url = normalize_feed_url(raw)
    except ValueError:
        return ""
    if url != raw:
        update_urls.append([raw, url])
    return url


def clean_upload(add: Iterable[str], remove: Iterable[str]) -> SubscriptionUpload:
    update_urls: list[list[str]] = []
    added = {url for raw in add if (url := _cleaned(raw, update_urls))}
    removed = {url for raw in remove if (url := _cleaned(raw, update_urls))}
    if added & removed:
        raise AddedAndRemoved("a feed was both added and removed")
    return SubscriptionUpload(sorted(added), sorted(removed), update_urls)


async def ensure_podcasts(sessionmaker: async_sessionmaker[AsyncSession], urls: list[str]) -> None:
    for attempt in range(2):
        async with sessionmaker() as session:
            try:
                for url in urls:
                    if await find_podcast_by_url(session, url) is None:
                        session.add(Podcast(uuid=podcast_uuid(url), feed_url=url))
                        await session.flush()
                await session.commit()
                return
            except IntegrityError:
                if attempt:
                    raise


async def _resolve(session: AsyncSession, urls: list[str]) -> list[str]:
    uuids: list[str] = []
    for url in urls:
        podcast = await find_podcast_by_url(session, url)
        if podcast is not None:
            uuids.append(podcast.uuid)
    return uuids


async def apply_subscription_upload(writer: ChangeWriter, upload: SubscriptionUpload) -> None:
    added = await _resolve(writer.session, upload.add)
    removed = await _resolve(writer.session, upload.remove)
    if set(added) & set(removed):
        raise AddedAndRemoved("a feed was both added and removed")
    changes = [PodcastChange(podcast_uuid=uuid, subscribed=True) for uuid in added]
    changes += [PodcastChange(podcast_uuid=uuid, subscribed=False) for uuid in removed]
    await apply_podcast_changes(writer, changes)
