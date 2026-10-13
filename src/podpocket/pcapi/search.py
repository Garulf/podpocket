from typing import Annotated

from fastapi import APIRouter, Depends

from podpocket.discovery import find_podcasts
from podpocket.services import Services, get_services

router = APIRouter(prefix="/search")
MIN_DIRECTORY_TERM = 3
DIRECTORY_SUGGESTIONS = 5
MAX_SUGGESTIONS = 8


@router.get("/autocomplete/search")
async def autocomplete(services: Annotated[Services, Depends(get_services)], q: str = "") -> dict:
    term = q.strip()
    if not term:
        return {"results": []}
    directory_limit = DIRECTORY_SUGGESTIONS if len(term) >= MIN_DIRECTORY_TERM else 0
    found = await find_podcasts(services, term, directory_limit=directory_limit)
    return {
        "results": [
            {
                "type": "podcast",
                "value": {"uuid": p.uuid, "title": p.title, "author": p.author},
            }
            for p in found[:MAX_SUGGESTIONS]
        ]
    }
