import asyncio
import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from podpocket.gpodder import router
from podpocket.ids import episode_uuid, podcast_uuid
from podpocket.models import ChangeLog, Podcast, PodcastAlias
from podpocket.timefmt import epoch_ms, now_ms
from tests.gpodder import (
    EPISODE_1,
    EPISODE_2,
    FEED,
    basic,
    catalog_selects,
    change_log_inserts,
    episode_id,
    episode_state,
    ingest,
    post_actions,
    seed_catalog,
    seeded_feed,
    seeded_guid,
    seeded_url,
    write_as,
)
from tests.records import episode_upload, sync_update

STAMP = "2026-10-12T08:00:00"
STAMP_MS = epoch_ms(datetime(2026, 10, 12, 8, 0, tzinfo=UTC))


def play(position, total=3600, episode=EPISODE_1, podcast=FEED, timestamp=STAMP, **extra):
    return {
        "podcast": podcast,
        "episode": episode,
        "action": "play",
        "timestamp": timestamp,
        "started": 0,
        "position": position,
        "total": total,
        **extra,
    }


async def test_play_sets_progress_with_the_action_time(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    response = await post_actions(client, "alice", [play(120)])

    assert response.status_code == 200
    assert response.json()["update_urls"] == []
    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.podcast_uuid, state.played_up_to, state.playing_status, state.duration) == (
        show,
        120,
        2,
        3600,
    )
    assert state.played_up_to_modified == STAMP_MS
    async with services.sessionmaker() as session:
        sources = set(
            await session.scalars(select(ChangeLog.source).where(ChangeLog.entity == "episode"))
        )
    assert sources == {"gpodder"}


async def test_play_to_the_end_marks_played(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(3600)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (3600, 3)


async def test_play_without_a_total_never_marks_played(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(120, total=0)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status, state.duration) == (120, 2, None)


@pytest.mark.parametrize(("position", "status"), [(3540, 3), (3597, 3), (3539, 2)])
async def test_play_within_60_seconds_of_the_end_marks_played_like_music_assistant(
    client, services, respx_mock, alice, position, status
):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(position, total=3600)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (position, status)


@pytest.mark.parametrize(("position", "status"), [(3597, 3), (3000, 2)])
async def test_play_without_a_total_is_judged_against_the_feed_duration(
    client, services, respx_mock, alice, position, status
):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(position, total=0)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status, state.duration) == (position, status, None)


async def test_play_at_the_start_of_a_short_episode_is_not_played(
    client, services, respx_mock, alice
):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(0, total=45)])

    assert (await episode_state(services, alice.uuid, episode_id(show))).playing_status == 2


async def test_new_marks_unplayed(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)
    await post_actions(client, "alice", [play(3600, timestamp="2026-10-12T07:00:00")])

    await post_actions(
        client,
        "alice",
        [{"podcast": FEED, "episode": EPISODE_1, "action": "new", "timestamp": STAMP}],
    )

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (0, 1)


async def test_guid_wins_over_the_episode_url(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(60, episode=EPISODE_1, guid="ep-2")])

    assert await episode_state(services, alice.uuid, episode_id(show)) is None
    second = await episode_state(services, alice.uuid, episode_id(show, "ep-2", EPISODE_2))
    assert second.played_up_to == 60


async def test_unknown_guid_falls_back_to_the_episode_url(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(60, guid="not-in-the-feed")])

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 60


async def test_old_feed_url_matches_through_its_alias(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)
    async with services.sessionmaker() as session:
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        (await session.get(Podcast, show)).feed_url = "https://feeds.test/moved.xml"
        await session.commit()

    await post_actions(client, "alice", [play(60)])

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 60


async def test_unmatched_and_unsupported_actions_are_dropped_and_counted(
    client, services, respx_mock, alice, caplog
):
    show = await ingest(respx_mock, services)
    actions = [
        play(60),
        play(60, podcast="https://feeds.test/unknown.xml"),
        play(60, episode="https://cdn.example.com/unknown.mp3"),
        play(60, podcast="not a url"),
        {"podcast": FEED, "episode": EPISODE_2, "action": "download"},
        {"podcast": FEED, "episode": EPISODE_2, "action": "delete"},
        {"podcast": FEED, "action": "play"},
        "not an object",
        play(-5),
        play(float("nan")) | {"position": "NaN"},
    ]

    with caplog.at_level(logging.INFO, logger="podpocket.gpodder.router"):
        response = await post_actions(client, "alice", actions)

    assert response.status_code == 200
    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 60
    assert "1 applied, 3 unmatched, 6 ignored" in caplog.text


