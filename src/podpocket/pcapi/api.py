from fastapi import APIRouter

from podpocket.pcapi import auth, compat, episodes, fullsync, sync

router = APIRouter()
for module in (auth, compat, episodes, sync, fullsync):
    router.include_router(module.router)
