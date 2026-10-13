from datetime import UTC, datetime

import pytest

from podpocket.timefmt import epoch_ms, now_ms

HISTORY = "/api/history/sync"
YEAR = "/api/history/year"
LISTENED_2026 = epoch_ms(datetime(2026, 3, 1, tzinfo=UTC))
LISTENED_2025 = epoch_ms(datetime(2025, 6, 1, tzinfo=UTC))


def listen(episode, modified, action=1):
    return {
        "action": action,
        "modifiedAt": str(modified),
        "episode": episode,
        "podcast": "p1",
        "title": f"Title {episode}",
        "url": f"https://cdn.test/{episode}.mp3",
        "published": "2026-02-01T00:00:00Z",
    }


def history_body(*changes, server_modified=0):
    return {
        "changes": list(changes),
        "deviceTime": 1,
        "serverModified": server_modified,
        "version": 1,
    }


async def sync(client, auth, *changes, server_modified=0):
    response = await client.post(
        HISTORY, json=history_body(*changes, server_modified=server_modified), headers=auth
    )
    assert response.status_code == 200
    return response.json()


async def test_listens_are_stored_and_returned(client, alice_auth):
    body = await sync(client, alice_auth, listen("e1", 1000), listen("e2", 2000))

    assert body["serverModified"] > 0
    assert body["lastCleared"] == 0
    assert body["changes"] == [
        {
            "action": 1,
            "modifiedAt": "1000",
            "episode": "e1",
            "podcast": "p1",
            "title": "Title e1",
            "url": "https://cdn.test/e1.mp3",
            "published": "2026-02-01T00:00:00Z",
        },
        {
            "action": 1,
            "modifiedAt": "2000",
            "episode": "e2",
            "podcast": "p1",
            "title": "Title e2",
            "url": "https://cdn.test/e2.mp3",
            "published": "2026-02-01T00:00:00Z",
        },
    ]


async def test_another_device_gets_only_newer_changes(client, alice_auth):
    first = await sync(client, alice_auth, listen("e1", 1000))
    await sync(client, alice_auth, listen("e2", 2000), server_modified=first["serverModified"])

    later = await sync(client, alice_auth, server_modified=first["serverModified"])

    assert [change["episode"] for change in later["changes"]] == ["e2"]
    assert later["serverModified"] > first["serverModified"]


async def test_nothing_new_is_a_200_with_the_same_cursor(client, alice_auth):
    first = await sync(client, alice_auth, listen("e1", 1000))

    again = await sync(client, alice_auth, server_modified=first["serverModified"])

    assert again == {
        "serverModified": first["serverModified"],
        "lastCleared": 0,
        "changes": [],
    }


async def test_removing_from_history_wins_over_the_listen_it_removes(client, alice_auth):
    await sync(client, alice_auth, listen("e1", 1000))

    body = await sync(client, alice_auth, listen("e1", 1000, action=2))

    assert [(c["episode"], c["action"]) for c in body["changes"]] == [("e1", 2)]


async def test_an_older_listen_does_not_replace_a_newer_one(client, alice_auth):
    await sync(client, alice_auth, listen("e1", 5000))

    body = await sync(client, alice_auth, listen("e1", 1000))

    assert [c["modifiedAt"] for c in body["changes"]] == ["5000"]


async def test_clearing_history_drops_older_listens(client, alice_auth):
    await sync(client, alice_auth, listen("e1", 1000), listen("e2", 3000))

    cleared = await sync(client, alice_auth, {"action": 3, "modifiedAt": "2000"})
    stale = await sync(client, alice_auth, listen("e3", 1500))

    assert cleared["lastCleared"] == 2000
    assert [c["episode"] for c in cleared["changes"]] == ["e2"]
    assert [c["episode"] for c in stale["changes"]] == ["e2"]


@pytest.mark.parametrize(
    "modified_at",
    ["soon", "-5", "1_000", "\u0661\u0662", " 12 ", "9223372036854775808", "9" * 5000],
    ids=["word", "negative", "underscore", "arabic-digits", "spaces", "over-int64", "5000-digits"],
)
async def test_garbage_modified_at_is_skipped(client, alice_auth, modified_at):
    body = await sync(client, alice_auth, {"action": 1, "modifiedAt": modified_at, "episode": "e1"})

    assert body["changes"] == []


