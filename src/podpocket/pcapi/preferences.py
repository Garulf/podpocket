from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import AfterValidator, BaseModel

from podpocket.accounts.dependencies import CurrentUser
from podpocket.services import Services, get_services
from podpocket.sync.devices import stats_summary
from podpocket.sync.named_settings import update_named_settings
from podpocket.sync.writer import is_clean_text
from podpocket.timefmt import now_ms

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]


def _storable_or_empty(text: str) -> str:
    return text if is_clean_text(text) else ""


class NamedSettingsBody(BaseModel):
    m: str = "Android"
    v: int = 1
    settings: dict[str, Any] = {}


class StatsSummaryBody(BaseModel):
    deviceId: Annotated[str, AfterValidator(_storable_or_empty)] = ""
    deviceType: int = 2


@router.post("/user/named_settings/update")
async def named_settings(
    body: NamedSettingsBody, user: CurrentUser, services: ServicesDep
) -> dict[str, dict[str, Any]]:
    async with services.sessionmaker() as session:
        result = await update_named_settings(session, user.uuid, body.settings, now_ms())
        await session.commit()
    return result


@router.post("/user/stats/summary")
async def stats(body: StatsSummaryBody, user: CurrentUser, services: ServicesDep) -> dict[str, str]:
    async with services.sessionmaker() as session:
        return await stats_summary(session, user.uuid, body.deviceId)
