import httpx
import pytest
from asgi_lifespan import LifespanManager

from podpocket.app import create_app
from podpocket.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        public_url="http://pod.test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        cache_dir=tmp_path / "cache",
        allow_private_feeds=True,
        run_scheduler=False,
    )


@pytest.fixture
async def app(settings):
    application = create_app(settings)
    async with LifespanManager(application):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pod.test") as http:
        yield http


@pytest.fixture
def services(app):
    return app.state.services
