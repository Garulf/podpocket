import base64
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
from sqlalchemy import event

from podpocket.ids import episode_uuid, podcast_uuid
from podpocket.models import Episode, Podcast, UserEpisodeState
from podpocket.sync.podcasts import PodcastChange, apply_podcast_changes
from podpocket.sync.writer import APP_SOURCE, ChangeWriter
from podpocket.timefmt import now_ms
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


PUBLISHED = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)


def seeded_feed(number: int) -> str:
    return f"https://feeds.test/seeded/{number}.xml"


def seeded_guid(number: int, episode: int) -> str:
    return f"seeded-{number}-{episode}"


def seeded_url(number: int, episode: int) -> str:
    return f"https://cdn.example.com/seeded/{number}/{episode}.mp3"


async def seed_catalog(services, feeds: int, episodes_per_feed: int = 2) -> None:
    async with services.sessionmaker() as session:
        session.add_all(
            Podcast(uuid=podcast_uuid(feed), feed_url=feed)
            for feed in map(seeded_feed, range(feeds))
        )
        await session.flush()
        for number in range(feeds):
            feed = seeded_feed(number)
            for position in range(episodes_per_feed):
                guid, url = seeded_guid(number, position), seeded_url(number, position)
                session.add(
                    Episode(
                        uuid=episode_uuid(podcast_uuid(feed), guid, url),
                        podcast_uuid=podcast_uuid(feed),
                        guid=guid,
                        enclosure_url=url,
                        published=PUBLISHED,
                        first_seen_at=PUBLISHED,
                    )
                )
        await session.commit()


async def write_as(services, user, podcast: str) -> None:
    async with services.sessionmaker() as session:
        writer = await ChangeWriter.begin(session, user.uuid, source=APP_SOURCE, now_ms=now_ms())
        await apply_podcast_changes(writer, [PodcastChange(podcast_uuid=podcast, subscribed=True)])
        await writer.commit()


@contextmanager
def recorded_statements(services, wanted):
    statements: list[str] = []

    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        if wanted(statement.lstrip()):
            statements.append(statement)

    engine = services.sessionmaker.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def catalog_selects(services):
    tables = ("podcasts", "podcast_aliases", "episodes")
    return recorded_statements(
        services,
        lambda statement: (
            statement.startswith("SELECT") and any(f"FROM {table}" in statement for table in tables)
        ),
    )


def change_log_inserts(services):
    return recorded_statements(
        services, lambda statement: statement.startswith("INSERT INTO change_log")
    )
