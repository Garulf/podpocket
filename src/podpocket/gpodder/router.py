from fastapi import APIRouter
from fastapi.responses import Response

from podpocket.gpodder.auth import GpodderRoute, GpodderUser

router = APIRouter(prefix="/api/2", route_class=GpodderRoute)


@router.post("/auth/{username}/login.json")
async def login(user: GpodderUser) -> Response:
    return Response(status_code=200)


@router.post("/auth/{username}/logout.json")
async def logout(user: GpodderUser) -> Response:
    return Response(status_code=200)
