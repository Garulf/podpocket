import httpx
import pytest
from argon2 import PasswordHasher
from asgi_lifespan import LifespanManager

from podpocket.accounts import passwords
from podpocket.accounts.users import create_user
from podpocket.app import create_app
from podpocket.config import Settings
from tests.api import PASSWORD, sign_in

TEST_SECRET_KEY = "k" * 48
LIFESPAN_TIMEOUT_SECONDS = 60


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    monkeypatch.setattr(
        passwords, "HASHER", PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    )


@pytest.fixture
def settings(tmp_path):
    return Settings(
        public_url="http://pod.test",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        cache_dir=tmp_path / "cache",
        allow_private_feeds=True,
        run_scheduler=False,
        secret_key=TEST_SECRET_KEY,
    )


@pytest.fixture
async def app(settings):
    application = create_app(settings)
    async with LifespanManager(
        application,
        startup_timeout=LIFESPAN_TIMEOUT_SECONDS,
        shutdown_timeout=LIFESPAN_TIMEOUT_SECONDS,
    ):
        yield application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://pod.test") as http:
        yield http


@pytest.fixture
def services(app):
    return app.state.services


@pytest.fixture
async def alice(services):
    return await create_user(services.sessionmaker, "alice", PASSWORD, email="alice@example.com")


@pytest.fixture
async def bob(services):
    return await create_user(services.sessionmaker, "bob", PASSWORD)


@pytest.fixture
async def alice_auth(client, alice):
    return await sign_in(client, "alice")


@pytest.fixture
async def bob_auth(client, bob):
    return await sign_in(client, "bob")
