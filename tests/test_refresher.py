import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from podpocket.catalog import find_podcast_by_url, list_episodes
from podpocket.feeds import refresher as refresher_module
from podpocket.feeds.errors import FeedError
from podpocket.feeds.fetch import FetchPolicy
from podpocket.feeds.refresher import FeedRefresher
from podpocket.ids import episode_uuid, podcast_uuid
from podpocket.models import Episode, Podcast
from tests.factories import FakeClock, item, rss

FEED = "https://feeds.test/show.xml"
START = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock():
    return FakeClock(START)


@pytest.fixture
async def refresher(services, clock):
    async with httpx.AsyncClient() as http:
        yield FeedRefresher(
            services.sessionmaker,
            http,
            FetchPolicy(max_bytes=10_000_000, allow_private=True),
            timedelta(minutes=30),
            clock=clock,
        )


async def episode_guids(services, uuid):
    async with services.sessionmaker() as session:
        return [episode.guid for episode in await list_episodes(session, uuid)]


async def load_podcast(services, uuid):
    async with services.sessionmaker() as session:
        return await session.get(Podcast, uuid)


async def test_resolve_creates_and_fetches_podcast(respx_mock, refresher, services):
    respx_mock.get(FEED).respond(
        200,
        content=rss(
            item(guid="ep-2", published="Tue, 13 Oct 2026 06:00:00 GMT"),
            item(guid="ep-1"),
            title="My Show",
        ),
    )

    uuid = await refresher.resolve(FEED)

    podcast = await load_podcast(services, uuid)
    assert uuid == podcast_uuid(FEED)
    assert podcast.title == "My Show"
    assert podcast.fetched_at == START
    assert podcast.next_refresh_at == START + timedelta(minutes=30)
    assert await episode_guids(services, uuid) == ["ep-2", "ep-1"]


async def test_resolve_reuses_fetched_podcast(respx_mock, refresher):
    route = respx_mock.get(FEED).respond(200, content=rss(item()))

    first = await refresher.resolve(FEED)
    second = await refresher.resolve("https://FEEDS.test/show.xml#latest")

    assert first == second
    assert route.call_count == 1


async def test_resolve_of_non_feed_leaves_no_podcast(respx_mock, refresher, services):
    respx_mock.get("https://example.test/page").respond(
        200, content=b"<html><body>hi</body></html>"
    )

    with pytest.raises(FeedError, match="not a podcast feed"):
        await refresher.resolve("https://example.test/page")

    async with services.sessionmaker() as session:
        assert (await session.scalars(select(Podcast))).all() == []


async def test_resolve_rejects_non_urls(refresher):
    with pytest.raises(FeedError):
        await refresher.resolve("not a url")


async def test_refresh_adds_new_episodes_and_never_deletes(respx_mock, refresher, services):
    route = respx_mock.get(FEED)
    route.respond(
        200,
        content=rss(
            item(guid="ep-2", published="Tue, 13 Oct 2026 06:00:00 GMT"), item(guid="ep-1")
        ),
    )
    uuid = await refresher.resolve(FEED)

    route.respond(
        200,
        content=rss(
            item(guid="ep-3", published="Wed, 14 Oct 2026 06:00:00 GMT"),
            item(guid="ep-2", published="Tue, 13 Oct 2026 06:00:00 GMT"),
        ),
    )
    new_count = await refresher.refresh(uuid)

    assert new_count == 1
    assert await episode_guids(services, uuid) == ["ep-3", "ep-2", "ep-1"]


async def test_duplicate_guids_are_stored_once(respx_mock, refresher, services):
    respx_mock.get(FEED).respond(
        200, content=rss(item(guid="same", title="Newer"), item(guid="same", title="Older"))
    )

    uuid = await refresher.resolve(FEED)

    async with services.sessionmaker() as session:
        episodes = await list_episodes(session, uuid)
    assert [episode.title for episode in episodes] == ["Newer"]


