from fastapi import APIRouter

from podpocket.pcapi import auth, compat, episodes, sync

router = APIRouter()
for module in (auth, compat, episodes, sync):
    router.include_router(module.router)
