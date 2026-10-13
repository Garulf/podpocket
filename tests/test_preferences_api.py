import pytest

from tests.records import device_upload, sync_update

NAMED = "/api/user/named_settings/update"
STATS = "/api/user/stats/summary"
INT64_MAX = 2**63 - 1
ZERO_STATS = {
    "timeSilenceRemoval": "0",
    "timeSkipping": "0",
    "timeIntroSkipping": "0",
    "timeVariableSpeed": "0",
    "timeListened": "0",
}


def settings_body(**settings):
    return {"m": "Android", "v": 1, "settings": settings}


async def test_sent_settings_are_stored_and_not_marked_changed(client, alice_auth):
    response = await client.post(
        NAMED, json=settings_body(skipForward=45, gridOrder=3), headers=alice_auth
    )

    assert response.json() == {
        "gridOrder": {"value": 3, "changed": False},
        "skipForward": {"value": 45, "changed": False},
    }


async def test_settings_from_another_device_come_back_as_changed(client, alice_auth):
    await client.post(NAMED, json=settings_body(skipForward=45, audioOnly=True), headers=alice_auth)

    response = await client.post(NAMED, json=settings_body(), headers=alice_auth)

    assert response.json() == {
        "audioOnly": {"value": True, "changed": True},
        "skipForward": {"value": 45, "changed": True},
    }


async def test_a_newer_value_replaces_the_stored_one(client, alice_auth):
    await client.post(NAMED, json=settings_body(skipForward=45, gridOrder=3), headers=alice_auth)

    response = await client.post(NAMED, json=settings_body(skipForward=15), headers=alice_auth)

    assert response.json() == {
        "gridOrder": {"value": 3, "changed": True},
        "skipForward": {"value": 15, "changed": False},
    }


async def test_null_and_nested_values_are_ignored(client, alice_auth):
    response = await client.post(
        NAMED, json=settings_body(skipBack=None, nested={"a": 1}), headers=alice_auth
    )

    assert response.json() == {}


async def test_non_finite_numbers_are_ignored(client, alice_auth):
    body = b'{"m": "Android", "v": 1, "settings": {"speed": NaN, "trim": Infinity, "skipBack": 10}}'

    response = await client.post(
        NAMED, content=body, headers={**alice_auth, "Content-Type": "application/json"}
    )

    assert response.json() == {"skipBack": {"value": 10, "changed": False}}


async def test_named_settings_need_a_token(client):
    assert (await client.post(NAMED, json=settings_body())).status_code == 401


async def test_integers_beyond_int64_are_ignored(client, alice_auth):
    body = b'{"settings": {"big": 9223372036854775808, "small": -9223372036854775809, "ok": 7}}'

    response = await client.post(
        NAMED, content=body, headers={**alice_auth, "Content-Type": "application/json"}
    )

    assert response.json() == {"ok": {"value": 7, "changed": False}}


async def test_the_largest_integer_is_kept(client, alice_auth):
    response = await client.post(
        NAMED, json=settings_body(limit=INT64_MAX, floor=-INT64_MAX - 1), headers=alice_auth
    )

    assert response.json() == {
        "floor": {"value": -INT64_MAX - 1, "changed": False},
        "limit": {"value": INT64_MAX, "changed": False},
    }


async def test_names_and_strings_that_cannot_be_stored_are_ignored(client, alice_auth):
    body = (
        b'{"settings": {"nul\\u0000": 1, "lone": "\\ud800", "zero": "a\\u0000b",'
        b' "": 2, "' + b"n" * 65 + b'": 3, "fine": "text"}}'
    )

    response = await client.post(
        NAMED, content=body, headers={**alice_auth, "Content-Type": "application/json"}
    )

    assert response.json() == {"fine": {"value": "text", "changed": False}}


@pytest.mark.parametrize("body", [{"settings": []}, {"settings": "x"}, [], {"v": "one"}])
async def test_malformed_named_settings_body_is_a_422(client, alice_auth, body):
    response = await client.post(NAMED, json=body, headers=alice_auth)

    assert response.status_code == 422


async def test_users_do_not_see_each_others_settings(client, alice_auth, bob_auth):
    await client.post(NAMED, json=settings_body(skipForward=45), headers=alice_auth)

    response = await client.post(NAMED, json=settings_body(), headers=bob_auth)

    assert response.json() == {}


async def test_stats_summary_sums_the_other_devices_as_strings(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        device_upload("phone", listened=100, skipping=5, started_at=1_700_000_000),
        device_upload("tablet", listened=40, started_at=1_600_000_000),
    )

    response = await client.post(
        STATS, json={"deviceId": "phone", "deviceType": 2}, headers=alice_auth
    )

    assert response.json() == {
        **ZERO_STATS,
        "timeListened": "40",
        "timesStartedAt": "2020-09-13T12:26:40Z",
    }


