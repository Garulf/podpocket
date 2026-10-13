import asyncio
import logging
import socket

import aiohttp
import pytest
import uvicorn

from podpocket.accounts.users import create_user
from tests.api import PASSWORD
from tests.gpodder import EPISODE_1, EPISODE_2, FEED, episode_id, episode_state, ingest, subscribe
from tests.ma_gpodder.client import EpisodeActionNew, EpisodeActionPlay, GPodderClient
from tests.records import episode_upload, records_of, sync_update

DEVICE = "music-assistant"


@pytest.fixture
async def server_url(app):
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="warning"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    while not server.started:
        assert not task.done(), "the test server stopped"
        await asyncio.sleep(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    await task


@pytest.fixture
async def http_session():
    async with aiohttp.ClientSession() as session:
        yield session


async def music_assistant(session, url, login="alice", password=PASSWORD) -> GPodderClient:
    client = GPodderClient(session=session, logger=logging.getLogger("music-assistant"))
    await client.init_gpodder(username=login, password=password, device=DEVICE, base_url=url)
    return client


@pytest.fixture
async def ma(http_session, server_url, alice):
    return await music_assistant(http_session, server_url)


async def test_login_with_the_app_password(http_session, server_url, alice):
    assert await music_assistant(http_session, server_url) is not None


async def test_wrong_password_fails_login(http_session, server_url, alice):
    with pytest.raises(RuntimeError):
        await music_assistant(http_session, server_url, password="wrong password")


async def test_non_ascii_password_from_music_assistant(http_session, server_url, services):
    await create_user(services.sessionmaker, "carol", "pässwörd")

    assert await music_assistant(http_session, server_url, "carol", "pässwörd") is not None


async def test_subscriptions_reach_music_assistant(ma, client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)

    subscriptions = await ma.get_subscriptions()

    assert (subscriptions.add, subscriptions.remove) == ([FEED], [])


async def test_unsubscribe_in_the_app_reaches_music_assistant(
    ma, client, services, respx_mock, alice_auth
):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
    await subscribe(client, alice_auth, show, subscribed=False)

    subscriptions = await ma.get_subscriptions()

    assert (subscriptions.add, subscriptions.remove) == ([], [FEED])


async def test_progress_from_music_assistant_reaches_the_app(
    ma, client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    cursor = (await sync_update(client, alice_auth)).last_modified

    await ma.update_progress(
        podcast_id=FEED, episode_id=EPISODE_1, guid="ep-1", position_s=120, duration_s=3600
    )

    episodes = records_of(await sync_update(client, alice_auth, last_modified=cursor), "episode")
    assert [(e.uuid, e.played_up_to.value, e.playing_status.value) for e in episodes] == [
        (episode_id(show), 120, 2)
    ]


async def test_finishing_in_music_assistant_marks_played(ma, services, respx_mock, alice):
    show = await ingest(respx_mock, services)

    await ma.update_progress(
        podcast_id=FEED, episode_id=EPISODE_1, guid="ep-1", position_s=3600, duration_s=3600
    )

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (3600, 3)


async def test_mark_unplayed_in_music_assistant(
    ma, client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await sync_update(
        client, alice_auth, episode_upload(episode_id(show), show, playing_status=(3, 1000))
    )

    await ma.update_progress(
        podcast_id=FEED, episode_id=EPISODE_1, guid="ep-1", position_s=0, duration_s=3600
    )

    state = await episode_state(services, alice.uuid, episode_id(show))
    assert (state.played_up_to, state.playing_status) == (0, 1)


async def test_app_progress_reaches_music_assistant(ma, client, services, respx_mock, alice_auth):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
    second = episode_id(show, "ep-2", EPISODE_2)
    await sync_update(
        client,
        alice_auth,
        episode_upload(episode_id(show), show, played_up_to=(300, 1000), playing_status=(2, 1000)),
        episode_upload(second, show, playing_status=(1, 1000)),
    )

    actions, timestamp = await ma.get_episode_actions(since=0)

    by_episode = {action.episode: action for action in actions}
    play = by_episode[EPISODE_1]
    assert isinstance(play, EpisodeActionPlay)
    assert (play.podcast, play.guid, play.position, play.total) == (FEED, "ep-1", 300, 3600)
    assert isinstance(by_episode[EPISODE_2], EpisodeActionNew)
    assert timestamp is not None
    assert await ma.get_episode_actions(since=timestamp) == ([], timestamp)


async def test_newer_app_progress_beats_music_assistant(
    ma, client, services, respx_mock, alice, alice_auth
):
    show = await ingest(respx_mock, services)
    await subscribe(client, alice_auth, show)
    await ma.update_progress(
        podcast_id=FEED, episode_id=EPISODE_1, guid="ep-1", position_s=120, duration_s=3600
    )
    await client.post(
        "/api/sync/update_episode",
        json={
            "uuid": episode_id(show),
            "podcast": show,
            "position": 600,
            "duration": 3600,
            "status": 2,
        },
        headers=alice_auth,
    )

    actions, _ = await ma.get_episode_actions(since=0)

    assert [(a.episode, a.position) for a in actions] == [(EPISODE_1, 600)]
