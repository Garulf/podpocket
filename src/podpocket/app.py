import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI

from podpocket import health
from podpocket.config import USER_AGENT, Settings
from podpocket.db import create_engine, create_sessionmaker
from podpocket.feeds.fetch import guarded_transport
from podpocket.migrate import upgrade_to_head
from podpocket.pcapi import cache, refresh, search
from podpocket.services import build_services


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    options = {"headers": {"User-Agent": USER_AGENT}, "follow_redirects": False, "trust_env": False}
    if settings.allow_private_feeds:
        return httpx.AsyncClient(**options)
    return httpx.AsyncClient(transport=guarded_transport(), **options)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        engine = create_engine(settings.database_url)
        async with build_http_client(settings) as http:
            services = build_services(settings, create_sessionmaker(engine), http)
            app.state.services = services
            scheduler = (
                asyncio.create_task(services.refresher.run_forever())
                if settings.run_scheduler
                else None
            )
            try:
                yield
            finally:
                if scheduler is not None:
                    scheduler.cancel()
                    with suppress(asyncio.CancelledError):
                        await scheduler
                await engine.dispose()

    app = FastAPI(title="Pod Pocket", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(cache.router)
    app.include_router(search.router)
    app.include_router(refresh.router)
    return app


def create_app_from_env() -> FastAPI:
    return create_app(Settings())
