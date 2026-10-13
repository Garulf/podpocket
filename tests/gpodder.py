import base64

import httpx

from podpocket.ids import episode_uuid
from podpocket.models import UserEpisodeState
from tests.api import PASSWORD
from tests.factories import item, rss
from tests.records import podcast_upload, sync_update

FEED = "https://feeds.test/show.xml"
EPISODE_1 = "https://cdn.example.com/1.mp3"
EPISODE_2 = "https://cdn.example.com/2.mp3"
SHOW = rss(
    item(guid="ep-1", url=EPISODE_1, extra="<itunes:duration>3600</itunes:duration>"),
    item(guid="ep-2", title="Episode 2", url=EPISODE_2, published="Tue, 13 Oct 2026 06:00:00 GMT"),
)


def basic(login: str, password: str = PASSWORD) -> dict[str, str]:
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


async def ingest(respx_mock, services, url: str = FEED, content: bytes = SHOW) -> str:
    respx_mock.get(url).respond(200, content=content)
    return await services.refresher.resolve(url)


def episode_id(podcast_uuid: str, guid: str = "ep-1", url: str = EPISODE_1) -> str:
    return episode_uuid(podcast_uuid, guid, url)


async def subscribe(client: httpx.AsyncClient, auth, podcast_uuid: str, subscribed=True) -> None:
    await sync_update(client, auth, podcast_upload(podcast_uuid, subscribed=subscribed))


async def episode_state(services, user_uuid: str, uuid: str) -> UserEpisodeState | None:
    async with services.sessionmaker() as session:
        return await session.get(UserEpisodeState, (user_uuid, uuid))


async def post_actions(
    client: httpx.AsyncClient, login: str, actions: list, password: str = PASSWORD
) -> httpx.Response:
    return await client.post(
        f"/api/2/episodes/{login}.json", json=actions, headers=basic(login, password)
    )


async def get_actions(client: httpx.AsyncClient, login: str, since: int = 0) -> dict:
    response = await client.get(
        f"/api/2/episodes/{login}.json", params={"since": since}, headers=basic(login)
    )
    assert response.status_code == 200, response.text
    return response.json()
