from fastapi import APIRouter

from podpocket.pcapi import (
    auth,
    compat,
    episode_sources,
    episodes,
    fullsync,
    history,
    preferences,
    sync,
    upnext,
)

router = APIRouter()
for module in (
    auth,
    compat,
    episodes,
    episode_sources,
    sync,
    fullsync,
    upnext,
    history,
    preferences,
):
    router.include_router(module.router)
