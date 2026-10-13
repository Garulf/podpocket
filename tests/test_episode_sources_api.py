import pytest

from podpocket.catalog import list_episodes
from tests.factories import item, rss

URL = "/api/podpocket/episode_sources"
FEED = "https://feeds.test/show.xml"
WITH_GUID = "https://cdn.example.com/2.mp3"
WITHOUT_GUID = "https://cdn.example.com/1.mp3"

SHOW = rss(
    item(guid="ep-2", title="Second", url=WITH_GUID, published="Tue, 13 Oct 2026 06:00:00 GMT"),
    item(guid=None, title="First", url=WITHOUT_GUID),
)


@pytest.fixture
async def episodes(respx_mock, services):
    respx_mock.get(FEED).respond(200, content=SHOW)
    podcast = await services.refresher.resolve(FEED)
    async with services.sessionmaker() as session:
        return {
            episode.enclosure_url: episode.uuid for episode in await list_episodes(session, podcast)
        }


async def test_lists_feed_url_guid_and_enclosure(client, alice_auth, episodes):
    with_guid, without_guid = episodes[WITH_GUID], episodes[WITHOUT_GUID]

    response = await client.post(URL, json={"uuids": [with_guid, without_guid]}, headers=alice_auth)

    assert response.status_code == 200
    assert sorted(response.json()["episodes"], key=lambda e: e["enclosure_url"]) == [
        {"uuid": without_guid, "feed_url": FEED, "guid": None, "enclosure_url": WITHOUT_GUID},
        {"uuid": with_guid, "feed_url": FEED, "guid": "ep-2", "enclosure_url": WITH_GUID},
    ]


async def test_orders_by_uuid(client, alice_auth, episodes):
    uuids = sorted(episodes.values())

    response = await client.post(URL, json={"uuids": uuids[::-1]}, headers=alice_auth)

    assert [episode["uuid"] for episode in response.json()["episodes"]] == uuids


async def test_leaves_out_unknown_and_repeated_uuids(client, alice_auth, episodes):
    known = episodes[WITH_GUID]

    response = await client.post(
        URL,
        json={"uuids": [known, known, "00000000-0000-0000-0000-000000000000"]},
        headers=alice_auth,
    )

    assert [episode["uuid"] for episode in response.json()["episodes"]] == [known]


async def test_answers_an_empty_request_with_no_episodes(client, alice_auth):
    response = await client.post(URL, json={"uuids": []}, headers=alice_auth)

    assert response.status_code == 200
    assert response.json() == {"episodes": []}


async def test_accepts_the_most_uuids_allowed(client, alice_auth, episodes):
    known = episodes[WITH_GUID]
    uuids = [known] + [f"{number:036d}" for number in range(499)]

    response = await client.post(URL, json={"uuids": uuids}, headers=alice_auth)

    assert response.status_code == 200
    assert [episode["uuid"] for episode in response.json()["episodes"]] == [known]


async def test_describes_episodes_for_any_signed_in_user(client, episodes, alice_auth, bob_auth):
    known = episodes[WITH_GUID]

    alice = await client.post(URL, json={"uuids": [known]}, headers=alice_auth)
    bob = await client.post(URL, json={"uuids": [known]}, headers=bob_auth)

    assert alice.json() == bob.json()
    assert set(alice.json()["episodes"][0]) == {"uuid", "feed_url", "guid", "enclosure_url"}


async def test_needs_a_signed_in_user(client, episodes):
    response = await client.post(URL, json={"uuids": [episodes[WITH_GUID]]})

    assert response.status_code == 401


async def test_signs_in_before_reading_an_oversized_body(client):
    response = await client.post(URL, json={"uuids": ["a"] * 501})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"uuids": ["a"] * 501},
        {"uuids": ["x" * 37]},
        {"uuids": "abc"},
        {"uuids": [1]},
        {"uuids": None},
        {},
    ],
)
async def test_rejects_malformed_requests(client, alice_auth, body):
    response = await client.post(URL, json=body, headers=alice_auth)

    assert response.status_code == 422


async def test_rejects_a_lone_surrogate(client, alice_auth):
    response = await client.post(
        URL,
        content=b'{"uuids": ["\\ud800"]}',
        headers={**alice_auth, "content-type": "application/json"},
    )

    assert response.status_code == 422


async def test_rejects_unparseable_json_without_echoing_it(client, alice_auth):
    response = await client.post(
        URL,
        content=b'{"uuids": ["secret-input"',
        headers={**alice_auth, "content-type": "application/json"},
    )

    assert response.status_code == 422
    assert "secret-input" not in response.text


async def test_does_not_echo_rejected_values(client, alice_auth):
    response = await client.post(URL, json={"uuids": ["secret-input" * 5]}, headers=alice_auth)

    assert response.status_code == 422
    assert "secret-input" not in response.text
