import asyncio
import sqlite3
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from podpocket.app import build_http_client, create_app
from podpocket.db import create_engine
from podpocket.feeds.fetch import GuardedNetworkBackend
from podpocket.feeds.refresher import FeedRefresher
from podpocket.migrate import alembic_config, upgrade_to_head
from podpocket.models import Base


async def test_health_reports_ok(client):
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_migrations_match_models(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'migrated.db'}"
    upgrade_to_head(url)

    async def schema_drift():
        engine = create_async_engine(url)
        async with engine.connect() as connection:
            drift = await connection.run_sync(
                lambda sync: compare_metadata(MigrationContext.configure(sync), Base.metadata)
            )
        await engine.dispose()
        return drift

    assert asyncio.run(schema_drift()) == []


V010_REVISION = "abc09288ab95"
ACCOUNT_TABLES = {"users", "refresh_tokens", "refresh_token_hashes", "change_log", "history"}


def test_upgrading_a_populated_v010_database_keeps_the_catalog(tmp_path):
    path = tmp_path / "v010.db"
    url = f"sqlite+aiosqlite:///{path}"
    command.upgrade(alembic_config(url), V010_REVISION)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO podcasts (uuid, feed_url, title, author, description, description_html,"
            " category, language, show_type, explicit, failure_count)"
            " VALUES ('p1', 'https://feed.test/rss', 'Show', 'Ann', '', '', '', 'en', 'episodic', 0, 0)"
        )
        connection.execute(
            "INSERT INTO episodes (uuid, podcast_uuid, title, enclosure_url, file_type, file_size,"
            " duration, published, first_seen_at, episode_type, show_notes, transcripts)"
            " VALUES ('e1', 'p1', 'One', 'https://feed.test/1.mp3', 'audio/mpeg', 10, 60.0,"
            " '2026-01-01 00:00:00', '2026-01-02 00:00:00', 'full', '', '[]')"
        )
    upgrade_to_head(url)

    with sqlite3.connect(path) as connection:
        podcasts = connection.execute("SELECT uuid, title FROM podcasts").fetchall()
        episodes = connection.execute("SELECT uuid, podcast_uuid, title FROM episodes").fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert podcasts == [("p1", "Show")]
    assert episodes == [("e1", "p1", "One")]
    assert tables >= ACCOUNT_TABLES


async def test_file_databases_get_a_pool_sized_for_concurrent_refreshes(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}")

    assert (engine.pool.size(), engine.pool._max_overflow) == (20, 20)
    await engine.dispose()


@pytest.mark.parametrize("url", ["sqlite+aiosqlite://", "sqlite+aiosqlite:///:memory:"])
async def test_memory_databases_keep_their_default_pool(url):
    engine = create_engine(url)

    async with engine.connect() as connection:
        assert (await connection.exec_driver_sql("SELECT 1")).scalar() == 1
    await engine.dispose()


def pool_backend(client):
    return client._transport._pool._network_backend


async def test_client_blocks_private_networks_by_default(settings):
    async with build_http_client(
        settings.model_copy(update={"allow_private_feeds": False})
    ) as http:
        assert isinstance(pool_backend(http), GuardedNetworkBackend)
        assert http.follow_redirects is False
        assert http._trust_env is False
        assert "PodPocket" in http.headers["User-Agent"]


async def test_client_allows_private_networks_when_configured(settings):
    async with build_http_client(settings.model_copy(update={"allow_private_feeds": True})) as http:
        assert not isinstance(pool_backend(http), GuardedNetworkBackend)
        assert http.follow_redirects is False
        assert http._trust_env is False
        assert "PodPocket" in http.headers["User-Agent"]


@pytest.mark.parametrize("allow_private", [False, True])
async def test_client_allows_thirty_seconds_per_request(settings, allow_private):
    async with build_http_client(
        settings.model_copy(update={"allow_private_feeds": allow_private})
    ) as http:
        timeout = http.timeout
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (10, 30, 30, 30)


@pytest.fixture
def scheduler_probe(monkeypatch):
    probe = SimpleNamespace(started=asyncio.Event(), cancelled=False, calls=0)

    async def run_forever(self):
        probe.calls += 1
        probe.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            probe.cancelled = True
            raise

    monkeypatch.setattr(FeedRefresher, "run_forever", run_forever)
    return probe


async def test_scheduler_runs_during_lifespan_and_is_cancelled_on_exit(settings, scheduler_probe):
    application = create_app(settings.model_copy(update={"run_scheduler": True}))

    async with LifespanManager(application):
        await asyncio.wait_for(scheduler_probe.started.wait(), timeout=5)
        assert scheduler_probe.cancelled is False

    assert scheduler_probe.cancelled is True


async def test_scheduler_does_not_run_when_disabled(settings, scheduler_probe):
    application = create_app(settings.model_copy(update={"run_scheduler": False}))

    async with LifespanManager(application):
        await asyncio.sleep(0)

    assert scheduler_probe.calls == 0
