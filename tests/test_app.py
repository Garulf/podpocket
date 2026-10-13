import asyncio

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from asgi_lifespan import LifespanManager
from sqlalchemy.ext.asyncio import create_async_engine

from podpocket.app import build_http_client, create_app
from podpocket.feeds.fetch import GuardedNetworkBackend
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


def pool_backend(client):
    return client._transport._pool._network_backend


async def test_client_blocks_private_networks_by_default(settings):
    async with build_http_client(
        settings.model_copy(update={"allow_private_feeds": False})
    ) as http:
        assert isinstance(pool_backend(http), GuardedNetworkBackend)
        assert http.follow_redirects is False
        assert "PodPocket" in http.headers["User-Agent"]


async def test_client_allows_private_networks_when_configured(settings):
    async with build_http_client(settings.model_copy(update={"allow_private_feeds": True})) as http:
        assert not isinstance(pool_backend(http), GuardedNetworkBackend)
        assert http.follow_redirects is False
        assert "PodPocket" in http.headers["User-Agent"]


async def test_lifespan_exposes_refresher(services):
    assert services.refresher is not None


async def test_scheduler_starts_and_stops_with_the_app(settings):
    application = create_app(settings.model_copy(update={"run_scheduler": True}))

    async with LifespanManager(application):
        assert application.state.services.refresher is not None