@pytest.mark.parametrize("guid", [b"\\ud800", b"nul\\u0000"])
async def test_text_the_database_cannot_store_is_ignored(
    client, services, respx_mock, alice, caplog, guid
):
    show = await ingest(respx_mock, services)
    body = (
        b'[{"podcast": "https://feeds.test/show.xml", "episode": "https://cdn.example.com/1.mp3",'
        b' "action": "play", "position": 1, "total": 2, "guid": "' + guid + b'"}]'
    )

    with caplog.at_level(logging.INFO, logger="podpocket.gpodder.router"):
        response = await client.post(
            "/api/2/episodes/alice.json", content=body, headers=basic("alice")
        )

    assert response.status_code == 200
    assert await episode_state(services, alice.uuid, episode_id(show)) is None
    assert "0 applied, 0 unmatched, 1 ignored" in caplog.text


async def test_nextcloud_style_upper_case_actions_are_accepted(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90) | {"action": "PLAY", "guid": "ep-1"}])

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 90


async def test_missing_timestamp_uses_the_receive_time(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)
    before = now_ms()

    await post_actions(client, "alice", [play(90, timestamp=None)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert before <= state.played_up_to_modified <= now_ms()


async def test_epoch_seconds_timestamp_is_accepted(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp=STAMP_MS // 1000)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified == STAMP_MS


async def test_future_timestamp_is_capped_at_now(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    before = now_ms()

    await post_actions(client, "alice", [play(90, timestamp="2999-01-01T00:00:00")])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert before <= state.played_up_to_modified <= now_ms()


@pytest.mark.parametrize(
    "timestamp",
    [
        "2026-10-12T08:00:00Z",
        "2026-10-12T10:00:00+02:00",
        "2026-10-12T03:00:00-05:00",
        "2026-10-12 08:00:00",
    ],
)
async def test_timestamps_are_read_as_utc(client, services, respx_mock, alice, timestamp):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp=timestamp)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified == STAMP_MS


@pytest.mark.parametrize(
    "timestamp",
    [
        "garbage",
        "",
        "1760000000",
        True,
        -1,
        253402300800,
        "9999-12-31T23:59:59-05:00",
        "0001-01-01T00:00:00+05:00",
        "0001-01-01T00:00:00",
    ],
)
async def test_present_but_invalid_timestamp_is_ignored(
    client, services, respx_mock, alice, caplog, timestamp
):
    show = await ingest(respx_mock, services)

    with caplog.at_level(logging.INFO, logger="podpocket.gpodder.router"):
        response = await post_actions(client, "alice", [play(90, timestamp=timestamp)])

    assert response.status_code == 200
    assert await episode_state(services, alice.uuid, episode_id(show)) is None
    assert "0 applied, 0 unmatched, 1 ignored" in caplog.text


@pytest.mark.parametrize("field", ["position", "total"])
async def test_boolean_seconds_are_ignored(client, services, respx_mock, alice, field):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(120) | {field: True}])

    assert await episode_state(services, alice.uuid, episode_id(show)) is None


async def test_stale_action_loses_to_newer_app_progress(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    uuid = episode_id(show)
    await sync_update(
        client, alice_auth, episode_upload(uuid, show, played_up_to=(900, STAMP_MS + 1000))
    )

    await post_actions(client, "alice", [play(60)])

    assert (await episode_state(services, alice.uuid, uuid)).played_up_to == 900


async def test_actions_in_one_upload_merge_by_their_timestamps(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(
        client,
        "alice",
        [play(300, timestamp="2026-10-12T09:00:00"), play(100, timestamp="2026-10-12T08:00:00")],
    )

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 300


async def test_same_second_duplicates_resolve_to_the_last_action(
    client, services, respx_mock, alice
):
    show = await ingest(respx_mock, services)
    new = {"podcast": FEED, "episode": EPISODE_1, "action": "new", "timestamp": STAMP}

    await post_actions(client, "alice", [play(300), new])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (0, 1)


async def test_same_second_duplicates_keep_the_later_play(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)
    new = {"podcast": FEED, "episode": EPISODE_1, "action": "new", "timestamp": STAMP}

    await post_actions(client, "alice", [new, play(100), play(99)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (99, 2)


async def test_request_order_does_not_override_timestamps(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(
        client,
        "alice",
        [play(300, timestamp="2026-10-12T09:00:00"), play(100, timestamp="2026-10-12T08:00:00")],
    )

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 300


async def test_every_spelling_of_a_feed_url_matches(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)
    spellings = [FEED, "HTTPS://FEEDS.TEST:443/show.xml", "feed:" + FEED]

    await post_actions(
        client, "alice", [play(60 + n, podcast=url) for n, url in enumerate(spellings)]
    )

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 62


async def test_actions_only_touch_the_callers_state(client, services, respx_mock, alice, bob):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(60)])

    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 60
    assert await episode_state(services, bob.uuid, episode_id(show)) is None
    await post_actions(client, "bob", [play(10, timestamp="2026-10-12T09:00:00")])
    assert (await episode_state(services, alice.uuid, episode_id(show))).played_up_to == 60
    assert (await episode_state(services, bob.uuid, episode_id(show))).played_up_to == 10


async def test_body_must_be_a_list(client, alice):
    response = await post_actions(client, "alice", {"actions": []})

    assert response.status_code == 400


async def test_invalid_json_is_a_400_without_echo(client, alice):
    response = await client.post(
        "/api/2/episodes/alice.json", content=b"[{secret-value", headers=basic("alice")
    )

    assert response.status_code == 400
    assert "secret-value" not in response.text


async def test_too_many_actions_are_rejected(client, alice):
    response = await post_actions(client, "alice", [play(1)] * 1001)

    assert response.status_code == 413


async def test_oversized_body_is_rejected(client, alice):
    body = b"[" + b" " * (2 * 1024 * 1024) + b"]"
    response = await client.post("/api/2/episodes/alice.json", content=body, headers=basic("alice"))

    assert response.status_code == 413


@pytest.fixture
def receive_times(monkeypatch):
    times = iter([1_700_000_000_450, 1_700_000_000_460])
    monkeypatch.setattr(router, "now_ms", lambda: next(times))


@pytest.mark.parametrize("timestamp", ["2023-11-14T22:13:20", 1_700_000_000])
async def test_a_whole_second_in_the_current_second_is_stamped_with_the_receive_time(
    client, services, respx_mock, alice, receive_times, timestamp
):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp=timestamp)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified == 1_700_000_000_450


async def test_same_second_reports_are_ordered_by_arrival(
    client, services, respx_mock, alice, receive_times
):
    show = await ingest(respx_mock, services)
    stamp = "2023-11-14T22:13:20"

    await post_actions(client, "alice", [play(998, timestamp=stamp)])
    await post_actions(client, "alice", [play(1003, timestamp=stamp)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to == 1003


async def test_a_whole_second_from_an_earlier_second_keeps_its_time(
    client, services, respx_mock, alice, receive_times
):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp="2023-11-14T22:13:19")])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified == 1_699_999_999_000


def seeded_plays(feeds: int, episodes_per_feed: int = 2) -> list[dict]:
    return [
        play(
            60 + feed,
            episode=seeded_url(feed, position),
            podcast=seeded_feed(feed),
            guid=seeded_guid(feed, position),
        )
        for feed in range(feeds)
        for position in range(episodes_per_feed)
    ]


async def test_a_large_upload_matches_with_a_handful_of_catalog_queries(client, services, alice):
    await seed_catalog(services, feeds=500)

    with catalog_selects(services) as selects:
        response = await post_actions(client, "alice", seeded_plays(500))

    assert response.status_code == 200
    assert len(selects) <= 16
    last = podcast_uuid(seeded_feed(499))
    state = await episode_state(
        services,
        alice.uuid,
        episode_uuid(last, seeded_guid(499, 1), seeded_url(499, 1)),
    )
    assert state.played_up_to == 60 + 499


async def test_catalog_lookups_select_columns_only(client, services, alice):
    await seed_catalog(services, feeds=3)

    with catalog_selects(services) as selects:
        await post_actions(client, "alice", seeded_plays(3))

    episode_selects = [statement for statement in selects if "FROM episodes" in statement]
    assert episode_selects
    assert all("show_notes" not in statement for statement in episode_selects)


async def test_a_large_upload_does_not_hold_the_write_lock_while_matching(
    client, services, alice, bob, monkeypatch
):
    await seed_catalog(services, feeds=500)
    matched_while_writing = []
    match_actions = router.match_actions

    async def match_then_let_bob_write(session, actions):
        pairs = await match_actions(session, actions)
        async with asyncio.timeout(30):
            await write_as(services, bob, podcast_uuid(seeded_feed(0)))
        matched_while_writing.append(len(pairs))
        return pairs

    monkeypatch.setattr(router, "match_actions", match_then_let_bob_write)

    response = await post_actions(client, "alice", seeded_plays(500))

    assert response.status_code == 200
    assert matched_while_writing == [1000]


async def test_a_large_upload_writes_its_change_log_in_one_statement(client, services, alice):
    await seed_catalog(services, feeds=500)

    with change_log_inserts(services) as inserts:
        response = await post_actions(client, "alice", seeded_plays(500))

    assert response.status_code == 200
    assert len(inserts) == 1
    async with services.sessionmaker() as session:
        logged = await session.scalar(select(func.count()).select_from(ChangeLog))
    assert logged == 3000
