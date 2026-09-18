"""T025 live-PostgreSQL integration tests for the authorization boundary.

The protected routes below run the full frozen chain against a disposable
PostgreSQL database:

    request -> require_principal -> CurrentPrincipal
            -> OrganizationRepository.get_in_principal_tenant(
                   resource_organization_id, principal.organization_id)
            -> tenant-scoped SQL result
            -> role authorization

Only the tenant-scoped lookup decides visibility; the role check runs after it,
so a foreign or missing resource answers the same 404 no matter which role asked.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException
from psycopg import sql
from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

try:
    from alembic import command
    from alembic.config import Config
except ModuleNotFoundError:  # pragma: no cover - host environment guard
    pytest.skip("HOST_ENVIRONMENT: Alembic dependency is not installed", allow_module_level=True)

from persistence.engine import create_async_engine, create_session_factory
from persistence.models import Membership, Organization, Role, User
from persistence.passwords import hash_password
from persistence.repositories import OrganizationRepository
from service import auth_dependency
from service.auth_dependency import require_principal
from service.authorization import (
    AUTHORIZATION_FORBIDDEN_DETAIL,
    RESOURCE_NOT_FOUND_DETAIL,
    AuthorizationError,
    require_role,
)
from service.session import AuthService, CurrentPrincipal

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery staple"


def _configured_test_url() -> str:
    database_url = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("HOST_ENVIRONMENT: TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return database_url


async def _create_database(base_url: str) -> str:
    base = make_url(base_url)
    if "test" not in (base.database or "").casefold():
        raise RuntimeError("Refusing database lifecycle operation outside a test database")
    name = f"taskpilot_t025_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    async with await psycopg.AsyncConnection.connect(admin_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return base.set(database=name).render_as_string(hide_password=False)


async def _drop_database(base_url: str, database_url: str) -> None:
    name = make_url(database_url).database or ""
    admin_url = (
        make_url(base_url)
        .set(database="postgres", drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    async with await psycopg.AsyncConnection.connect(admin_url, autocommit=True) as connection:
        await connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )


async def _migrate(database_url: str) -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "alembic.ini")
    config.attributes["database_url"] = database_url
    await asyncio.to_thread(command.upgrade, config, "head")


@dataclass
class AuthorizationEnvironment:
    """Live handles for driving the tenant-scoped authorization chain."""

    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    owner_token: str
    admin_token: str
    member_token: str
    owner_user_id: UUID
    organization_id: UUID
    foreign_organization_id: UUID
    foreign_owner_token: str
    foreign_owner_user_id: UUID
    scoped_queries: list[tuple[UUID, UUID]] = field(default_factory=list)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://taskpilot.test"
        )

    @staticmethod
    def headers(token: str, **extra: str) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {token}"}
        headers.update(extra)
        return headers


def _build_app(
    session_factory: async_sessionmaker[AsyncSession],
    scoped_queries: list[tuple[UUID, UUID]],
) -> FastAPI:
    """Build the test app; only the tenant lookup can decide visibility."""

    app = FastAPI()

    async def get_organization_repository() -> AsyncIterator[OrganizationRepository]:
        async with session_factory() as session:
            yield OrganizationRepository(session)

    @app.get("/admin-only")
    async def admin_only(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
    ) -> dict[str, str]:
        _apply_guard(require_role, principal, [Role.ADMIN])
        return {"role": principal.role.value}

    @app.get("/owner-only")
    async def owner_only(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
    ) -> dict[str, str]:
        _apply_guard(require_role, principal, [Role.OWNER])
        return {"role": principal.role.value}

    @app.get("/tenant/{resource_organization_id}")
    async def tenant_resource(
        resource_organization_id: UUID,
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        repository: Annotated[OrganizationRepository, Depends(get_organization_repository)],
        organization_id: UUID | None = None,
        role: str | None = None,
    ) -> dict[str, str]:
        # Caller-supplied organisation_id/role exist only to prove they are
        # ignored: the tenant scope is the principal's, never the caller's.
        del organization_id, role
        scoped_queries.append((resource_organization_id, principal.organization_id))
        # Step 1: tenant-scoped existence. The predicate is in the SQL, so a
        # foreign row is simply not found and never reaches a decision.
        tenant_organization = await repository.get_in_principal_tenant(
            resource_organization_id, principal.organization_id
        )
        if tenant_organization is None:
            raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
        return {"organization_id": str(tenant_organization.id)}

    @app.get("/tenant-admin/{resource_organization_id}")
    async def tenant_admin_resource(
        resource_organization_id: UUID,
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        repository: Annotated[OrganizationRepository, Depends(get_organization_repository)],
    ) -> dict[str, str]:
        scoped_queries.append((resource_organization_id, principal.organization_id))
        # Step 1 again: existence inside the caller's tenant decides visibility,
        # so a foreign resource is 404 before any role comparison happens.
        tenant_organization = await repository.get_in_principal_tenant(
            resource_organization_id, principal.organization_id
        )
        if tenant_organization is None:
            raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
        # Step 2: only a same-tenant resource can be refused for role reasons.
        _apply_guard(require_role, principal, [Role.ADMIN])
        return {"organization_id": str(tenant_organization.id)}

    app.dependency_overrides[auth_dependency.get_session_factory] = lambda: session_factory
    return app


def _apply_guard(guard: Any, *args: Any) -> Any:
    """Apply a pure authorization guard, mapping its result onto HTTP status."""

    try:
        return guard(*args)
    except AuthorizationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    organization = Organization(name="Authz Org")
    foreign_organization = Organization(name="Foreign Org")
    owner = User(email="authz-owner@example.com", password_hash=hash_password(PASSWORD))
    admin = User(email="authz-admin@example.com", password_hash=hash_password(PASSWORD))
    member = User(email="authz-member@example.com", password_hash=hash_password(PASSWORD))
    foreign_owner = User(email="authz-foreign@example.com", password_hash=hash_password(PASSWORD))
    for entity in (organization, foreign_organization, owner, admin, member, foreign_owner):
        entity.id = uuid4()

    owner_membership = Membership(
        user_id=owner.id, organization_id=organization.id, role=Role.OWNER, is_active=True
    )
    admin_membership = Membership(
        user_id=admin.id, organization_id=organization.id, role=Role.ADMIN, is_active=True
    )
    member_membership = Membership(
        user_id=member.id, organization_id=organization.id, role=Role.MEMBER, is_active=True
    )
    foreign_membership = Membership(
        user_id=foreign_owner.id,
        organization_id=foreign_organization.id,
        role=Role.OWNER,
        is_active=True,
    )

    async with factory() as session:
        async with session.begin():
            for entity in (organization, foreign_organization, owner, admin, member, foreign_owner):
                session.add(entity)
            await session.flush()
            for membership in (
                owner_membership,
                admin_membership,
                member_membership,
                foreign_membership,
            ):
                session.add(membership)

    async def issue(email: str) -> str:
        async with factory() as session:
            async with session.begin():
                result = await AuthService(session).login(email, PASSWORD)
            assert hasattr(result, "token"), result
            return result.token  # type: ignore[union-attr]

    return {
        "owner_token": await issue("authz-owner@example.com"),
        "admin_token": await issue("authz-admin@example.com"),
        "member_token": await issue("authz-member@example.com"),
        "foreign_owner_token": await issue("authz-foreign@example.com"),
        "owner_user_id": owner.id,
        "foreign_owner_user_id": foreign_owner.id,
        "organization_id": organization.id,
        "foreign_organization_id": foreign_organization.id,
    }


@pytest_asyncio.fixture
async def authorization_environment() -> AsyncIterator[AuthorizationEnvironment]:
    base_url = _configured_test_url()
    database_url = await _create_database(base_url)
    engine: AsyncEngine | None = None
    try:
        await _migrate(database_url)
        engine = create_async_engine(database_url)
        session_factory = create_session_factory(engine)
        seeded = await _seed(session_factory)
        scoped_queries: list[tuple[UUID, UUID]] = []
        yield AuthorizationEnvironment(
            app=_build_app(session_factory, scoped_queries),
            session_factory=session_factory,
            owner_token=seeded["owner_token"],
            admin_token=seeded["admin_token"],
            member_token=seeded["member_token"],
            owner_user_id=seeded["owner_user_id"],
            organization_id=seeded["organization_id"],
            foreign_organization_id=seeded["foreign_organization_id"],
            foreign_owner_token=seeded["foreign_owner_token"],
            foreign_owner_user_id=seeded["foreign_owner_user_id"],
            scoped_queries=scoped_queries,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(base_url, database_url)


# --------------------------------------------------------------------------- #
# A. Same-tenant resource + permitted role
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_tenant_resource_is_visible_to_a_permitted_role(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant-admin/{environment.organization_id}",
            headers=environment.headers(environment.admin_token),
        )

    assert response.status_code == 200
    assert response.json()["organization_id"] == str(environment.organization_id)
    assert environment.scoped_queries[-1] == (
        environment.organization_id,
        environment.organization_id,
    )


# --------------------------------------------------------------------------- #
# B. Same-tenant resource + insufficient role -> 403
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_same_tenant_resource_with_insufficient_role_returns_403(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant-admin/{environment.organization_id}",
            headers=environment.headers(environment.member_token),
        )

    assert response.status_code == 403
    assert response.json()["detail"] == AUTHORIZATION_FORBIDDEN_DETAIL
    # The scoped lookup ran first and found the row before the role was checked.
    assert environment.scoped_queries[-1] == (
        environment.organization_id,
        environment.organization_id,
    )


# --------------------------------------------------------------------------- #
# C/D/E. Foreign existing vs nonexistent -> identical 404
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_foreign_existing_resource_is_404(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant/{environment.foreign_organization_id}",
            headers=environment.headers(environment.owner_token),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == RESOURCE_NOT_FOUND_DETAIL
    # The foreign row exists in the table; the scoped query is what hides it.
    async with environment.session_factory() as session:
        stored = await session.get(Organization, environment.foreign_organization_id)
    assert stored is not None
    assert str(environment.foreign_organization_id) not in response.text


@pytest.mark.asyncio
async def test_nonexistent_resource_is_404(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant/{uuid4()}", headers=environment.headers(environment.owner_token)
        )

    assert response.status_code == 404
    assert response.json()["detail"] == RESOURCE_NOT_FOUND_DETAIL


@pytest.mark.asyncio
async def test_foreign_and_nonexistent_resources_are_indistinguishable(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        foreign = await client.get(
            f"/tenant/{environment.foreign_organization_id}",
            headers=environment.headers(environment.member_token),
        )
        missing = await client.get(
            f"/tenant/{uuid4()}", headers=environment.headers(environment.member_token)
        )

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert foreign.headers.get("content-type") == missing.headers.get("content-type")


# --------------------------------------------------------------------------- #
# F. Foreign resource + insufficient role -> still 404 (lookup before role)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_role_gated_route_is_404_for_a_foreign_tenant_request(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    """An admin in the caller's own tenant cannot address another tenant's row.

    The scoped lookup runs first and returns nothing, so the answer is 404 even
    though the role itself would have been sufficient.
    """

    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant-admin/{environment.foreign_organization_id}",
            headers=environment.headers(environment.admin_token),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == RESOURCE_NOT_FOUND_DETAIL


@pytest.mark.asyncio
async def test_foreign_owner_cannot_reach_the_tenant_scoped_route(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    """Another tenant's owner is powerful there, but holds no authority here.

    Their principal organization is the foreign tenant, so the scoped lookup
    for the caller's organization returns nothing and the answer is 404 rather
    than a role success.
    """

    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant-admin/{environment.organization_id}",
            headers=environment.headers(environment.foreign_owner_token),
        )

    assert response.status_code == 404
    assert response.json()["detail"] == RESOURCE_NOT_FOUND_DETAIL
    # The lookup was scoped to the foreign owner's own tenant, not to the
    # organization named in the path.
    assert environment.scoped_queries[-1] == (
        environment.organization_id,
        environment.foreign_organization_id,
    )


# --------------------------------------------------------------------------- #
# G. Caller injection cannot change the repository scope
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_caller_supplied_identity_cannot_change_the_lookup_scope(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        response = await client.get(
            f"/tenant/{environment.foreign_organization_id}",
            headers=environment.headers(
                environment.owner_token,
                **{
                    "X-Organization-Id": str(environment.foreign_organization_id),
                    "X-Role": "owner",
                },
            ),
            params={
                "organization_id": str(environment.foreign_organization_id),
                "role": "owner",
            },
        )

    assert response.status_code == 404
    # The scope passed to the repository is the principal's, regardless of the
    # caller's query and header values.
    assert environment.scoped_queries[-1] == (
        environment.foreign_organization_id,
        environment.organization_id,
    )


@pytest.mark.asyncio
async def test_lookup_scope_always_comes_from_the_principal(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        await client.get(
            f"/tenant/{environment.organization_id}",
            headers=environment.headers(environment.owner_token),
        )

    resource_id, scope_id = environment.scoped_queries[-1]
    assert resource_id == environment.organization_id
    assert scope_id == environment.organization_id


# --------------------------------------------------------------------------- #
# HTTP flow really exercises the tenant-scoped repository query
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_http_flow_executes_a_tenant_scoped_database_query(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment
    statements: list[str] = []
    # Listen on the very engine the request session uses, so the captured
    # statements are exactly what the HTTP flow executed against PostgreSQL.
    engine: AsyncEngine = environment.session_factory.kw["bind"]

    def record(
        conn: Any,  # noqa: ARG001
        cursor: Any,  # noqa: ARG001
        statement: str,
        parameters: Any,  # noqa: ARG001
        context: Any,  # noqa: ARG001
        executemany: bool,  # noqa: ARG001
    ) -> None:
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        async with environment.client() as client:
            same_tenant = await client.get(
                f"/tenant/{environment.organization_id}",
                headers=environment.headers(environment.owner_token),
            )
            foreign = await client.get(
                f"/tenant/{environment.foreign_organization_id}",
                headers=environment.headers(environment.owner_token),
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert same_tenant.status_code == 200
    assert foreign.status_code == 404
    # Only the tenant-scoped lookup carries both predicates; the identity reads
    # use single-key lookups and are filtered out here.
    scoped_statements = [
        statement
        for statement in statements
        if statement.casefold().count("taskpilot.organizations.id =") >= 2
    ]
    assert scoped_statements, "the HTTP flow must run the tenant-scoped lookup"
    for statement in scoped_statements:
        normalized = statement.casefold()
        assert "from taskpilot.organizations" in normalized
        assert "where" in normalized


# --------------------------------------------------------------------------- #
# Authentication stays T024's concern
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_authentication_failure_is_401_before_any_authorization_decision(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        missing = await client.get("/owner-only")
        unknown = await client.get("/owner-only", headers={"Authorization": "Bearer unknown-token"})
        no_principal = await client.get(f"/tenant-admin/{environment.organization_id}")

    assert missing.status_code == 401
    assert unknown.status_code == 401
    assert no_principal.status_code == 401
    assert all(
        response.json()["detail"] == "Not authenticated"
        for response in (missing, unknown, no_principal)
    )
    # No principal means no tenant lookup happened.
    assert environment.scoped_queries == []


@pytest.mark.asyncio
async def test_authorization_failures_never_leak_identity_or_credentials(
    authorization_environment: AuthorizationEnvironment,
) -> None:
    environment = authorization_environment

    async with environment.client() as client:
        forbidden = await client.get(
            "/admin-only", headers=environment.headers(environment.member_token)
        )
        hidden = await client.get(
            f"/tenant/{environment.foreign_organization_id}",
            headers=environment.headers(environment.owner_token),
        )

    for response, token in (
        (forbidden, environment.member_token),
        (hidden, environment.owner_token),
    ):
        body = response.text
        assert token not in body
        assert str(environment.organization_id) not in body
        assert str(environment.foreign_organization_id) not in body
        assert str(environment.owner_user_id) not in body
