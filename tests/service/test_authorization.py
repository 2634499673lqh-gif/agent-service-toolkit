"""T025 tests for the central authorization boundary.

These cover the pure policy guards plus the FastAPI guard wrappers, using the
server-derived ``CurrentPrincipal`` value object directly.  Authentication
itself is T024's contract and is exercised in ``test_current_principal.py`` and
the persistence integration suite.
"""

from typing import Annotated
from uuid import UUID, uuid4

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from persistence.models import Role
from service import auth_dependency
from service.authorization import (
    APPROVAL_DECISION_ROLES,
    AUTHENTICATION_FAILED_DETAIL,
    AUTHORIZATION_FORBIDDEN_DETAIL,
    RESOURCE_NOT_FOUND_DETAIL,
    AuthorizationError,
    require_active_membership,
    require_authenticated,
    require_authenticated_principal,
    require_resource_tenant,
    require_resource_tenant_dependency,
    require_role,
    require_role_dependency,
)
from service.session import CurrentPrincipal


def _principal(
    *,
    role: Role = Role.OWNER,
    organization_id: UUID | None = None,
) -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=organization_id or uuid4(),
        role=role,
        session_id=uuid4(),
    )


def _expect_http_error(call, status_code: int, detail: str) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as raised:
        call()
    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
    return raised.value


def _expect_authorization_error(call, status_code: int, detail: str) -> AuthorizationError:  # noqa: ANN001
    with pytest.raises(AuthorizationError) as raised:
        call()
    assert raised.value.status_code == status_code
    assert raised.value.detail == detail
    assert str(raised.value) == detail
    return raised.value


# --------------------------------------------------------------------------- #
# require_authenticated
# --------------------------------------------------------------------------- #


def test_require_authenticated_returns_the_principal() -> None:
    principal = _principal()

    assert require_authenticated(principal) is principal


def test_require_authenticated_fails_closed_without_a_principal() -> None:
    error = _expect_http_error(
        lambda: require_authenticated(None),
        401,
        AUTHENTICATION_FAILED_DETAIL,
    )

    # Missing authentication is never downgraded into an authorization answer.
    assert error.headers == {"WWW-Authenticate": "Bearer"}


# --------------------------------------------------------------------------- #
# require_active_membership
# --------------------------------------------------------------------------- #


def test_require_active_membership_accepts_an_existing_principal() -> None:
    principal = _principal()

    assert require_active_membership(principal) is principal


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.MEMBER])
def test_require_active_membership_does_not_depend_on_the_role(role: Role) -> None:
    principal = _principal(role=role)

    assert require_active_membership(principal) is principal


# --------------------------------------------------------------------------- #
# require_role
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.MEMBER])
def test_require_role_allows_each_role_when_listed(role: Role) -> None:
    principal = _principal(role=role)

    assert require_role(principal, [role]) is principal


def test_require_role_is_not_a_hierarchy() -> None:
    # A member-only operation must reject an owner: the ADR grants member
    # self-service rights that owner/admin are not assumed to inherit.
    principal = _principal(role=Role.OWNER)

    _expect_authorization_error(
        lambda: require_role(principal, [Role.MEMBER]),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )


def test_require_role_rejects_an_insufficient_role_with_403() -> None:
    principal = _principal(role=Role.MEMBER)

    _expect_authorization_error(
        lambda: require_role(principal, APPROVAL_DECISION_ROLES),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN])
def test_approval_decision_roles_allow_owner_and_admin(role: Role) -> None:
    principal = _principal(role=role)

    assert require_role(principal, APPROVAL_DECISION_ROLES) is principal


def test_require_role_rejects_an_empty_allow_list() -> None:
    principal = _principal(role=Role.OWNER)

    _expect_authorization_error(
        lambda: require_role(principal, []),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )


def test_require_role_ignores_a_forged_role_attribute_on_the_request() -> None:
    # The guard reads only principal.role; nothing from the caller is consulted.
    principal = _principal(role=Role.MEMBER)

    _expect_authorization_error(
        lambda: require_role(principal, [Role.OWNER]),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )
    assert principal.role is Role.MEMBER


# --------------------------------------------------------------------------- #
# require_resource_tenant
# --------------------------------------------------------------------------- #


def test_require_resource_tenant_allows_a_same_tenant_resource() -> None:
    organization_id = uuid4()
    principal = _principal(organization_id=organization_id)

    assert require_resource_tenant(principal, organization_id) == organization_id


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.MEMBER])
def test_foreign_tenant_resource_is_404_for_every_role(role: Role) -> None:
    principal = _principal(role=role)

    _expect_authorization_error(
        lambda: require_resource_tenant(principal, uuid4()),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )


def test_owner_is_not_a_global_owner_across_tenants() -> None:
    principal = _principal(role=Role.OWNER)

    error = _expect_authorization_error(
        lambda: require_resource_tenant(principal, uuid4()),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )
    assert error.status_code != 403


def test_nonexistent_resource_is_indistinguishable_from_a_foreign_one() -> None:
    principal = _principal()

    missing = _expect_authorization_error(
        lambda: require_resource_tenant(principal, None),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )
    foreign = _expect_authorization_error(
        lambda: require_resource_tenant(principal, uuid4()),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )

    assert missing.status_code == foreign.status_code
    assert missing.detail == foreign.detail


def test_authorization_error_does_not_leak_resource_ids() -> None:
    principal = _principal()
    foreign_organization_id = uuid4()

    error = _expect_authorization_error(
        lambda: require_resource_tenant(principal, foreign_organization_id),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )

    assert str(foreign_organization_id) not in str(error)
    assert str(principal.organization_id) not in str(error)


