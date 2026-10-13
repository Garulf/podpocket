from dataclasses import dataclass
from datetime import timedelta

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.config import Settings
from podpocket.directory.itunes import ITunesDirectory
from podpocket.feeds.fetch import FetchPolicy
from podpocket.feeds.refresher import FeedRefresher
from podpocket.opml import OpmlImports


@dataclass
class Services:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    refresher: FeedRefresher
    directory: ITunesDirectory
    imports: OpmlImports


def build_services(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> Services:
    feed_policy = FetchPolicy(
        max_bytes=settings.max_feed_bytes, allow_private=settings.allow_private_feeds
    )
    refresher = FeedRefresher(
        sessionmaker, http, feed_policy, timedelta(minutes=settings.refresh_interval_minutes)
    )
    return Services(
        settings=settings,
        sessionmaker=sessionmaker,
        http=http,
        refresher=refresher,
        directory=ITunesDirectory(http, settings.region),
        imports=OpmlImports(refresher.resolve),
    )


def get_services(request: Request) -> Services:
    return request.app.state.services
