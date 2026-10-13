import logging
from datetime import UTC, datetime

import pytest

from podpocket.gpodder import actions as actions_module
from podpocket.gpodder.actions import EpisodeRef, episode_action, gpodder_time
from podpocket.models import Podcast, PodcastAlias, UserEpisodeState
from podpocket.sync.writer import INT32_MAX
from podpocket.timefmt import epoch_ms
from tests.factories import item, rss
from tests.gpodder import (
    EPISODE_1,
    EPISODE_2,
    FEED,
    basic,
    episode_id,
    get_actions,
    ingest,
    subscribe,
)
from tests.ma_gpodder.client import EpisodeActionGet, EpisodeActionNew, EpisodeActionPlay
from tests.records import episode_upload, sync_update

OTHER_FEED = "https://feeds.test/other.xml"
OTHER_EPISODE = "https://cdn.example.com/other.mp3"
OTHER_SHOW = rss(item(guid="other-1", url=OTHER_EPISODE), title="Other Show")

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


def test_unplayed_mark_newer_than_progress_is_a_new_action():
    action = episode_action(
        state(
            played_up_to=30,
            played_up_to_modified=STAMP_MS,
            playing_status=1,
            playing_status_modified=STAMP_MS + 1000,
        ),
        REF,
    )

    assert action["action"] == "new"


def test_progress_newer_than_the_unplayed_mark_is_a_play_action():
    action = episode_action(
        state(
            played_up_to=30,
            played_up_to_modified=STAMP_MS + 1000,
            playing_status=1,
            playing_status_modified=STAMP_MS,
        ),
        REF,
    )

    assert (action["action"], action["position"]) == ("play", 30)


def test_total_never_exceeds_the_int32_range():
    ref = EpisodeRef(feed_url=FEED, enclosure_url=EPISODE_1, guid=None, duration=0.0)

    action = episode_action(
        state(played_up_to=INT32_MAX, played_up_to_modified=STAMP_MS, playing_status=2), ref
    )

    assert action["total"] == INT32_MAX
    assert action["position"] < action["total"]


def test_absurd_modified_times_do_not_break_the_timestamp():
    assert gpodder_time(-(2**63)) == "1970-01-01T00:00:00"
    assert gpodder_time(2**63 - 1) == "1970-01-01T00:00:00"


