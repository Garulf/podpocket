import asyncio

import pytest
from sqlalchemy import func, select

from podpocket.gpodder import router
from podpocket.ids import podcast_uuid
from podpocket.models import ChangeLog, Podcast, PodcastAlias, Subscription
from tests.factories import item, rss
from tests.gpodder import (
    FEED,
    basic,
    catalog_selects,
    ingest,
    seed_catalog,
    seeded_feed,
    subscribe,
    write_as,
)
from tests.records import records_of, sync_update

SUBSCRIPTIONS = "/api/2/subscriptions/alice/any-device.json"
OTHER_FEED = "https://feeds.test/other.xml"


async def read(client, since=0, device="any-device"):
    response = await client.get(
        f"/api/2/subscriptions/alice/{device}.json", params={"since": since}, headers=basic("alice")
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/json"
    return response.json()


async def upload(client, add=(), remove=()):
    return await client.post(
        SUBSCRIPTIONS, json={"add": list(add), "remove": list(remove)}, headers=basic("alice")
    )


async def test_lists_subscribed_feeds_for_any_device(client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)

    for device in ("music-assistant", "phone"):
        body = await read(client, device=device)
        assert (body["add"], body["remove"]) == ([FEED], [])
        assert body["timestamp"] > 0


async def test_unsubscribed_podcast_is_removed_even_since_zero(
    client, services, respx_mock, alice_auth
):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
    await subscribe(client, alice_auth, show, subscribed=False)

    body = await read(client, since=0)

    assert (body["add"], body["remove"]) == ([], [FEED])


async def test_moved_feed_lists_the_old_url_as_removed(client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, show)
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        podcast.feed_url = "https://feeds.test/moved.xml"
        await session.commit()
    await subscribe(client, alice_auth, show)

    body = await read(client)

    assert (body["add"], body["remove"]) == (["https://feeds.test/moved.xml"], [FEED])


async def test_since_returns_only_later_changes(client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    other = await ingest(respx_mock, services, OTHER_FEED, rss(item()))
    await subscribe(client, alice_auth, show)
    first = await read(client)
    await subscribe(client, alice_auth, other)

    later = await read(client, since=first["timestamp"])
    nothing = await read(client, since=later["timestamp"])

    assert (later["add"], later["remove"]) == ([OTHER_FEED], [])
    assert later["timestamp"] > first["timestamp"]
    assert (nothing["add"], nothing["remove"]) == ([], [])


async def test_podcast_unknown_to_the_catalog_is_skipped(client, alice_auth):
    await subscribe(client, alice_auth, "not-a-catalog-podcast")

    assert (await read(client))["add"] == []


async def test_add_subscribes_and_the_app_sees_it(client, services, respx_mock, alice, alice_auth):
    show = await ingest(respx_mock, services)
    before = (await sync_update(client, alice_auth)).last_modified

    response = await upload(client, add=[FEED])

    assert response.status_code == 200
    assert response.json() == {"timestamp": response.json()["timestamp"], "update_urls": []}
    podcasts = records_of(await sync_update(client, alice_auth, last_modified=before), "podcast")
    assert [(p.uuid, p.subscribed.value) for p in podcasts] == [(show, True)]
    async with services.sessionmaker() as session:
        sources = set(await session.scalars(select(ChangeLog.source)))
    assert "gpodder" in sources


async def test_add_of_an_unknown_feed_creates_a_stub(client, services, alice):
    response = await upload(client, add=["https://feeds.test/new.xml"])

    assert response.status_code == 200
    uuid = podcast_uuid("https://feeds.test/new.xml")
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, uuid)
        subscription = await session.get(Subscription, (alice.uuid, uuid))
    assert podcast.feed_url == "https://feeds.test/new.xml"
    assert podcast.fetched_at is None
    assert subscription.subscribed is True


async def test_remove_unsubscribes(client, services, respx_mock, alice, alice_auth):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)

    response = await upload(client, remove=[FEED])

    assert response.status_code == 200
    async with services.sessionmaker() as session:
        assert (await session.get(Subscription, (alice.uuid, show))).subscribed is False


async def test_rewritten_urls_are_reported_and_rejected_ones_are_not(client, alice):
    response = await upload(
        client, add=["HTTPS://Feeds.Test/a.xml", "ftp://feeds.test/b.xml", "https://feeds.test/c"]
    )

    assert response.json()["update_urls"] == [
        ["HTTPS://Feeds.Test/a.xml", "https://feeds.test/a.xml"]
    ]


