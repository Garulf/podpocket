from fastapi import APIRouter

from podpocket.pcapi import (
    auth,
    compat,
    episodes,
    fullsync,
    history,
    preferences,
    sync,
    upnext,
)

router = APIRouter()
for module in (auth, compat, episodes, sync, fullsync, upnext, history, preferences):
    router.include_router(module.router)
