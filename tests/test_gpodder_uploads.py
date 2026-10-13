import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from podpocket.models import ChangeLog, Podcast, PodcastAlias
from podpocket.timefmt import epoch_ms, now_ms
from tests.gpodder import (
    EPISODE_1,
    EPISODE_2,
    FEED,
    basic,
    episode_id,
    episode_state,
    ingest,
    post_actions,
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
    assert state.played_up_to_modified >= before


async def test_epoch_seconds_timestamp_is_accepted(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp=STAMP_MS // 1000)])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified == STAMP_MS


async def test_future_timestamp_is_capped_at_now(client, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await post_actions(client, "alice", [play(90, timestamp="2999-01-01T00:00:00")])

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert state.played_up_to_modified <= now_ms()


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
