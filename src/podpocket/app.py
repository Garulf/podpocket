import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from podpocket import health
from podpocket.config import USER_AGENT, Settings
from podpocket.db import create_engine, create_sessionmaker
from podpocket.migrate import upgrade_to_head
from podpocket.services import build_services


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(upgrade_to_head, settings.database_url)
        engine = create_engine(settings.database_url)
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as http:
            app.state.services = build_services(settings, create_sessionmaker(engine), http)
            try:
                yield
            finally:
                await engine.dispose()

    app = FastAPI(title="Pod Pocket", lifespan=lifespan)
    app.include_router(health.router)
    return app


def create_app_from_env() -> FastAPI:
    return create_app(Settings())
