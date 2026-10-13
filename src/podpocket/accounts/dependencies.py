from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from podpocket.accounts.tokens import InvalidToken
from podpocket.models import User
from podpocket.services import Services, get_services

CHALLENGE = {"WWW-Authenticate": "Bearer"}
USER_SCOPE_KEY = "podpocket.user"


async def current_user(
    request: Request, services: Annotated[Services, Depends(get_services)]
) -> User:
    known: User | None = request.scope.get(USER_SCOPE_KEY)
    if known is not None:
        return known
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="missing bearer token", headers=CHALLENGE)
    try:
        return await services.tokens.authenticate(token)
    except InvalidToken as exc:
        raise HTTPException(status_code=401, detail=str(exc), headers=CHALLENGE) from exc


CurrentUser = Annotated[User, Depends(current_user)]


def _needs_user(dependant: Dependant) -> bool:
    return any(sub.call is current_user or _needs_user(sub) for sub in dependant.dependencies)


class AuthenticatedRoute(APIRoute):
    """Signs the caller in before FastAPI reads and parses the request body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        if not _needs_user(self.dependant):
            return handler

        async def authenticate_then_handle(request: Request) -> Response:
            request.scope[USER_SCOPE_KEY] = await current_user(request, get_services(request))
            return await handler(request)

        return authenticate_then_handle
