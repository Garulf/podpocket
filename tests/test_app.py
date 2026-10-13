import asyncio

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import create_async_engine

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
