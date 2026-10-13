from dataclasses import dataclass

import httpx
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from podpocket.config import Settings


@dataclass
class Services:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient


def build_services(
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    http: httpx.AsyncClient,
) -> Services:
    return Services(settings=settings, sessionmaker=sessionmaker, http=http)


def get_services(request: Request) -> Services:
    return request.app.state.services