async def test_lists_actions_for_catalog_episodes_only(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
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
    await subscribe(client, alice_auth, show)
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
    await subscribe(client, alice_auth, show)
    await sync_update(client, alice_auth, episode_upload(episode_id(show), show, starred=(True, 1)))

    assert (await get_actions(client, "alice"))["actions"] == []


async def test_other_users_progress_is_not_listed(client, services, respx_mock, alice, bob_auth):
    show = await ingest(respx_mock, services)
    await subscribe(client, bob_auth, show)
    await sync_update(client, bob_auth, episode_upload(episode_id(show), show, played_up_to=(5, 1)))

    assert (await get_actions(client, "alice"))["actions"] == []


async def test_device_and_aggregated_parameters_are_accepted(client, alice):
    response = await client.get(
        "/api/2/episodes/alice.json",
        params={"since": 0, "device": "ma", "aggregated": "true"},
        headers=basic("alice"),
    )

    assert response.status_code == 200


async def test_unsubscribed_podcasts_are_excluded_even_since_zero(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
    await sync_update(
        client, alice_auth, episode_upload(episode_id(show), show, played_up_to=(5, STAMP_MS))
    )
    await subscribe(client, alice_auth, show, subscribed=False)

    assert (await get_actions(client, "alice"))["actions"] == []


async def test_progress_without_any_subscription_is_not_listed(
    client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await sync_update(
        client, alice_auth, episode_upload(episode_id(show), show, played_up_to=(5, STAMP_MS))
    )

    assert (await get_actions(client, "alice"))["actions"] == []


async def listen_to_both_shows(client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    other = await ingest(respx_mock, services, OTHER_FEED, OTHER_SHOW)
    other_episode = episode_id(other, "other-1", OTHER_EPISODE)
    await subscribe(client, alice_auth, show)
    await subscribe(client, alice_auth, other)
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode_id(show), show, played_up_to=(10, STAMP_MS)),
        episode_upload(other_episode, other, played_up_to=(20, STAMP_MS + 1000)),
    )
    return show, other


async def filtered(client, podcast: str) -> list[str]:
    response = await client.get(
        "/api/2/episodes/alice.json", params={"podcast": podcast}, headers=basic("alice")
    )
    assert response.status_code == 200, response.text
    return [action["episode"] for action in response.json()["actions"]]


async def test_podcast_parameter_narrows_to_one_feed(
    client, services, respx_mock, alice, alice_auth
):
    await listen_to_both_shows(client, services, respx_mock, alice_auth)

    assert await filtered(client, FEED) == [EPISODE_1]
    assert await filtered(client, OTHER_FEED) == [OTHER_EPISODE]
    assert [a["episode"] for a in (await get_actions(client, "alice"))["actions"]] == [
        OTHER_EPISODE,
        EPISODE_1,
    ]


async def test_podcast_parameter_matches_an_alias(client, services, respx_mock, alice, alice_auth):
    show, _ = await listen_to_both_shows(client, services, respx_mock, alice_auth)
    async with services.sessionmaker() as session:
        podcast = await session.get(Podcast, show)
        session.add(PodcastAlias(url=FEED, podcast_uuid=show))
        podcast.feed_url = "https://feeds.test/moved.xml"
        await session.commit()

    assert await filtered(client, FEED) == [EPISODE_1]


@pytest.mark.parametrize("podcast", ["https://feeds.test/unknown.xml", "not a url", "x" * 100])
async def test_unknown_podcast_gives_an_empty_list(
    client, services, respx_mock, alice, alice_auth, podcast
):
    await listen_to_both_shows(client, services, respx_mock, alice_auth)

    assert await filtered(client, podcast) == []


async def test_response_is_capped_to_the_newest_actions(
    client, services, respx_mock, alice, alice_auth, monkeypatch, caplog
):
    await listen_to_both_shows(client, services, respx_mock, alice_auth)
    monkeypatch.setattr(actions_module, "MAX_RESPONSE_ACTIONS", 1)

    with caplog.at_level(logging.WARNING, logger=actions_module.__name__):
        body = await get_actions(client, "alice")

    assert [a["episode"] for a in body["actions"]] == [OTHER_EPISODE]
    assert "truncated" in caplog.text


async def test_response_parses_with_the_music_assistant_client(
    client, services, respx_mock, alice, alice_auth
):
    guidless_feed = "https://feeds.test/guidless.xml"
    guidless_episode = "https://cdn.example.com/guidless.mp3"
    show = await ingest(respx_mock, services)
    guidless = await ingest(
        respx_mock, services, guidless_feed, rss(item(guid=None, url=guidless_episode))
    )
    await subscribe(client, alice_auth, show)
    await subscribe(client, alice_auth, guidless)
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode_id(show), show, played_up_to=(120, STAMP_MS)),
        episode_upload(
            episode_id(show, "ep-2", EPISODE_2),
            show,
            playing_status=(1, STAMP_MS + 1000),
            played_up_to=(0, STAMP_MS + 1000),
        ),
        episode_upload(
            episode_id(guidless, None, guidless_episode),
            guidless,
            played_up_to=(7, STAMP_MS + 2000),
        ),
    )
    response = await client.get("/api/2/episodes/alice.json", headers=basic("alice"))

    parsed = EpisodeActionGet.from_json(response.text)

    by_episode = {action.episode: action for action in parsed.actions}
    assert isinstance(by_episode[EPISODE_1], EpisodeActionPlay)
    assert (by_episode[EPISODE_1].position, by_episode[EPISODE_1].guid) == (120, "ep-1")
    assert isinstance(by_episode[EPISODE_2], EpisodeActionNew)
    assert by_episode[guidless_episode].guid is None
    assert parsed.timestamp > 0


@pytest.mark.parametrize("since", ["-1", "99999999999999999999", "abc", "1.5"])
async def test_invalid_since_is_rejected_without_echo(client, alice, since):
    response = await client.get(
        "/api/2/episodes/alice.json", params={"since": since}, headers=basic("alice")
    )

    assert response.status_code == 422
    assert since not in response.text
