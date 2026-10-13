from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from podpocket import catalog
from podpocket.discovery import find_podcasts
from podpocket.feeds.errors import FeedError
from podpocket.models import Podcast
from podpocket.pcapi import serializers
from podpocket.pcapi.wire import EncodableText
from podpocket.services import Services, get_services

router = APIRouter(prefix="/cache")
ServicesDep = Annotated[Services, Depends(get_services)]
CACHEABLE = {"Cache-Control": "public, max-age=300"}


class PodcastEpisodeSearch(BaseModel):
    podcastuuid: EncodableText
    searchterm: EncodableText


class TermSearch(BaseModel):
    term: EncodableText


async def _fetched_podcast(services: Services, podcast_uuid: str) -> Podcast:
    try:
        podcast = await services.refresher.ensure_fetched(podcast_uuid)
    except FeedError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if podcast is None:
        raise HTTPException(status_code=404)
    return podcast


@router.get("/mobile/podcast/full/{podcast_uuid}")
async def podcast_full(podcast_uuid: str, services: ServicesDep) -> JSONResponse:
    podcast = await _fetched_podcast(services, podcast_uuid)
    await services.refresher.touch([podcast_uuid])
    async with services.sessionmaker() as session:
        episodes = await catalog.list_episodes(session, podcast_uuid)
    return JSONResponse(serializers.podcast_full(podcast, episodes), headers=CACHEABLE)


@router.get("/mobile/podcast/findbyepisode/{podcast_uuid}/{episode_uuid}")
async def find_by_episode(podcast_uuid: str, episode_uuid: str, services: ServicesDep) -> dict:
    podcast = await _fetched_podcast(services, podcast_uuid)
    async with services.sessionmaker() as session:
        episode = await catalog.find_episode(session, podcast_uuid, episode_uuid)
    return serializers.podcast_full(podcast, [episode] if episode is not None else [])


@router.get("/mobile/episode/url/{podcast_uuid}/{episode_uuid}")
async def episode_url(
    podcast_uuid: str, episode_uuid: str, services: ServicesDep
) -> PlainTextResponse:
    async with services.sessionmaker() as session:
        episode = await catalog.find_episode(session, podcast_uuid, episode_uuid)
    if episode is None:
        raise HTTPException(status_code=404)
    return PlainTextResponse(episode.enclosure_url)


@router.get("/mobile/show_notes/full/{podcast_uuid}")
async def show_notes_location(podcast_uuid: str, services: ServicesDep) -> JSONResponse:
    url = f"{services.settings.public_url}/cache/mobile/show_notes/data/{podcast_uuid}.json"
    return JSONResponse({"url": url}, headers=CACHEABLE)


@router.get("/mobile/show_notes/data/{podcast_uuid}.json")
async def show_notes(podcast_uuid: str, services: ServicesDep) -> JSONResponse:
    await _fetched_podcast(services, podcast_uuid)
    async with services.sessionmaker() as session:
        episodes = await catalog.list_episodes(session, podcast_uuid)
    return JSONResponse(serializers.show_notes(podcast_uuid, episodes), headers=CACHEABLE)


@router.post("/mobile/podcast/episode/search")
async def search_podcast_episodes(body: PodcastEpisodeSearch, services: ServicesDep) -> dict:
    async with services.sessionmaker() as session:
        matches = await catalog.search_episodes(
            session, body.searchterm, podcast_uuid=body.podcastuuid, limit=None
        )
    return {"episodes": [{"uuid": episode.uuid} for episode, _ in matches]}


@router.post("/episode/search")
async def search_all_episodes(body: TermSearch, services: ServicesDep) -> dict:
    async with services.sessionmaker() as session:
        matches = await catalog.search_episodes(session, body.term)
    return {
        "episodes": [
            serializers.episode_search_result(episode, podcast) for episode, podcast in matches
        ]
    }


@router.post("/v2/search/combined")
async def combined_search(body: TermSearch, services: ServicesDep) -> dict:
    term = body.term.strip()
    if not term:
        return {"results": []}
    podcasts = await find_podcasts(services, term, directory_limit=20)
    async with services.sessionmaker() as session:
        episodes = await catalog.search_episodes(session, term, limit=20)
    return {
        "results": [
            *(
                {"type": "podcast", "uuid": p.uuid, "title": p.title, "author": p.author}
                for p in podcasts
            ),
            *(serializers.combined_episode(episode, podcast) for episode, podcast in episodes),
        ]
    }