async def test_missing_pubdate_uses_first_seen_time(respx_mock, refresher, services):
    respx_mock.get(FEED).respond(200, content=rss(item(published=None)))

    uuid = await refresher.resolve(FEED)

    async with services.sessionmaker() as session:
        episode = (await list_episodes(session, uuid))[0]
    assert episode.published == START


async def test_not_modified_keeps_episodes_and_sends_validators(
    respx_mock, refresher, services, clock
):
    route = respx_mock.get(FEED)
    route.respond(200, content=rss(item()), headers={"ETag": '"v1"'})
    uuid = await refresher.resolve(FEED)
    route.respond(304)
    clock.advance(timedelta(hours=1))

    assert await refresher.refresh(uuid) == 0

    assert route.calls.last.request.headers["If-None-Match"] == '"v1"'
    assert await episode_guids(services, uuid) == ["ep-1"]
    podcast = await load_podcast(services, uuid)
    assert podcast.next_refresh_at == START + timedelta(hours=1, minutes=30)


async def test_permanent_redirect_keeps_uuid_and_aliases_old_url(respx_mock, refresher, services):
    new_url = "https://new-host.test/show.xml"
    route = respx_mock.get(FEED)
    route.respond(200, content=rss(item()))
    uuid = await refresher.resolve(FEED)
    route.respond(301, headers={"Location": new_url})
    respx_mock.get(new_url).respond(200, content=rss(item()))

    await refresher.refresh(uuid)

    podcast = await load_podcast(services, uuid)
    assert podcast.feed_url == new_url
    async with services.sessionmaker() as session:
        assert (await find_podcast_by_url(session, FEED)).uuid == uuid
        assert (await find_podcast_by_url(session, new_url)).uuid == uuid
    assert await refresher.resolve(FEED) == uuid


async def test_failures_back_off_and_success_resets(respx_mock, refresher, services, clock):
    route = respx_mock.get(FEED)
    route.respond(200, content=rss(item()))
    uuid = await refresher.resolve(FEED)

    route.respond(500)
    with pytest.raises(FeedError):
        await refresher.refresh(uuid)
    first = await load_podcast(services, uuid)
    assert (first.failure_count, first.next_refresh_at) == (1, START + timedelta(minutes=60))
    assert "500" in first.last_error

    with pytest.raises(FeedError):
        await refresher.refresh(uuid)
    second = await load_podcast(services, uuid)
    assert second.next_refresh_at == START + timedelta(minutes=120)

    route.respond(200, content=rss(item()))
    await refresher.refresh(uuid)
    recovered = await load_podcast(services, uuid)
    assert (recovered.failure_count, recovered.last_error) == (0, None)


async def test_backoff_is_capped_at_a_day(respx_mock, refresher, services):
    route = respx_mock.get(FEED)
    route.respond(200, content=rss(item()))
    uuid = await refresher.resolve(FEED)
    route.respond(500)

    for _ in range(12):
        with pytest.raises(FeedError):
            await refresher.refresh(uuid)

    podcast = await load_podcast(services, uuid)
    assert podcast.next_refresh_at == START + timedelta(hours=24)


async def test_refresh_due_only_touches_active_due_podcasts(respx_mock, refresher, services, clock):
    active_url = "https://feeds.test/active"
    active_uuid = podcast_uuid(active_url)
    later_uuid = podcast_uuid("https://feeds.test/later")
    async with services.sessionmaker() as session:
        session.add_all(
            [
                Podcast(uuid=active_uuid, feed_url=active_url),
                Podcast(
                    uuid=podcast_uuid("https://feeds.test/idle"), feed_url="https://feeds.test/idle"
                ),
                Podcast(
                    uuid=later_uuid,
                    feed_url="https://feeds.test/later",
                    next_refresh_at=START + timedelta(hours=1),
                ),
            ]
        )
        await session.commit()
    await refresher.touch([active_uuid, later_uuid])
    active = respx_mock.get(active_url).respond(200, content=rss(item()))

    assert await refresher.refresh_due() == 1

    assert active.call_count == 1