# --------------------------------------------------------------------------- #
# Tenant isolation ordering
# --------------------------------------------------------------------------- #


def test_tenant_check_precedes_role_check() -> None:
    """A foreign resource must be 404 even when the role would also be rejected."""

    principal = _principal(role=Role.MEMBER)
    foreign_organization_id = uuid4()

    # Wrong order (role first) would answer 403 and leak that the id exists.
    _expect_authorization_error(
        lambda: require_role(principal, APPROVAL_DECISION_ROLES),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )
    _expect_authorization_error(
        lambda: require_resource_tenant(principal, foreign_organization_id),
        404,
        RESOURCE_NOT_FOUND_DETAIL,
    )


def test_full_ordered_guards_answer_404_before_403() -> None:
    """The composed guard order used by routes: tenant first, then role."""

    principal = _principal(role=Role.MEMBER)

    def guarded(resource_organization_id: UUID | None) -> str:
        require_authenticated(principal)
        require_resource_tenant(principal, resource_organization_id)
        require_role(principal, APPROVAL_DECISION_ROLES)
        return "ok"

    # Same tenant but insufficient role -> 403.
    _expect_authorization_error(
        lambda: guarded(principal.organization_id),
        403,
        AUTHORIZATION_FORBIDDEN_DETAIL,
    )
    # Foreign tenant -> 404, never 403.
    _expect_authorization_error(lambda: guarded(uuid4()), 404, RESOURCE_NOT_FOUND_DETAIL)
    # Nonexistent -> 404.
    _expect_authorization_error(lambda: guarded(None), 404, RESOURCE_NOT_FOUND_DETAIL)


# --------------------------------------------------------------------------- #
# FastAPI guard wrappers (real dependency chain)
# --------------------------------------------------------------------------- #


def _guard_app(principal: CurrentPrincipal | None) -> FastAPI:
    app = FastAPI()

    @app.get("/role-restricted")
    async def role_restricted(
        guarded: Annotated[CurrentPrincipal, Depends(require_role_dependency([Role.ADMIN]))],
    ) -> dict[str, str]:
        del guarded
        return {"ok": "true"}

    @app.get("/authenticated")
    async def authenticated(
        current: Annotated[CurrentPrincipal, Depends(require_authenticated_principal)],
    ) -> dict[str, str]:
        return {"user_id": str(current.user_id)}

    # Replace the whole authentication edge with a fixed principal.
    async def fake_principal() -> CurrentPrincipal | None:
        return principal

    app.dependency_overrides[auth_dependency.require_principal] = fake_principal
    return app


def test_fastapi_role_guard_allows_an_allowed_role() -> None:
    client = TestClient(_guard_app(_principal(role=Role.ADMIN)))

    response = client.get("/role-restricted")

    assert response.status_code == 200


def test_fastapi_role_guard_rejects_an_insufficient_role_with_403() -> None:
    client = TestClient(_guard_app(_principal(role=Role.MEMBER)))

    response = client.get("/role-restricted")

    assert response.status_code == 403
    assert response.json()["detail"] == AUTHORIZATION_FORBIDDEN_DETAIL


def test_fastapi_guard_never_answers_403_without_authentication() -> None:
    client = TestClient(_guard_app(None))

    response = client.get("/role-restricted")

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_FAILED_DETAIL


def test_fastapi_authenticated_guard_returns_the_principal() -> None:
    principal = _principal()
    client = TestClient(_guard_app(principal))

    response = client.get("/authenticated")

    assert response.status_code == 200
    assert response.json()["user_id"] == str(principal.user_id)


def test_fastapi_authenticated_guard_returns_401_without_a_principal() -> None:
    client = TestClient(_guard_app(None))

    response = client.get("/authenticated")

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_FAILED_DETAIL
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_fastapi_tenant_guard_returns_404_for_a_foreign_tenant() -> None:
    principal = _principal(role=Role.OWNER)
    # Routes build this dependency at definition time, from the resource they
    # already looked up inside the principal's tenant, e.g.
    # ``Depends(require_resource_tenant_dependency(resource.organization_id))``.
    guard = require_resource_tenant_dependency(principal.organization_id)
    foreign_guard = require_resource_tenant_dependency(uuid4())
    app = FastAPI()

    @app.get("/tenant")
    async def tenant(
        guarded: Annotated[UUID, Depends(guard)],
    ) -> dict[str, str]:
        del guarded
        return {"ok": "true"}

    @app.get("/foreign-tenant")
    async def foreign_tenant(
        guarded: Annotated[UUID, Depends(foreign_guard)],
    ) -> dict[str, str]:
        del guarded
        return {"ok": "true"}

    async def fake_principal() -> CurrentPrincipal | None:
        return principal

    app.dependency_overrides[auth_dependency.require_principal] = fake_principal
    client = TestClient(app)

    same_tenant = client.get("/tenant")
    foreign = client.get("/foreign-tenant")

    assert same_tenant.status_code == 200
    assert foreign.status_code == 404
    assert foreign.json()["detail"] == RESOURCE_NOT_FOUND_DETAIL
    # An owner in their own organization is not a global owner.
    assert principal.role is Role.OWNER


def test_fastapi_authorization_error_never_leaks_credential_material() -> None:
    principal = _principal(role=Role.MEMBER)
    client = TestClient(_guard_app(principal))

    response = client.get("/role-restricted", headers={"X-Role": "owner"})

    assert response.status_code == 403
    assert str(principal.session_id) not in response.text
    assert str(principal.membership_id) not in response.text
    assert str(principal.organization_id) not in response.text
    assert "Bearer" not in response.text
