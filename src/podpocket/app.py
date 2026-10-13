import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from podpocket import health
from podpocket.accounts.dependencies import CHALLENGE
from podpocket.config import USER_AGENT, Settings
from podpocket.db import create_engine, create_sessionmaker
from podpocket.feeds.fetch import guarded_transport
from podpocket.gpodder.router import router as gpodder_router
from podpocket.migrate import upgrade_to_head
from podpocket.pcapi import api, cache, refresh, search, static
from podpocket.pcapi.wire import MAX_REQUEST_BYTES, BodySizeLimit
from podpocket.services import build_services
from podpocket.sync.writer import UserGone

HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    options = {
        "headers": {"User-Agent": USER_AGENT},
        "follow_redirects": False,
        "trust_env": False,
        "timeout": HTTP_TIMEOUT,
    }
    if settings.allow_private_feeds:
        return httpx.AsyncClient(**options)
    return httpx.AsyncClient(transport=guarded_transport(), **options)


async def account_gone(request: Request, exc: UserGone) -> JSONResponse:
    return JSONResponse({"detail": "account no longer exists"}, status_code=401, headers=CHALLENGE)


def _printable(value: object) -> str:
    return str(value).encode("utf-8", "backslashreplace").decode()


def _error_location(location: tuple[int | str, ...]) -> list[int | str]:
    return [part if isinstance(part, int) else _printable(part) for part in location]


async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {
            "loc": _error_location(error["loc"]),
            "msg": _printable(error["msg"]),
            "type": _printable(error["type"]),
        }
        for error in exc.errors()
    ]
    return JSONResponse({"detail": errors}, status_code=422)


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
    app.add_exception_handler(UserGone, account_gone)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_middleware(BodySizeLimit, max_bytes=MAX_REQUEST_BYTES)
    app.include_router(health.router)
    app.include_router(cache.router)
    app.include_router(search.router)
    app.include_router(refresh.router)
    app.include_router(static.router)
    app.include_router(api.router)
    app.include_router(gpodder_router)
    return app


def create_app_from_env() -> FastAPI:
    return create_app(Settings())
