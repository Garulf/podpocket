from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from podpocket.services import Services, get_services

router = APIRouter()


@router.get("/health")
async def health(services: Annotated[Services, Depends(get_services)]) -> dict[str, str]:
    async with services.sessionmaker() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ok"}