async def test_a_cursor_beyond_int64_is_rejected(client, alice_auth):
    response = await client.post(
        HISTORY, json=history_body(server_modified=2**63), headers=alice_auth
    )

    assert response.status_code == 422


@pytest.mark.parametrize(("year", "expected"), [(2026, 1), (2025, 1), (2024, 0)])
async def test_year_count(client, alice_auth, year, expected):
    await sync(client, alice_auth, listen("e1", LISTENED_2026), listen("e2", LISTENED_2025))

    response = await client.post(
        YEAR, json={"version": "1", "count": True, "year": year}, headers=alice_auth
    )

    assert response.json() == {"count": expected}


async def test_year_history_lists_that_years_listens(client, alice_auth):
    await sync(client, alice_auth, listen("e1", LISTENED_2026), listen("e2", LISTENED_2025))

    response = await client.post(
        YEAR, json={"version": "1", "count": False, "year": 2026}, headers=alice_auth
    )

    history = response.json()["history"]
    assert history["serverModified"] > 0
    assert history["lastCleared"] == 0
    assert [c["episode"] for c in history["changes"]] == ["e1"]


async def test_history_needs_a_token(client):
    assert (await client.post(HISTORY, json=history_body())).status_code == 401


async def test_a_listen_from_the_future_is_clamped_to_now(client, alice_auth):
    before = now_ms()

    body = await sync(client, alice_auth, listen("e1", 2**62))

    [change] = body["changes"]
    assert before <= int(change["modifiedAt"]) <= now_ms()


async def test_a_clear_from_the_future_does_not_block_later_listens(client, alice_auth):
    cleared = await sync(client, alice_auth, {"action": 3, "modifiedAt": str(2**62)})

    assert cleared["lastCleared"] <= now_ms()
    later = await sync(client, alice_auth, listen("e1", cleared["lastCleared"] + 10**9))
    assert [c["episode"] for c in later["changes"]] == ["e1"]


async def test_listens_newer_than_the_clear_in_the_same_request_are_kept(client, alice_auth):
    body = await sync(
        client,
        alice_auth,
        listen("old", 1000),
        {"action": 3, "modifiedAt": "2000"},
        listen("new", 3000),
    )

    assert body["lastCleared"] == 2000
    assert [c["episode"] for c in body["changes"]] == ["new"]


async def test_a_large_batch_is_stored_in_full(client, alice_auth):
    changes = [listen(f"e{index:05d}", 1000 + index) for index in range(1300)]

    body = await sync(client, alice_auth, *changes)
    again = await sync(client, alice_auth, *changes)

    assert len(body["changes"]) == 1300
    assert again["serverModified"] == body["serverModified"]
    assert again["changes"] == body["changes"]


async def test_the_latest_of_repeated_changes_for_an_episode_wins_in_any_order(client, alice_auth):
    body = await sync(
        client, alice_auth, listen("e1", 3000), listen("e1", 1000), listen("e1", 2000, action=2)
    )

    assert [(c["modifiedAt"], c["action"]) for c in body["changes"]] == [("3000", 1)]


async def test_unusable_changes_are_skipped(client, alice_auth):
    body = await sync(
        client,
        alice_auth,
        {"action": 1, "modifiedAt": "1000"},
        {"action": 9, "modifiedAt": "1000", "episode": "e1"},
        {"action": 2, "modifiedAt": "1000", "episode": ""},
        listen("e2", 1000),
    )

    assert [c["episode"] for c in body["changes"]] == ["e2"]


async def test_a_published_date_before_1970_is_dropped(client, alice_auth):
    change = listen("e1", 1000) | {"published": "1901-01-01T00:00:00Z"}

    body = await sync(client, alice_auth, change)

    assert "published" not in body["changes"][0]


async def test_users_do_not_see_each_others_history(client, alice_auth, bob_auth):
    await sync(client, alice_auth, listen("e1", 1000))

    body = await sync(client, bob_auth)

    assert body["changes"] == []


@pytest.mark.parametrize("year", [0, -1, 10000, 2**70])
async def test_year_outside_the_calendar_has_no_listens(client, alice_auth, year):
    response = await client.post(
        YEAR, json={"version": "1", "count": True, "year": year}, headers=alice_auth
    )

    assert response.json() == {"count": 0}


async def test_malformed_history_body_is_a_422(client, alice_auth):
    response = await client.post(HISTORY, json={"changes": "nope"}, headers=alice_auth)

    assert response.status_code == 422
