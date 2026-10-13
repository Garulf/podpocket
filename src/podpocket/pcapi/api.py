from fastapi import APIRouter

from podpocket.pcapi import auth, compat, episodes, fullsync, history, sync, upnext

router = APIRouter()
for module in (auth, compat, episodes, sync, fullsync, upnext, history):
    router.include_router(module.router)
