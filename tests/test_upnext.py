from datetime import UTC, datetime

from google.protobuf.timestamp_pb2 import Timestamp

from podpocket.catalog import list_episodes
from podpocket.models import User
from podpocket.protocol import sync_api_pb2 as pb
from podpocket.protocol import to_timestamp
from podpocket.sync import upnext
from podpocket.sync.upnext import (
    MAX_CHANGES,
    MAX_QUEUE,
    PLAY_LAST,
    PLAY_NEXT,
    PLAY_NOW,
    REMOVE,
    REPLACE,
    QueueChange,
    QueueEntry,
    apply_changes,
)
from podpocket.sync.writer import MAX_CLOCK_SKEW_MS, UserGone
from podpocket.timefmt import now_ms
from tests.api import PROTOBUF, post_proto
from tests.factories import item, rss

UP_NEXT = "/api/up_next/sync"
FEED = "https://feeds.test/show.xml"
A, B, C = (QueueEntry(uuid) for uuid in "abc")


def uuids(entries):
    return [entry.uuid for entry in entries]


def up_next_body(*changes, server_modified=0):
    return {
        "deviceTime": 1,
        "version": "2",
        "upNext": {"serverModified": server_modified, "changes": list(changes)},
    }


def add_last(uuid, modified, **metadata):
    return {"action": PLAY_LAST, "modified": modified, "uuid": uuid, **metadata}


def replace_all(modified, *episode_uuids):
    episodes = [{"uuid": uuid, "podcast": "p1"} for uuid in episode_uuids]
    return {"action": REPLACE, "modified": modified, "episodes": episodes}


def test_play_now_next_last_and_remove():
    changes = [
        QueueChange(PLAY_LAST, 1, C),
        QueueChange(PLAY_NOW, 2, C),
        QueueChange(PLAY_NEXT, 3, A),
        QueueChange(REMOVE, 4, B),
    ]

    queue, _ = apply_changes([A, B], changes, 0)

    assert uuids(queue) == ["c", "a"]


def test_changes_apply_in_modified_order():
    queue, _ = apply_changes([], [QueueChange(PLAY_LAST, 20, B), QueueChange(PLAY_LAST, 10, A)], 0)

    assert uuids(queue) == ["a", "b"]


def test_play_next_on_an_empty_queue():
    queue, _ = apply_changes([], [QueueChange(PLAY_NEXT, 1, A)], 0)

    assert uuids(queue) == ["a"]


def test_replace_sets_the_whole_queue_without_duplicates():
    queue, replaced = apply_changes([A], [QueueChange(REPLACE, 5, entries=(B, C, B))], 0)

    assert (uuids(queue), replaced) == (["b", "c"], 5)


def test_stale_replace_from_an_offline_device_is_ignored():
    queue, replaced = apply_changes([A, B], [QueueChange(REPLACE, 5, entries=(C,))], 10)

    assert (uuids(queue), replaced) == (["a", "b"], 10)


def test_readding_keeps_known_metadata():
    known = QueueEntry("a", podcast_uuid="p", title="T", url="u")

    queue, _ = apply_changes([known, B], [QueueChange(PLAY_LAST, 1, QueueEntry("a"))], 0)

    assert queue == [B, known]


async def test_json_sync_applies_changes_and_returns_the_queue(client, alice_auth):
    body = up_next_body(
        add_last(
            "e1",
            1000,
            title="One",
            url="https://cdn/1.mp3",
            published="2026-10-12T09:00:00Z",
            podcast="p1",
        ),
        add_last("e2", 1001, podcast="p1"),
    )

    response = await client.post(UP_NEXT, json=body, headers=alice_auth)

    data = response.json()
    assert data["serverModified"] > 0
    assert data["episodes"] == [
        {
            "uuid": "e1",
            "title": "One",
            "url": "https://cdn/1.mp3",
            "podcast": "p1",
            "published": "2026-10-12T09:00:00Z",
        },
        {"uuid": "e2", "podcast": "p1"},
    ]


