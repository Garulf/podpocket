from sqlalchemy import select

from podpocket.models import ChangeLog, UserEpisodeState

PING = "/api/sync/update_episode"


async def load(services, user_uuid, episode_uuid):
    async with services.sessionmaker() as session:
        return await session.get(UserEpisodeState, (user_uuid, episode_uuid))


async def test_progress_ping_updates_the_episode(client, services, alice, alice_auth):
    body = {"uuid": "e1", "podcast": "p1", "position": 120, "duration": 3600, "status": 2}

    response = await client.post(PING, json=body, headers=alice_auth)

    assert response.status_code == 204
    state = await load(services, alice.uuid, "e1")
    assert (state.podcast_uuid, state.played_up_to, state.duration, state.playing_status) == (
        "p1",
        120,
        3600,
        2,
    )
    assert state.played_up_to_modified > 0


async def test_completion_ping_marks_the_episode_played(client, services, alice, alice_auth):
    await client.post(
        PING,
        json={"uuid": "e1", "podcast": "p1", "position": 120, "duration": 3600, "status": 2},
        headers=alice_auth,
    )
    await client.post(
        PING,
        json={"uuid": "e1", "podcast": "p1", "position": 3600, "duration": 3600, "status": 3},
        headers=alice_auth,
    )

    state = await load(services, alice.uuid, "e1")
    assert (state.played_up_to, state.playing_status) == (3600, 3)


async def test_progress_ping_needs_a_token(client):
    body = {"uuid": "e1", "podcast": "p1", "position": 1, "duration": 2, "status": 2}

    assert (await client.post(PING, json=body)).status_code == 401


async def test_out_of_range_numbers_never_reach_the_database(client, services, alice, alice_auth):
    huge = {"uuid": "e1", "podcast": "p1", "position": 2**63, "duration": 1, "status": 2}
    negative = {"uuid": "e1", "podcast": "p1", "position": -1, "duration": 0, "status": 9}

    assert (await client.post(PING, json=huge, headers=alice_auth)).status_code == 422
    assert (await client.post(PING, json=negative, headers=alice_auth)).status_code == 204
    async with services.sessionmaker() as session:
        assert (await session.scalars(select(ChangeLog))).all() == []


async def test_malformed_progress_pings_are_client_errors(client, alice_auth):
    huge_status = {"uuid": "e1", "podcast": "p1", "position": 1, "duration": 2, "status": 10**30}
    missing = {"uuid": "e1"}

    assert (await client.post(PING, json=huge_status, headers=alice_auth)).status_code == 204
    assert (await client.post(PING, json=missing, headers=alice_auth)).status_code == 422
    assert (await client.post(PING, content=b"{nope", headers=alice_auth)).status_code == 422
    assert (await client.post(PING, content=b"", headers=alice_auth)).status_code == 422