async def test_refresh_due_survives_an_unexpected_error_for_one_podcast(
    respx_mock, refresher, services, monkeypatch
):
    broken_url = "https://feeds.test/broken"
    healthy_url = "https://feeds.test/healthy"
    broken_uuid, healthy_uuid = podcast_uuid(broken_url), podcast_uuid(healthy_url)
    async with services.sessionmaker() as session:
        session.add_all(
            [
                Podcast(uuid=broken_uuid, feed_url=broken_url),
                Podcast(uuid=healthy_uuid, feed_url=healthy_url),
            ]
        )
        await session.commit()
    await refresher.touch([broken_uuid, healthy_uuid])
    healthy = respx_mock.get(healthy_url).respond(200, content=rss(item()))
    original_refresh = refresher.refresh

    async def refresh(uuid):
        if uuid == broken_uuid:
            raise LookupError(uuid)
        return await original_refresh(uuid)

    monkeypatch.setattr(refresher, "refresh", refresh)

    assert await refresher.refresh_due() == 2

    assert healthy.call_count == 1


async def test_ensure_fetched_fetches_stubs(respx_mock, refresher, services):
    async with services.sessionmaker() as session:
        session.add(Podcast(uuid=podcast_uuid(FEED), feed_url=FEED, title="From directory"))
        await session.commit()
    respx_mock.get(FEED).respond(200, content=rss(item(), title="Real Title"))

    podcast = await refresher.ensure_fetched(podcast_uuid(FEED))

    assert podcast.title == "Real Title"
    assert podcast.fetched_at == START
    assert await refresher.ensure_fetched("missing") is None


async def test_touch_marks_podcasts_requested(refresher, services):
    async with services.sessionmaker() as session:
        session.add(Podcast(uuid="p", feed_url=FEED))
        await session.commit()

    await refresher.touch(["p", "unknown"])

    assert (await load_podcast(services, "p")).last_requested_at == START


async def test_episode_rows_use_stable_ids(respx_mock, refresher, services):
    respx_mock.get(FEED).respond(200, content=rss(item(guid="ep-1")))

    uuid = await refresher.resolve(FEED)

    async with services.sessionmaker() as session:
        episode = await session.scalar(select(Episode).where(Episode.podcast_uuid == uuid))

    assert episode.uuid == episode_uuid(uuid, "ep-1", "https://cdn.example.com/1.mp3")


TOO_BIG = "100000000000000000000"


async def test_blank_category_and_oversized_numbers_are_ingested(respx_mock, refresher, services):
    respx_mock.get(FEED).respond(
        200,
        content=rss(
            item(extra=f"<itunes:episode>{TOO_BIG}</itunes:episode>"),
            channel="<itunes:category/>",
        ),
    )

    uuid = await refresher.resolve(FEED)

    podcast = await load_podcast(services, uuid)
    async with services.sessionmaker() as session:
        episode = (await list_episodes(session, uuid))[0]
    assert (podcast.category, episode.number) == ("", None)


INGEST_FAILURES = {
    "null category": lambda feed: replace(feed, category=None),
    "oversized number": lambda feed: replace(
        feed, episodes=[replace(episode, number=10**20) for episode in feed.episodes]
    ),
}


def break_ingest(monkeypatch, corrupt):
    real_parse = refresher_module.parse_feed
    monkeypatch.setattr(
        refresher_module, "parse_feed", lambda content: corrupt(real_parse(content))
    )


@pytest.mark.parametrize("corrupt", INGEST_FAILURES.values(), ids=INGEST_FAILURES.keys())
async def test_unexpected_ingest_error_on_resolve_leaves_no_podcast(
    respx_mock, refresher, services, monkeypatch, corrupt
):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    break_ingest(monkeypatch, corrupt)

    with pytest.raises(FeedError, match="could not process"):
        await refresher.resolve(FEED)

    async with services.sessionmaker() as session:
        assert (await session.scalars(select(Podcast))).all() == []