async def test_new_account_has_an_unchanged_empty_queue(client, alice_auth):
    response = await client.post(UP_NEXT, json=up_next_body(), headers=alice_auth)

    assert response.status_code == 200
    assert response.json() == {"serverModified": 0, "episodes": []}


async def test_unchanged_queue_keeps_server_modified(client, alice_auth):
    first = await client.post(
        UP_NEXT, json=up_next_body(add_last("e1", 1000, podcast="p1")), headers=alice_auth
    )
    server_modified = first.json()["serverModified"]

    again = await client.post(
        UP_NEXT, json=up_next_body(server_modified=server_modified), headers=alice_auth
    )

    assert again.status_code == 200
    assert again.json() == first.json()


async def test_protobuf_replace_sets_the_whole_queue(client, alice_auth):
    published = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
    replace = pb.UpNextChanges.Change(
        action=REPLACE,
        modified=2000,
        episodes=[
            pb.UpNextEpisodeRequest(
                uuid="e1", podcast="p1", title="One", url="u1", published=to_timestamp(published)
            ),
            pb.UpNextEpisodeRequest(uuid="e2", podcast="p1"),
        ],
    )
    request = pb.UpNextSyncRequest(
        device_time=1,
        version="2",
        model="mobile",
        device_id="phone",
        up_next=pb.UpNextChanges(server_modified=0, changes=[replace]),
    )

    response = await post_proto(client, UP_NEXT, request, pb.UpNextResponse, alice_auth)

    assert response.server_modified > 0
    assert [(e.uuid, e.podcast, e.title) for e in response.episodes] == [
        ("e1", "p1", "One"),
        ("e2", "p1", ""),
    ]
    assert response.episodes[0].published == to_timestamp(published)
    assert not response.episodes[1].HasField("published")


async def test_json_and_protobuf_share_one_queue(client, alice_auth):
    await client.post(
        UP_NEXT, json=up_next_body(add_last("e1", 1000, podcast="p1")), headers=alice_auth
    )

    request = pb.UpNextSyncRequest(version="2", up_next=pb.UpNextChanges())
    response = await post_proto(client, UP_NEXT, request, pb.UpNextResponse, alice_auth)

    assert [episode.uuid for episode in response.episodes] == ["e1"]


async def test_missing_metadata_comes_from_the_catalog(client, services, respx_mock, alice_auth):
    respx_mock.get(FEED).respond(200, content=rss(item(guid="ep-1", title="First")))
    podcast_uuid = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        [episode] = await list_episodes(session, podcast_uuid)

    response = await client.post(
        UP_NEXT, json=up_next_body(add_last(episode.uuid, 1000)), headers=alice_auth
    )

    assert response.json()["episodes"] == [
        {
            "uuid": episode.uuid,
            "title": "First",
            "url": "https://cdn.example.com/1.mp3",
            "podcast": podcast_uuid,
            "published": "2026-10-12T06:00:00Z",
        }
    ]


async def test_an_older_replace_from_another_device_does_not_win(client, alice_auth):
    await client.post(UP_NEXT, json=up_next_body(replace_all(2000, "a", "b")), headers=alice_auth)

    response = await client.post(
        UP_NEXT, json=up_next_body(replace_all(1000, "c")), headers=alice_auth
    )

    assert [episode["uuid"] for episode in response.json()["episodes"]] == ["a", "b"]


async def test_up_next_needs_a_token(client):
    assert (await client.post(UP_NEXT, json=up_next_body())).status_code == 401


async def test_a_long_queue_is_kept_whole(client, alice_auth):
    episode_uuids = [f"e{index}" for index in range(1200)]

    response = await client.post(
        UP_NEXT, json=up_next_body(replace_all(1000, *episode_uuids)), headers=alice_auth
    )

    assert [episode["uuid"] for episode in response.json()["episodes"]] == episode_uuids


