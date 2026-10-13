from datetime import UTC, datetime

from podpocket.gpodder.actions import EpisodeRef, episode_action, gpodder_time
from podpocket.models import UserEpisodeState
from podpocket.timefmt import epoch_ms
from tests.gpodder import EPISODE_1, EPISODE_2, FEED, basic, episode_id, get_actions, ingest
from tests.records import episode_upload, sync_update

STAMP_MS = epoch_ms(datetime(2026, 10, 12, 8, 0, tzinfo=UTC))
REF = EpisodeRef(feed_url=FEED, enclosure_url=EPISODE_1, guid="ep-1", duration=3600.0)


def state(**fields):
    return UserEpisodeState(user_uuid="u", episode_uuid="e", podcast_uuid="p", **fields)


def test_in_progress_episode_is_a_play_action():
    action = episode_action(
        state(played_up_to=120, played_up_to_modified=STAMP_MS, playing_status=2), REF
    )

    assert action == {
        "podcast": FEED,
        "episode": EPISODE_1,
        "guid": "ep-1",
        "timestamp": "2026-10-12T08:00:00",
        "action": "play",
        "started": 0,
        "position": 120,
        "total": 3600,
    }


def test_completed_episode_reports_position_equal_to_total():
    action = episode_action(
        state(played_up_to=10, playing_status=3, playing_status_modified=STAMP_MS), REF
    )

    assert (action["action"], action["position"], action["total"]) == ("play", 3600, 3600)


def test_unknown_duration_never_looks_finished():
    ref = EpisodeRef(feed_url=FEED, enclosure_url=EPISODE_1, guid=None, duration=0.0)

    action = episode_action(
        state(played_up_to=120, played_up_to_modified=STAMP_MS, playing_status=2), ref
    )

    assert action["position"] < action["total"]
    assert "guid" not in action


def test_user_duration_wins_over_the_feed_duration():
    action = episode_action(
        state(played_up_to=1, played_up_to_modified=STAMP_MS, duration=1800), REF
    )

    assert action["total"] == 1800


def test_unplayed_episode_is_a_new_action():
    action = episode_action(
        state(played_up_to=0, playing_status=1, playing_status_modified=STAMP_MS), REF
    )

    assert action["action"] == "new"
    assert "position" not in action


def test_state_without_progress_is_skipped():
    assert episode_action(state(starred=True, starred_modified=STAMP_MS), REF) is None


def test_absurd_modified_times_do_not_break_the_timestamp():
    assert gpodder_time(-(2**63)) == "1970-01-01T00:00:00"
    assert gpodder_time(2**63 - 1) == "1970-01-01T00:00:00"


async def test_lists_actions_for_catalog_episodes_only(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode_id(show), show, played_up_to=(120, STAMP_MS)),
        episode_upload("not-in-catalog", show, played_up_to=(60, STAMP_MS)),
    )

    body = await get_actions(client, "alice")

    assert [(a["episode"], a["position"]) for a in body["actions"]] == [(EPISODE_1, 120)]
    assert body["timestamp"] > 0


async def test_since_returns_only_later_progress(client, services, respx_mock, alice, alice_auth):
    show = await ingest(respx_mock, services)
    second = episode_id(show, "ep-2", EPISODE_2)
    await sync_update(
        client, alice_auth, episode_upload(episode_id(show), show, played_up_to=(1, 1))
    )
    first = await get_actions(client, "alice")
    await sync_update(client, alice_auth, episode_upload(second, show, played_up_to=(2, 2)))
    await sync_update(client, alice_auth, episode_upload(second, show, starred=(True, 3)))

    later = await get_actions(client, "alice", since=first["timestamp"])

    assert [a["episode"] for a in later["actions"]] == [EPISODE_2]
    assert (await get_actions(client, "alice", since=later["timestamp"]))["actions"] == []


async def test_star_alone_is_not_an_action(client, services, respx_mock, alice, alice_auth):
    show = await ingest(respx_mock, services)
    await sync_update(client, alice_auth, episode_upload(episode_id(show), show, starred=(True, 1)))

    assert (await get_actions(client, "alice"))["actions"] == []


async def test_other_users_progress_is_not_listed(client, services, respx_mock, alice, bob_auth):
    show = await ingest(respx_mock, services)
    await sync_update(client, bob_auth, episode_upload(episode_id(show), show, played_up_to=(5, 1)))

    assert (await get_actions(client, "alice"))["actions"] == []


async def test_device_and_podcast_filters_are_accepted(client, alice):
    response = await client.get(
        "/api/2/episodes/alice.json",
        params={"since": 0, "device": "ma", "aggregated": "true"},
        headers=basic("alice"),
    )

    assert response.status_code == 200


async def test_negative_since_is_rejected(client, alice):
    response = await client.get(
        "/api/2/episodes/alice.json", params={"since": -1}, headers=basic("alice")
    )

    assert response.status_code == 422
