from itertools import zip_longest
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from podpocket import catalog
from podpocket.discovery import find_podcasts, looks_like_url, resolve_user_url
from podpocket.feeds.errors import FeedError
from podpocket.models import Podcast
from podpocket.pcapi import envelope, serializers
from podpocket.services import Services, get_services

router = APIRouter(prefix="/refresh")
ServicesDep = Annotated[Services, Depends(get_services)]
NO_KNOWN_EPISODE = frozenset({"", "null"})


class ImportOpmlRequest(BaseModel):
    urls: list[str] | None = None
    poll_uuids: list[str] | None = None


def _split(value: str) -> list[str]:
    return value.split(",") if value else []


@router.post("/user/update")
async def refresh_podcasts(
    services: ServicesDep,
    podcasts: Annotated[str, Form()] = "",
    last_episodes: Annotated[str, Form()] = "",
) -> JSONResponse:
    pairs = [
        (uuid, last)
        for uuid, last in zip_longest(_split(podcasts), _split(last_episodes), fillvalue="")
        if uuid
    ]
    await services.refresher.touch(uuid for uuid, _ in pairs)
    updates = {}
    async with services.sessionmaker() as session:
        for uuid, last in pairs:
            episodes = await catalog.episodes_after(
                session, uuid, None if last in NO_KNOWN_EPISODE else last
            )
            if episodes:
                updates[uuid] = [serializers.refresh_episode(episode) for episode in episodes]
    return envelope.ok({"podcast_updates": updates})


@router.post("/podcasts/search")
async def search_podcasts(services: ServicesDep, q: Annotated[str, Form()] = "") -> JSONResponse:
    term = q.strip()
    if not term:
        return envelope.ok({"is_url": False, "search_results": []})
    if looks_like_url(term):
        try:
            uuid = await resolve_user_url(services, term)
        except FeedError as exc:
            return envelope.error(f"Could not add that podcast: {exc}")
        async with services.sessionmaker() as session:
            podcast = await session.get(Podcast, uuid)
        return envelope.ok(
            {
                "is_url": True,
                "podcast": {"uuid": podcast.uuid, "title": podcast.title, "author": podcast.author},
                "search_results": [],
            }
        )
    found = await find_podcasts(services, term, directory_limit=20)
    return envelope.ok(
        {
            "is_url": False,
            "search_results": [
                {"uuid": p.uuid, "title": p.title, "author": p.author} for p in found
            ],
        }
    )


@router.post("/import/opml")
async def import_opml(body: ImportOpmlRequest, services: ServicesDep) -> JSONResponse:
    if body.poll_uuids:
        batch = await services.imports.poll(body.poll_uuids)
    else:
        batch = await services.imports.start(body.urls or [])
    return envelope.ok(
        {"uuids": batch.uuids, "poll_uuids": batch.poll_uuids, "failed": batch.failed}
    )


@router.post("/import/export_feed_urls")
async def export_feed_urls(
    services: ServicesDep, uuids: Annotated[str, Form()] = ""
) -> JSONResponse:
    async with services.sessionmaker() as session:
        rows = await session.execute(
            select(Podcast.uuid, Podcast.feed_url).where(Podcast.uuid.in_(_split(uuids)))
        )
    return envelope.ok({uuid: feed_url for uuid, feed_url in rows})


@router.get("/api/v1/update_podcast")
async def check_podcast(
    services: ServicesDep, podcast_uuid: str, last_episode_uuid: str | None = None
) -> Response:
    try:
        await services.refresher.refresh(podcast_uuid)
    except LookupError:
        return Response(status_code=404)
    except FeedError:
        return Response(status_code=204)
    async with services.sessionmaker() as session:
        newest = await catalog.newest_episode(session, podcast_uuid)
    found = newest is not None and newest.uuid != last_episode_uuid
    return Response(status_code=200 if found else 204)
