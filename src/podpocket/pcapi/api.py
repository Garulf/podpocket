from fastapi import APIRouter

from podpocket.pcapi import auth, compat, episodes

router = APIRouter()
for module in (auth, compat, episodes):
    router.include_router(module.router)