async def test_a_modified_time_beyond_int64_is_rejected(client, alice_auth):
    body = up_next_body(add_last("e1", 2**63, podcast="p1"))

    assert (await client.post(UP_NEXT, json=body, headers=alice_auth)).status_code == 422


async def test_invalid_json_is_rejected(client, alice_auth):
    response = await client.post(
        UP_NEXT,
        content=b"{nope",
        headers={**alice_auth, "Content-Type": "application/json"},
    )

    assert response.status_code == 422


async def test_malformed_protobuf_is_rejected(client, alice_auth):
    response = await client.post(
        UP_NEXT, content=b"\xff\xff\xff", headers={**alice_auth, **PROTOBUF}
    )

    assert response.status_code == 400


async def test_unknown_actions_change_nothing(client, alice_auth):
    body = up_next_body({"action": 99, "modified": 1000, "uuid": "e1"})

    response = await client.post(UP_NEXT, json=body, headers=alice_auth)

    assert response.json() == {"serverModified": 0, "episodes": []}


async def test_changes_beyond_the_cap_are_not_processed(client, alice_auth):
    changes = [add_last(f"e{index}", index, podcast="p1") for index in range(MAX_CHANGES + 5)]

    response = await client.post(UP_NEXT, json=up_next_body(*changes), headers=alice_auth)

    assert len(response.json()["episodes"]) == MAX_CHANGES


def test_replace_is_capped_to_the_queue_limit():
    entries = tuple(QueueEntry(f"e{index}") for index in range(MAX_QUEUE + 5))

    queue, _ = apply_changes([], [QueueChange(REPLACE, 1, entries=entries)], 0)

    assert [entry.uuid for entry in queue] == [entry.uuid for entry in entries[:MAX_QUEUE]]


def test_appending_to_a_full_queue_drops_the_tail():
    full = [QueueEntry(f"e{index}") for index in range(MAX_QUEUE)]

    queue, _ = apply_changes(full, [QueueChange(PLAY_LAST, 1, A)], 0)

    assert len(queue) == MAX_QUEUE
    assert "a" not in uuids(queue)


async def test_far_future_modified_times_are_clamped(client, services, alice, alice_auth):
    body = up_next_body(replace_all(2**62, "e1"))

    response = await client.post(UP_NEXT, json=body, headers=alice_auth)

    assert response.status_code == 200
    async with services.sessionmaker() as session:
        user = await session.get(User, alice.uuid)
    assert user.up_next_replaced_ms <= now_ms() + MAX_CLOCK_SKEW_MS


async def test_a_pre_1970_published_date_is_ignored(client, alice_auth):
    body = up_next_body(add_last("e1", 1000, podcast="p1", published="0001-01-01T00:00:00Z"))

    response = await client.post(UP_NEXT, json=body, headers=alice_auth)

    assert response.json()["episodes"] == [{"uuid": "e1", "podcast": "p1"}]


async def test_a_pre_1970_protobuf_published_date_is_ignored(client, alice_auth):
    year_one = Timestamp(seconds=-62135596800)
    change = pb.UpNextChanges.Change(
        action=PLAY_LAST, modified=1000, uuid="e1", podcast="p1", published=year_one
    )
    request = pb.UpNextSyncRequest(up_next=pb.UpNextChanges(changes=[change]))

    response = await post_proto(client, UP_NEXT, request, pb.UpNextResponse, alice_auth)

    assert not response.episodes[0].HasField("published")


async def test_account_deleted_mid_request_answers_401(client, alice_auth, monkeypatch):
    async def gone(session, user_uuid):
        raise UserGone(user_uuid)

    monkeypatch.setattr(upnext, "lock_user", gone)

    response = await client.post(UP_NEXT, json=up_next_body(), headers=alice_auth)

    assert response.status_code == 401
