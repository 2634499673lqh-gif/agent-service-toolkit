"""FastAPI request authentication dependency for TaskPilot identity.

The dependency answers exactly one question: does this request carry a currently
valid, server-derived principal?  It resolves the opaque bearer credential to a
fresh principal by re-reading the database on every request, closes the request
scoped session, and raises one generic 401 for every authentication failure.
It makes no authorization decision (that is T025) and never treats the legacy
``AUTH_SECRET`` compatibility bearer as a TaskPilot identity.
"""

import logging
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine, create_session_factory
from service.session import AuthService, CurrentPrincipal

logger = logging.getLogger(__name__)

AUTHENTICATION_ERROR_DETAIL = "Not authenticated"
WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}

BEARER_SCHEME = "bearer"


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide TaskPilot session factory.

    Cached so the business engine is created once.  Raises if
    ``TASKPILOT_DATABASE_URL`` is unconfigured, which only happens when a
    TaskPilot identity endpoint is used without business persistence.
    """

    return create_session_factory(create_async_engine())


def get_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncSession:
    """Return one request-scoped session from the injected factory.

    Declared as a dependency so applications and tests can override the factory
    without monkeypatching module globals.
    """

    return session_factory()


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Return the opaque bearer token, or ``None`` for any malformed header."""

    if not authorization:
        return None
    scheme, separator, credentials = authorization.partition(" ")
    if not separator or scheme.casefold() != BEARER_SCHEME or not credentials or " " in credentials:
        return None
    return credentials


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=AUTHENTICATION_ERROR_DETAIL,
        headers={**WWW_AUTHENTICATE_HEADER, "Cache-Control": "no-store"},
    )


async def _cleanup_session(session: AsyncSession) -> None:
    """Roll back an unexpected failure, then close the session exactly once.

    Used only where rollback is required; ``_close_session`` covers the
    expected rejections that never wrote.
    A rollback failure must not prevent the close, nor overwrite the original
    error, so it is logged without the exception text (driver errors can embed
    SQL text and bind parameters).
    """

    try:
        await session.rollback()
    except Exception:  # noqa: BLE001 - cleanup must never mask or escape
        logger.warning("Session rollback failed during authentication failure cleanup")
    await _close_session(session)


async def _close_session(session: AsyncSession) -> None:
    """Close one request-scoped session exactly once."""

    await session.close()


async def require_principal(
    session: Annotated[AsyncSession, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
) -> CurrentPrincipal:
    """Resolve the request's server-derived principal or raise a generic 401.

    The session is acquired and closed per request and rolled back if anything
    raises, so a failed authentication leaves no transaction open.
    """

    # The cleanup boundary starts here, immediately after the request-scoped
    # session exists, so every path below - malformed credential, unknown or
    # revoked session, inactive state, unexpected database error, or success -
    # closes the session exactly once.
    unexpected_error = False
    try:
        token = _extract_bearer_token(authorization)
        if token is None:
            # Expected rejection: raise inside the boundary so the session is
            # still closed on the way out.
            raise _authentication_error()
        principal = await AuthService(session).authenticate(token)
        if principal is None:
            raise _authentication_error()
        return principal
    except HTTPException:
        # Expected authentication rejection: nothing was written, so it only
        # needs the session returned to the pool.
        raise
    except Exception:
        # Unexpected failure inside resolution: roll back, then close.
        unexpected_error = True
        raise
    finally:
        if unexpected_error:
            await _cleanup_session(session)
        else:
            await _close_session(session)


PrincipalDependency = Annotated[CurrentPrincipal, Depends(require_principal)]

__all__ = [
    "AUTHENTICATION_ERROR_DETAIL",
    "PrincipalDependency",
    "get_session_factory",
    "require_principal",
]
