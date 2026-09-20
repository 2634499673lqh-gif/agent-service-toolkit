"""Central server-side authorization boundary (T025).

This module is the single place that answers "may this valid principal perform
this operation?".  Authentication - is there a currently valid principal at
all? - is T024's job.  The guards here consume the server-derived
``CurrentPrincipal``; they never re-derive identity from caller input.

Frozen V1 semantics (ADR-004 decisions 3, 4, 14, 15):

* a valid in-tenant principal lacking the required role gets **403**;
* a resource outside the principal's organization gets **404**, which hides
  whether that resource exists at all;
* the principal's own ``organization_id`` is the only authorization tenant
  context - request bodies, query strings, headers, ``agent_config`` and AG-UI
  forwarded identity are never authorization truth.

Tenant-owned resource lookups must therefore carry the tenant predicate in the
query itself (``WHERE id = :id AND organization_id = :principal_organization_id``)
so a foreign row never reaches an authorization decision.  The guards below add
the defensive, service-side confirmation of that rule.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, status

from persistence.models import Role
from service.auth_dependency import require_principal
from service.session import CurrentPrincipal

AUTHENTICATION_FAILED_DETAIL = "Not authenticated"
AUTHORIZATION_FORBIDDEN_DETAIL = "Forbidden"
RESOURCE_NOT_FOUND_DETAIL = "Not Found"

WWW_AUTHENTICATE_HEADER = {"WWW-Authenticate": "Bearer"}

# ADR-004 decision 2: "L2 approval decisions require owner or admin and an
# active membership in the task organization; L3 is blocked in V1."
APPROVAL_DECISION_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.ADMIN})

# TaskPilot V1 permits admins to manage any task in their active organization.
# Members have self-service rights only for tasks they created. Owners do not
# inherit admin task permissions; there is no implicit role hierarchy.
TASK_MANAGEMENT_ROLES: frozenset[Role] = frozenset({Role.ADMIN})


class AuthorizationError(Exception):
    """Result of a failed policy decision.

    Carries only a status code and a fixed, non-disclosing message so an
    authorization failure can never leak resource existence or membership
    details.  Service-layer callers may catch it; the FastAPI guard wrappers
    convert it to an ``HTTPException``.
    """

    def __init__(self, status_code: int) -> None:
        if status_code == status.HTTP_404_NOT_FOUND:
            detail = RESOURCE_NOT_FOUND_DETAIL
        elif status_code == status.HTTP_403_FORBIDDEN:
            detail = AUTHORIZATION_FORBIDDEN_DETAIL
        else:  # pragma: no cover - guards only construct 403/404
            raise ValueError("AuthorizationError only supports 403 and 404")
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def require_authenticated(principal: CurrentPrincipal | None) -> CurrentPrincipal:
    """Return the principal, or fail closed when authentication did not happen.

    Missing authentication is a 401, never a 403: a request without a valid
    principal must not be turned into an authorization decision.
    """

    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTHENTICATION_FAILED_DETAIL,
            headers=dict(WWW_AUTHENTICATE_HEADER),
        )
    return principal


def require_active_membership(principal: CurrentPrincipal) -> CurrentPrincipal:
    """Confirm the principal's membership is active.

    T024 constructs a principal only after re-reading an active user, active
    membership and active organization, so an existing principal is already
    proof of an active membership.  The guard is kept as the explicit policy
    seam named by the frozen helper contract; it performs no second database
    read and therefore cannot invent a new authentication path.
    """

    return require_authenticated(principal)


def require_role(
    principal: CurrentPrincipal,
    allowed_roles: Collection[Role],
) -> CurrentPrincipal:
    """Return the principal when its current role is allowed, else 403.

    ``allowed_roles`` is matched against ``principal.role``, which T024 reads
    from the current Membership row on every request.  A caller-supplied role is
    never consulted, and no role ordering is assumed: each operation names the
    roles it actually allows.
    """

    principal = require_authenticated(principal)
    if principal.role not in frozenset(allowed_roles):
        raise AuthorizationError(status.HTTP_403_FORBIDDEN)
    return principal


def require_resource_tenant(
    principal: CurrentPrincipal,
    resource_organization_id: UUID | None,
) -> UUID:
    """Return the resource tenant when it matches the principal, else 404.

    Cross-tenant and nonexistent resources answer identically so a caller
    cannot enumerate another organization's rows.  The primary enforcement is
    the tenant predicate in the repository query; this guard is the service-side
    confirmation that a foreign row never reaches an authorization decision.
    """

    principal = require_authenticated(principal)
    if resource_organization_id is None or resource_organization_id != principal.organization_id:
        raise AuthorizationError(status.HTTP_404_NOT_FOUND)
    return resource_organization_id


def require_task_management(
    principal: CurrentPrincipal, created_by_user_id: UUID
) -> CurrentPrincipal:
    """Allow an admin or a member managing their own Task."""

    principal = require_authenticated(principal)
    if principal.role in TASK_MANAGEMENT_ROLES or (
        principal.role is Role.MEMBER and principal.user_id == created_by_user_id
    ):
        return principal
    raise AuthorizationError(status.HTTP_403_FORBIDDEN)


async def require_authenticated_principal(
    principal: Annotated[CurrentPrincipal, Depends(require_principal)],
) -> CurrentPrincipal:
    """FastAPI guard: authentication is mandatory for this operation."""

    return require_authenticated(principal)


def require_role_dependency(allowed_roles: Collection[Role]) -> Any:
    """Build a FastAPI guard requiring one of ``allowed_roles``.

    Usage: ``Depends(require_role_dependency([Role.ADMIN]))``.
    """

    async def guard(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
    ) -> CurrentPrincipal:
        try:
            return require_role(principal, allowed_roles)
        except AuthorizationError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None

    return guard


def require_resource_tenant_dependency(resource_organization_id: UUID | None) -> Any:
    """Build a FastAPI guard requiring the resource to be in the caller's tenant."""

    async def guard(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
    ) -> UUID:
        try:
            return require_resource_tenant(principal, resource_organization_id)
        except AuthorizationError as error:
            raise HTTPException(status_code=error.status_code, detail=error.detail) from None

    return guard


__all__ = [
    "AUTHENTICATION_FAILED_DETAIL",
    "AUTHORIZATION_FORBIDDEN_DETAIL",
    "APPROVAL_DECISION_ROLES",
    "TASK_MANAGEMENT_ROLES",
    "RESOURCE_NOT_FOUND_DETAIL",
    "AuthorizationError",
    "require_active_membership",
    "require_authenticated",
    "require_authenticated_principal",
    "require_resource_tenant",
    "require_resource_tenant_dependency",
    "require_task_management",
    "require_role",
    "require_role_dependency",
]
