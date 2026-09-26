"""Minimal AuthService-backed HTTP authentication surface (T118)."""

import logging
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schema.auth_api import (
    LoginRequest,
    LoginResponse,
    OrganizationSelectionResponse,
    SessionResponse,
)
from service.auth_dependency import (
    _authentication_error,
    _extract_bearer_token,
    get_session_factory,
)
from service.session import (
    AuthService,
    CurrentPrincipal,
    LoginError,
    OrganizationSelectionRequired,
)

logger = logging.getLogger(__name__)

AUTH_SERVICE_UNAVAILABLE = {"detail": "Service unavailable"}
NO_STORE = "no-store"


async def get_auth_session(
    factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session


AuthSessionDependency = Annotated[AsyncSession, Depends(get_auth_session)]
auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _safe_service_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Service unavailable",
        headers={"Cache-Control": NO_STORE},
    )


async def require_auth_principal(
    session: AuthSessionDependency,
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentPrincipal:
    """Resolve auth-route identity with a fixed infrastructure failure response."""

    token = _extract_bearer_token(authorization)
    if token is None:
        raise _authentication_error()
    try:
        principal = await AuthService(session).authenticate(token)
    except Exception:
        logger.warning("TaskPilot authentication lookup unavailable")
        raise _safe_service_error() from None
    if principal is None:
        raise _authentication_error()
    return principal


AuthPrincipalDependency = Annotated[CurrentPrincipal, Depends(require_auth_principal)]


@auth_router.post("/login", response_model=LoginResponse | OrganizationSelectionResponse)
async def login(request: Request, session: AuthSessionDependency) -> Response:
    try:
        try:
            raw_payload = await request.json()
            if (
                not isinstance(raw_payload, dict)
                or not isinstance(raw_payload.get("email"), str)
                or not isinstance(raw_payload.get("password"), str)
                or (
                    "organization_id" in raw_payload
                    and raw_payload["organization_id"] is not None
                    and not isinstance(raw_payload["organization_id"], str)
                )
            ):
                raise ValueError("invalid credential shape")
            payload = LoginRequest.model_validate(raw_payload)
        except (ValidationError, ValueError, TypeError):
            raise _authentication_error() from None
        result = await AuthService(session).login_and_commit(
            payload.email,
            payload.password,
            organization_id=payload.organization_id,
        )
    except HTTPException:
        raise
    except LoginError:
        raise _authentication_error() from None
    except Exception:
        logger.warning("TaskPilot login failed due to unavailable persistence")
        raise _safe_service_error() from None

    if isinstance(result, OrganizationSelectionRequired):
        return Response(
            content=OrganizationSelectionResponse(
                code=result.code,
                organization_ids=list(result.organization_ids),
            ).model_dump_json(),
            status_code=status.HTTP_409_CONFLICT,
            media_type="application/json",
            headers={"Cache-Control": NO_STORE},
        )
    return Response(
        content=LoginResponse(
            access_token=result.token,
            expires_at=result.expires_at,
        ).model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": NO_STORE},
    )


@auth_router.get("/session", response_model=SessionResponse)
async def current_session(principal: AuthPrincipalDependency) -> Response:
    return Response(
        content=SessionResponse(
            user_id=principal.user_id,
            membership_id=principal.membership_id,
            organization_id=principal.organization_id,
            role=principal.role.value,
        ).model_dump_json(),
        media_type="application/json",
        headers={"Cache-Control": NO_STORE},
    )


@auth_router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    principal: AuthPrincipalDependency,
    session: AuthSessionDependency,
) -> Response:
    try:
        revoked = await AuthService(session).revoke_principal_and_commit(principal)
    except Exception:
        logger.warning("TaskPilot logout failed due to unavailable persistence")
        raise _safe_service_error() from None
    if not revoked:
        raise _authentication_error()
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": NO_STORE})


__all__ = ["auth_router", "get_auth_session"]