async def test_over_long_urls_are_dropped_without_echo(client, alice):
    response = await upload(client, add=["https://feeds.test/" + "x" * 5000, "z" * 5000])

    assert response.status_code == 200
    assert response.json()["update_urls"] == []
    assert "xxxxxxxxxx" not in response.text
    assert "zzzzzzzzzz" not in response.text


async def test_removing_an_unknown_feed_is_a_no_op(client, alice):
    response = await upload(client, remove=["https://feeds.test/never-seen.xml"])

    assert response.status_code == 200


async def test_same_feed_added_and_removed_is_rejected(client, alice):
    response = await upload(client, add=[FEED], remove=[FEED])

    assert response.status_code == 400
    assert FEED not in response.text


@pytest.mark.parametrize(
    "body",
    [
        b'{"add": [{"secret": "xxxxxxxxxx"}]}',
        b'{"add": 5}',
        b'{"remove": "xxxxxxxxxx"}',
        b"[]",
        b"null",
        b'{"add": ["https://feeds.test/\\ud800xxxxxxxxxx"]}',
        b'{"add": ["https://feeds.test/\\u0000xxxxxxxxxx"]}',
    ],
)
async def test_malformed_changes_are_rejected_without_echo(client, alice, body):
    response = await client.post(SUBSCRIPTIONS, content=body, headers=basic("alice"))

    assert response.status_code == 400
    assert "xxxxxxxxxx" not in response.text


async def test_too_many_changes_are_rejected(client, alice):
    urls = [f"https://feeds.test/{n}.xml" for n in range(1001)]

    response = await upload(client, add=urls)

    assert response.status_code == 413


async def test_unsubscribed_moved_feed_lists_both_urls_as_removed(
    client, services, respx_mock, alice_auth
):
    show = await ingest(respx_mock, services)
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, show)
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        podcast.feed_url = "https://feeds.test/moved.xml"
        await session.commit()
    await subscribe(client, alice_auth, show)
    await subscribe(client, alice_auth, show, subscribed=False)

    body = await read(client)

    assert (body["add"], body["remove"]) == ([], ["https://feeds.test/moved.xml", FEED])


async def test_adding_the_new_url_and_removing_the_old_one_is_a_conflict(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    moved = "https://feeds.test/moved.xml"
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, show)
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        podcast.feed_url = moved
        await session.commit()
    await subscribe(client, alice_auth, show)

    response = await upload(client, add=[moved], remove=[FEED])

    assert response.status_code == 400
    assert moved not in response.text
    async with services.sessionmaker() as session:
        assert (await session.get(Subscription, (alice.uuid, show))).subscribed is True


async def test_a_rejected_upload_creates_no_stubs(client, services, respx_mock, alice, alice_auth):
    show = await ingest(respx_mock, services)
    moved = "https://feeds.test/moved.xml"
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, show)
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        podcast.feed_url = moved
        await session.commit()
    async with services.sessionmaker() as session:
        before = await session.scalar(select(func.count()).select_from(Podcast))

    response = await upload(client, add=[moved, "https://feeds.test/new.xml"], remove=[FEED])

    assert response.status_code == 400
    async with services.sessionmaker() as session:
        assert await session.scalar(select(func.count()).select_from(Podcast)) == before


async def test_a_large_upload_resolves_feeds_with_a_handful_of_queries(client, services, alice):
    await seed_catalog(services, feeds=300, episodes_per_feed=0)
    known = [seeded_feed(n) for n in range(300)]
    new = [f"https://feeds.test/new/{n}.xml" for n in range(300)]

    with catalog_selects(services) as selects:
        response = await upload(client, add=[*known, *new], remove=[])

    assert response.status_code == 200
    assert len(selects) <= 16
    async with services.sessionmaker() as session:
        subscribed = await session.scalar(
            select(func.count()).select_from(Subscription).where(Subscription.subscribed)
        )
    assert subscribed == 600


async def test_resolving_feeds_does_not_hold_the_write_lock(
    client, services, alice, bob, monkeypatch
):
    await seed_catalog(services, feeds=2, episodes_per_feed=0)
    plan_subscription_upload = router.plan_subscription_upload

    async def plan_then_let_bob_write(session, upload):
        plan = await plan_subscription_upload(session, upload)
        async with asyncio.timeout(30):
            await write_as(services, bob, podcast_uuid(seeded_feed(0)))
        return plan

    monkeypatch.setattr(router, "plan_subscription_upload", plan_then_let_bob_write)

    response = await upload(client, add=[seeded_feed(0), "https://feeds.test/new.xml"])

    assert response.status_code == 200
