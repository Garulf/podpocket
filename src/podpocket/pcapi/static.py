import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from podpocket import catalog
from podpocket.artwork import SIZES
from podpocket.directory.itunes import DirectoryError
from podpocket.services import Services, get_services

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/static")
ServicesDep = Annotated[Services, Depends(get_services)]
HOURLY = {"Cache-Control": "public, max-age=3600"}
WEEKLY = {"Cache-Control": "public, max-age=604800"}


def discover_layout(public_url: str, region: str) -> dict[str, Any]:
    lists = f"{public_url}/static/discover/json"
    return {
        "layout": [
            {
                "type": "categories",
                "summary_style": "pills",
                "title": "Categories",
                "source": f"{lists}/categories.json",
                "regions": [region],
            },
            {
                "id": "top",
                "uuid": "top",
                "type": "podcast_list",
                "summary_style": "small_list",
                "expanded_style": "plain_list",
                "title": "Top podcasts",
                "source": f"{lists}/top.json",
                "regions": [region],
            },
        ],
        "regions": {
            region: {
                "name": region.upper(),
                "flag": f"{public_url}/static/discover/images/flags/{region}.png",
                "code": region,
            }
        },
        "region_code_token": "[regionCode]",
        "region_name_token": "[regionName]",
        "default_region_code": region,
    }


@router.get("/discover/blaze/promotions.json")
async def blaze_promotions() -> dict:
    return {"promotions": []}


@router.get("/whats-new/v1/android/{locale}.json")
async def whats_new(locale: str) -> dict:
    return {"schemaVersion": 1, "messages": []}


@router.get("/discover/json/categories.json")
async def categories() -> JSONResponse:
    return JSONResponse([], headers=HOURLY)


@router.get("/discover/json/top.json")
async def top_podcasts(services: ServicesDep) -> JSONResponse:
    try:
        found = await services.directory.top()
    except DirectoryError as exc:
        logger.warning("top chart unavailable: %s", exc)
        found = []
    podcasts = await catalog.save_stubs(services.sessionmaker, found)
    return JSONResponse(
        {
            "title": "Top podcasts",
            "list_id": "top",
            "podcasts": [{"uuid": p.uuid, "title": p.title, "author": p.author} for p in podcasts],
        },
        headers=HOURLY,
    )


@router.get("/discover/images/webp/{size}/{podcast_uuid}.webp")
async def artwork(size: int, podcast_uuid: str, services: ServicesDep) -> FileResponse:
    if size not in SIZES:
        raise HTTPException(status_code=404)
    path = await services.artwork.image(podcast_uuid, size)
    if path is None:
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/webp", headers=WEEKLY)


@router.get("/discover/images/metadata/{podcast_uuid}.json")
async def artwork_colors(podcast_uuid: str, services: ServicesDep) -> dict:
    return {"colors": await services.artwork.colors(podcast_uuid)}


@router.get("/discover/{platform}/{document}.json")
async def discover(platform: str, document: str, services: ServicesDep) -> JSONResponse:
    if not document.startswith("content"):
        raise HTTPException(status_code=404)
    settings = services.settings
    return JSONResponse(discover_layout(settings.public_url, settings.region), headers=HOURLY)