async def test_an_impossible_start_time_is_left_out(client, alice_auth):
    await sync_update(client, alice_auth, device_upload("tablet", listened=5, started_at=2**62))

    response = await client.post(
        STATS, json={"deviceId": "phone", "deviceType": 2}, headers=alice_auth
    )

    assert response.json() == {**ZERO_STATS, "timeListened": "5"}


async def test_stats_summary_always_has_every_numeric_key(client, alice_auth):
    response = await client.post(
        STATS, json={"deviceId": "phone", "deviceType": 2}, headers=alice_auth
    )

    assert response.json() == ZERO_STATS


async def test_stats_totals_saturate_at_int64_max(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        device_upload("tablet", listened=INT64_MAX, skipping=INT64_MAX),
        device_upload("watch", listened=INT64_MAX, skipping=1),
        device_upload("phone", listened=1),
    )

    response = await client.post(
        STATS, json={"deviceId": "phone", "deviceType": 2}, headers=alice_auth
    )

    assert response.json() == {
        **ZERO_STATS,
        "timeListened": str(INT64_MAX),
        "timeSkipping": str(INT64_MAX),
    }


async def test_stats_of_other_users_are_not_counted(client, alice_auth, bob_auth):
    await sync_update(client, bob_auth, device_upload("bobs-phone", listened=99))

    response = await client.post(
        STATS, json={"deviceId": "phone", "deviceType": 2}, headers=alice_auth
    )

    assert response.json() == ZERO_STATS


@pytest.mark.parametrize("escaped", ["a\\u0000b", "\\ud800"])
async def test_a_device_id_that_cannot_exist_excludes_nothing(client, alice_auth, escaped):
    await sync_update(client, alice_auth, device_upload("tablet", listened=5))
    body = ('{"deviceId": "' + escaped + '", "deviceType": 2}').encode()

    response = await client.post(
        STATS, content=body, headers={**alice_auth, "Content-Type": "application/json"}
    )

    assert response.json() == {**ZERO_STATS, "timeListened": "5"}


async def test_stats_need_a_token(client):
    assert (await client.post(STATS, json={"deviceId": "phone"})).status_code == 401


RAW_JSON = {"Content-Type": "application/json"}


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (NAMED, b'{"v": "\\ud800"}'),
        (NAMED, b'{"settings": "\\ud800"}'),
        (STATS, b'{"deviceType": "\\ud800"}'),
        (STATS, b'{"deviceId": 5, "deviceType": "\\ud800"}'),
        ("/api/user/login_pocket_casts", b'{"email": 5, "password": "\\ud800"}'),
        ("/cache/episode/search", b'{"term": 7, "x": "\\ud800"}'),
    ],
    ids=["named-v", "named-settings", "stats-type", "stats-id-and-type", "login", "cache-search"],
)
async def test_validation_errors_never_echo_the_input(client, alice_auth, path, body):
    response = await client.post(path, content=body, headers={**alice_auth, **RAW_JSON})

    assert response.status_code == 422
    assert "ud800" not in response.text.lower()
    assert all(set(error) == {"loc", "msg", "type"} for error in response.json()["detail"])


async def test_a_surrogate_in_an_error_location_is_escaped(client, alice_auth):
    body = b'{"deviceId": {"\\ud800": 1}}'

    response = await client.post(STATS, content=body, headers={**alice_auth, **RAW_JSON})

    assert response.status_code == 422


async def test_at_most_500_settings_are_stored_per_user(client, alice_auth):
    first = {f"s{index:03d}": index for index in range(500)}
    await client.post(NAMED, json=settings_body(**first), headers=alice_auth)

    response = await client.post(NAMED, json=settings_body(extra=1, s000=99), headers=alice_auth)

    stored = response.json()
    assert len(stored) == 500
    assert "extra" not in stored
    assert stored["s000"] == {"value": 99, "changed": False}


async def test_strings_over_8192_characters_are_ignored(client, alice_auth):
    response = await client.post(
        NAMED, json=settings_body(long="x" * 8193, edge="y" * 8192), headers=alice_auth
    )

    assert response.json() == {"edge": {"value": "y" * 8192, "changed": False}}


async def test_a_finite_float_round_trips(client, alice_auth):
    response = await client.post(NAMED, json=settings_body(speed=1.25), headers=alice_auth)

    assert response.json() == {"speed": {"value": 1.25, "changed": False}}


async def test_the_earliest_start_across_devices_is_reported(client, alice_auth):
    await sync_update(
        client,
        alice_auth,
        device_upload("a", listened=1, started_at=1_700_000_000),
        device_upload("b", listened=1, started_at=1_600_000_000),
        device_upload("c", listened=1, started_at=1_650_000_000),
    )

    response = await client.post(STATS, json={"deviceId": "phone"}, headers=alice_auth)

    assert response.json()["timesStartedAt"] == "2020-09-13T12:26:40Z"


async def test_a_start_time_of_zero_is_left_out(client, alice_auth):
    await sync_update(client, alice_auth, device_upload("a", listened=1, started_at=0))

    response = await client.post(STATS, json={"deviceId": "phone"}, headers=alice_auth)

    assert "timesStartedAt" not in response.json()
