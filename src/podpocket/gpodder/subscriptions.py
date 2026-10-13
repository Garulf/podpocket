from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from podpocket.catalog import resolve_podcast_uuids
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


@dataclass(frozen=True)
class SubscriptionPlan:
    subscribe: list[str]
    unsubscribe: list[str]
    new_feeds: list[str]


def _require_disjoint(subscribe: Iterable[str], unsubscribe: Iterable[str]) -> None:
    if set(subscribe) & set(unsubscribe):
        raise AddedAndRemoved("a feed was both added and removed")


async def plan_subscription_upload(
    session: AsyncSession, upload: SubscriptionUpload
) -> SubscriptionPlan:
    known = await resolve_podcast_uuids(session, [*upload.add, *upload.remove])
    plan = SubscriptionPlan(
        subscribe=sorted({known[url] for url in upload.add if url in known}),
        unsubscribe=sorted({known[url] for url in upload.remove if url in known}),
        new_feeds=[url for url in upload.add if url not in known],
    )
    _require_disjoint(
        [*plan.subscribe, *(podcast_uuid(url) for url in plan.new_feeds)], plan.unsubscribe
    )
    return plan


async def _create_podcasts(session: AsyncSession, urls: list[str]) -> list[str]:
    known = await resolve_podcast_uuids(session, urls)
    for url in urls:
        if url not in known:
            known[url] = podcast_uuid(url)
            session.add(Podcast(uuid=known[url], feed_url=url))
    await session.flush()
    return [known[url] for url in urls]


async def apply_subscription_plan(writer: ChangeWriter, plan: SubscriptionPlan) -> None:
    created = await _create_podcasts(writer.session, plan.new_feeds)
    subscribe = sorted({*plan.subscribe, *created})
    _require_disjoint(subscribe, plan.unsubscribe)
    changes = [PodcastChange(podcast_uuid=uuid, subscribed=True) for uuid in subscribe]
    changes += [PodcastChange(podcast_uuid=uuid, subscribed=False) for uuid in plan.unsubscribe]
    await apply_podcast_changes(writer, changes)
