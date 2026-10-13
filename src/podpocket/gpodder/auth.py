import base64
import binascii
from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from podpocket.accounts.dependencies import USER_SCOPE_KEY
from podpocket.accounts.throttle import LoginRefused
from podpocket.accounts.users import normalize_login
from podpocket.models import User
from podpocket.services import Services, get_services
from podpocket.sync.writer import is_clean_text

BASIC_CHALLENGE = {"WWW-Authenticate": 'Basic realm="Pod Pocket", charset="UTF-8"'}
MAX_CREDENTIALS_LENGTH = 4096
RETRY_AFTER = {"Retry-After": "30"}


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="authentication required", headers=BASIC_CHALLENGE)


def basic_credentials(header: str) -> tuple[str, str] | None:
    scheme, _, encoded = header.partition(" ")
    encoded = encoded.strip()
    if scheme.lower() != "basic" or not encoded or len(encoded) > MAX_CREDENTIALS_LENGTH:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    login, separator, password = text.partition(":")
    if not separator or not login or not password or not is_clean_text(text):
        return None
    return login, password


async def authenticate_for_url(request: Request, services: Services, username: str) -> User:
    credentials = basic_credentials(request.headers.get("authorization", ""))
    if credentials is None:
        raise _unauthorized()
    try:
        user = await services.login_gate.authenticate(services.sessionmaker, *credentials)
    except LoginRefused as exc:
        raise HTTPException(
            status_code=429, detail="too many sign-in attempts", headers=RETRY_AFTER
        ) from exc
    if user is None:
        raise _unauthorized()
    if normalize_login(username) not in (user.username, user.email):
        raise HTTPException(status_code=403, detail="the URL names a different user")
    return user


async def gpodder_user(
    request: Request, services: Annotated[Services, Depends(get_services)]
) -> User:
    known: User | None = request.scope.get(USER_SCOPE_KEY)
    if known is not None:
        return known
    return await authenticate_for_url(request, services, request.path_params["username"])


GpodderUser = Annotated[User, Depends(gpodder_user)]


def _needs_user(dependant: Dependant) -> bool:
    return any(sub.call is gpodder_user or _needs_user(sub) for sub in dependant.dependencies)


class GpodderRoute(APIRoute):
    """Signs the caller in before FastAPI reads and parses the request body."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler = super().get_route_handler()
        if not _needs_user(self.dependant):
            return handler

        async def authenticate_then_handle(request: Request) -> Response:
            request.scope[USER_SCOPE_KEY] = await authenticate_for_url(
                request, get_services(request), request.path_params["username"]
            )
            return await handler(request)

        return authenticate_then_handle
