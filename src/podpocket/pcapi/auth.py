from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from podpocket.accounts.dependencies import CurrentUser
from podpocket.accounts.tokens import InvalidToken, IssuedTokens
from podpocket.accounts.users import authenticate
from podpocket.models import User
from podpocket.services import Services, get_services
from podpocket.timefmt import iso_z

router = APIRouter(prefix="/api")
ServicesDep = Annotated[Services, Depends(get_services)]
FAR_FUTURE = "2099-12-31T00:00:00Z"
REFRESH_GRANT = "refresh_token"


class LoginRequest(BaseModel):
    email: str = ""
    password: str = ""
    scope: str = Field(default="mobile", max_length=32)


def login_error(status_code: int, message: str, message_id: str) -> JSONResponse:
    return JSONResponse(
        {"errorMessage": message, "errorMessageId": message_id}, status_code=status_code
    )


def token_error(error: str, description: str) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=400)


def token_body(user: User, issued: IssuedTokens) -> dict[str, Any]:
    return {
        "email": user.email or user.username,
        "uuid": user.uuid,
        "isNew": False,
        "accessToken": issued.access_token,
        "refreshToken": issued.refresh_token,
        "tokenType": "Bearer",
        "expiresIn": issued.expires_in,
    }


@router.post("/user/login_pocket_casts")
async def login(body: LoginRequest, services: ServicesDep) -> JSONResponse:
    if not body.email.strip():
        return login_error(400, "Enter your username or email.", "login_email_blank")
    if not body.password:
        return login_error(400, "Enter your password.", "login_password_blank")
    user = await authenticate(services.sessionmaker, body.email, body.password)
    if user is None:
        return login_error(401, "Incorrect username or password.", "login_password_incorrect")
    issued = await services.tokens.issue(user, body.scope)
    return JSONResponse(token_body(user, issued))


@router.post("/user/token")
async def token(request: Request, services: ServicesDep) -> JSONResponse:
    try:
        body = await request.json()
    except (ValueError, RecursionError):
        body = None
    if not isinstance(body, dict) or body.get("grant_type") != REFRESH_GRANT:
        return token_error(
            "unsupported_grant_type", "This server only supports signing in with a password."
        )
    if not isinstance(body.get("refresh_token"), str):
        return token_error("invalid_request", "A refresh token is required.")
    try:
        user, issued = await services.tokens.refresh(body["refresh_token"])
    except InvalidToken:
        return token_error("invalid_grant", "Please sign in again.")
    return JSONResponse(token_body(user, issued))


@router.post("/user/register_pocket_casts")
async def register() -> JSONResponse:
    return login_error(
        400, "Accounts are created by the server administrator.", "login_unable_to_create_account"
    )


@router.post("/user/login_google")
async def login_google() -> JSONResponse:
    return login_error(
        400, "Sign in with your Pod Pocket username and password.", "login_google_unsupported"
    )


@router.get("/subscription/status")
async def subscription_status(user: CurrentUser) -> dict[str, Any]:
    return {
        "autoRenewing": True,
        "expiryDate": FAR_FUTURE,
        "giftDays": 0,
        "paid": 1,
        "platform": 3,
        "frequency": 2,
        "type": 1,
        "tier": "patron",
        "index": 0,
        "subscriptions": None,
        "createdAt": iso_z(user.created_at),
        "features": None,
        "installmentBased": False,
    }
