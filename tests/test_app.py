import asyncio
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from podpocket.app import build_http_client, create_app
from podpocket.db import create_engine
from podpocket.feeds.fetch import GuardedNetworkBackend
from podpocket.feeds.refresher import FeedRefresher
from podpocket.migrate import upgrade_to_head
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
