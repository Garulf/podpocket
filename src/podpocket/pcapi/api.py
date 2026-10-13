from fastapi import APIRouter

from podpocket.pcapi import auth, compat

router = APIRouter()
for module in (auth, compat):
    router.include_router(module.router)