@pytest.mark.parametrize("corrupt", INGEST_FAILURES.values(), ids=INGEST_FAILURES.keys())
async def test_unexpected_ingest_error_on_refresh_backs_off(
    respx_mock, refresher, services, monkeypatch, corrupt
):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    uuid = await refresher.resolve(FEED)
    break_ingest(monkeypatch, corrupt)

    with pytest.raises(FeedError, match="could not process"):
        await refresher.refresh(uuid)

    podcast = await load_podcast(services, uuid)
    assert (podcast.failure_count, podcast.next_refresh_at) == (1, START + timedelta(minutes=60))
    assert "could not process" in podcast.last_error
    assert podcast.category == ""
    assert await episode_guids(services, uuid) == ["ep-1"]


async def add_stubs(services, *urls):
    async with services.sessionmaker() as session:
        session.add_all(Podcast(uuid=podcast_uuid(url), feed_url=url) for url in urls)
        await session.commit()


async def test_blocked_fetches_hold_no_database_connections(
    respx_mock, refresher, services, client
):
    release = asyncio.Event()
    started = 0

    async def blocked(request):
        nonlocal started
        started += 1
        await release.wait()
        return httpx.Response(200, content=rss(item()))

    urls = [f"https://feeds.test/{n}.xml" for n in range(20)]
    await add_stubs(services, *urls)
    respx_mock.get(url__startswith="https://feeds.test/").mock(side_effect=blocked)
    refreshes = [asyncio.create_task(refresher.refresh(podcast_uuid(url))) for url in urls]
    try:
        async with asyncio.timeout(5):
            while started < len(urls):
                await asyncio.sleep(0.01)
        assert services.sessionmaker.kw["bind"].pool.checkedout() == 0
        health = await asyncio.wait_for(client.get("/health"), timeout=5)
        assert health.status_code == 200
    finally:
        release.set()
        await asyncio.gather(*refreshes)


async def test_resolve_of_url_moved_to_a_known_podcast_returns_the_owner(
    respx_mock, refresher, services
):
    respx_mock.get(FEED).respond(200, content=rss(item()))
    owner = await refresher.resolve(FEED)
    insecure = "http://feeds.test/show.xml"
    respx_mock.get(insecure).respond(301, headers={"Location": FEED})

    assert await refresher.resolve(insecure) == owner

    async with services.sessionmaker() as session:
        assert list(await session.scalars(select(Podcast.uuid))) == [owner]
        assert len((await session.scalars(select(Episode))).all()) == 1


async def test_ensure_fetched_respects_backoff_of_a_failing_stub(
    respx_mock, refresher, services, clock
):
    await add_stubs(services, FEED)
    route = respx_mock.get(FEED).respond(500)

    with pytest.raises(FeedError):
        await refresher.ensure_fetched(podcast_uuid(FEED))
    with pytest.raises(FeedError, match="500"):
        await refresher.ensure_fetched(podcast_uuid(FEED))
    assert route.call_count == 1

    clock.advance(timedelta(minutes=61))
    route.respond(200, content=rss(item()))
    podcast = await refresher.ensure_fetched(podcast_uuid(FEED))

    assert podcast.fetched_at is not None
    assert route.call_count == 2


async def test_concurrent_first_hits_fetch_a_stub_once(respx_mock, refresher, services):
    await add_stubs(services, FEED)

    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=rss(item()))

    route = respx_mock.get(FEED).mock(side_effect=slow)

    first, second = await asyncio.gather(
        refresher.ensure_fetched(podcast_uuid(FEED)), refresher.ensure_fetched(podcast_uuid(FEED))
    )

    assert first.fetched_at is not None and second.fetched_at is not None
    assert route.call_count == 1
