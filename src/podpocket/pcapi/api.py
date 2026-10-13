from fastapi import APIRouter

from podpocket.pcapi import auth, compat, episodes, fullsync, sync, upnext

router = APIRouter()
for module in (auth, compat, episodes, sync, fullsync, upnext):
    router.include_router(module.router)
