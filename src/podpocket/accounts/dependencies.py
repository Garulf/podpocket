from typing import Annotated

from fastapi import Depends, HTTPException, Request

from podpocket.accounts.tokens import InvalidToken
from podpocket.models import User
from podpocket.services import Services, get_services

CHALLENGE = {"WWW-Authenticate": "Bearer"}


async def current_user(
    request: Request, services: Annotated[Services, Depends(get_services)]
) -> User:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token", headers=CHALLENGE)
    try:
        return await services.tokens.authenticate(token)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=CHALLENGE) from exc


CurrentUser = Annotated[User, Depends(current_user)]
