"""T026 authoritative Phase 2 security matrix.

The T026 Task Card owns the negative/security test matrix.  This module runs the
rows that need the whole frozen chain - real FastAPI requests against a real
disposable PostgreSQL database - so that a status code can never stand in for
the policy that produced it:

* authentication: valid, missing, malformed, unknown, expired, revoked and
  inactive user/membership/organization credentials; every failure returns one
  generic 401, builds no ``CurrentPrincipal`` and never reaches a protected
  resource body;
* tenant: same-tenant allow, foreign and nonexistent lookups answering the same
  404, and forged user/organization/role inputs that cannot select the scope;
* tenant channels: the upstream ``/threads`` query identity and the AG-UI
  ``forwardedProps.configurable`` identity cannot select the TaskPilot scope;
* role/approval: the live owner/admin/member operation matrix, the denied role,
  and the unauthorized approval decision against the frozen policy;
* secrets: password, stored hash, raw token and token digest are absent from
  every TaskPilot response and from the structured logs.

Persistence rows live in ``tests/persistence/test_postgres_integration.py``
(fresh database, existing upgrade, revision metadata, LangGraph coexistence,
development/test one-revision downgrade, rollback on error, independent
sessions, cleanup) and ``tests/persistence/test_foundation.py`` (production
forward-only migration policy).  V1 has no approval records, so approval record
lookup and duplicate-decision idempotency stay deferred to the task that
introduces that domain.
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, HTTPException, Request
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, MessagesState, StateGraph
from psycopg import sql
from sqlalchemy import event, make_url, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

try:
    from alembic import command
    from alembic.config import Config
except ModuleNotFoundError:  # pragma: no cover - host environment guard
    pytest.skip("HOST_ENVIRONMENT: Alembic dependency is not installed", allow_module_level=True)

from agents import DEFAULT_AGENT
from core.settings import settings
from persistence.engine import create_async_engine, create_session_factory
from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.passwords import hash_password
from persistence.repositories import (
    AuthSessionRepository,
    OrganizationRepository,
    UserRepository,
)
from persistence.tokens import generate_token, hash_token
from service import auth_dependency
from service.auth_dependency import AUTHENTICATION_ERROR_DETAIL, require_principal
from service.authorization import (
    APPROVAL_DECISION_ROLES,
    AUTHORIZATION_FORBIDDEN_DETAIL,
    RESOURCE_NOT_FOUND_DETAIL,
    AuthorizationError,
    require_resource_tenant,
    require_role,
)
from service.logging import configure_logging
from service.service import router as upstream_router
from service.session import SESSION_TTL, AuthService, CurrentPrincipal
from service.threads import THREAD_HEAD_STEP

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery staple"
FORGED_ROLE = "owner"


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
    name = f"taskpilot_t026_{uuid4().hex}"
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
class MatrixEnvironment:
    """Live handles for exercising the whole security matrix."""

    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    owner_token: str
    admin_token: str
    member_token: str
    foreign_owner_token: str
    unknown_token: str
    inactive_user_token: str
    inactive_membership_token: str
    inactive_organization_token: str
    expired_token: str
    revoked_token: str
    owner_user_id: UUID
    admin_user_id: UUID
    member_user_id: UUID
    owner_membership_id: UUID
    owner_session_id: UUID
    organization_id: UUID
    foreign_organization_id: UUID
    inactive_organization_id: UUID
    foreign_owner_user_id: UUID
    stored_password_hash: str
    # Every protected route body appends here, so a test can prove that a
    # rejected request never executed one.
    protected_accesses: list[tuple[UUID, UUID, str]] = field(default_factory=list)
    # The (resource id, scope id) pairs actually handed to the tenant-scoped
    # repository, so the enforced SQL scope is asserted instead of assumed.
    scoped_queries: list[tuple[UUID, UUID]] = field(default_factory=list)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://taskpilot.test"
        )

    @staticmethod
    def headers(token: str | None, **extra: str) -> dict[str, str]:
        headers = dict(extra)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def token_for_role(self, role: str) -> str:
        return {
            "owner": self.owner_token,
            "admin": self.admin_token,
            "member": self.member_token,
        }[role]


class _RecordingCheckpointer:
    """Checkpointer double that records the filter the ``/threads`` route asked for."""

    def __init__(self) -> None:
        self.filters: list[dict[str, Any]] = []

    async def alist(
        self,
        config: Any,  # noqa: ARG002 - signature mirrors the checkpointer contract
        *,
        filter: dict[str, Any],  # noqa: A002 - keyword name is the upstream contract
        before: Any = None,  # noqa: ARG002
        limit: int = 100,  # noqa: ARG002
    ) -> AsyncIterator[Any]:
        self.filters.append(dict(filter))
        return
        yield  # pragma: no cover - async generator that yields no threads


_agui_configurable: dict[str, Any] = {}


async def _agui_record_config(state: MessagesState, config: RunnableConfig) -> dict[str, Any]:
    """Record what AG-UI handed to LangGraph, then answer once."""

    _agui_configurable.update(config["configurable"])
    model = FakeListChatModel(responses=["acknowledged"])
    response = await model.ainvoke(state["messages"])
    return {"messages": [response]}


def _build_upstream_agui_agent() -> Any:
    """Build the LangGraph agent the real AG-UI router runs in these tests."""

    graph = StateGraph(MessagesState)
    graph.add_node("model", _agui_record_config)
    graph.set_entry_point("model")
    graph.add_edge("model", END)
    return graph.compile(checkpointer=MemorySaver())


def _apply_guard(guard: Any, *args: Any) -> Any:
    """Apply a pure authorization guard, mapping its result onto HTTP status."""

    try:
        return guard(*args)
    except AuthorizationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None


async def _organization_repository(request: Request) -> AsyncIterator[OrganizationRepository]:
    """Request-scoped tenant repository, overridable so a buggy lookup can be injected."""

    async with request.app.state.session_factory() as session:
        yield OrganizationRepository(session)


def _build_app(
    session_factory: async_sessionmaker[AsyncSession],
    protected_accesses: list[tuple[UUID, UUID, str]],
    scoped_queries: list[tuple[UUID, UUID]],
) -> FastAPI:
    """Build the test app: TaskPilot protected routes plus the real upstream router."""

    app = FastAPI()
    app.state.session_factory = session_factory

    @app.get("/whoami")
    async def whoami(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        user_id: str | None = None,
        organization_id: str | None = None,
        role: str | None = None,
    ) -> dict[str, str]:
        # The caller-supplied identity inputs exist only to prove they are ignored.
        del user_id, organization_id, role
        protected_accesses.append(
            (principal.user_id, principal.organization_id, principal.role.value)
        )
        return {
            "user_id": str(principal.user_id),
            "membership_id": str(principal.membership_id),
            "organization_id": str(principal.organization_id),
            "role": principal.role.value,
            "session_id": str(principal.session_id),
        }

    def _role_route(allowed_roles: list[Role], result: dict[str, str]) -> Any:
        async def handler(
            principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        ) -> dict[str, str]:
            _apply_guard(require_role, principal, allowed_roles)
            protected_accesses.append(
                (principal.user_id, principal.organization_id, principal.role.value)
            )
            return dict(result)

        return handler

    app.get("/owner-only")(_role_route([Role.OWNER], {"role": Role.OWNER.value}))
    app.get("/admin-only")(_role_route([Role.ADMIN], {"role": Role.ADMIN.value}))
    app.get("/member-only")(_role_route([Role.MEMBER], {"role": Role.MEMBER.value}))
    # ADR-004 decision 2 freezes the L2 approval-decision policy as owner or
    # admin.  V1 has no approval records, so this route exercises the frozen
    # policy gate only; record lookup is not implemented.
    app.post("/approval-decision")(
        _role_route(list(APPROVAL_DECISION_ROLES), {"decision": "accepted"})
    )

    @app.get("/tenant/{resource_organization_id}")
    async def tenant_resource(
        resource_organization_id: UUID,
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        repository: Annotated[OrganizationRepository, Depends(_organization_repository)],
    ) -> dict[str, str]:
        scoped_queries.append((resource_organization_id, principal.organization_id))
        # Step 1: the tenant predicate lives in the SQL, so a foreign row is
        # simply not found and never reaches an authorization decision.
        found = await repository.get_in_principal_tenant(
            resource_organization_id, principal.organization_id
        )
        if found is None:
            raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
        protected_accesses.append(
            (principal.user_id, principal.organization_id, principal.role.value)
        )
        return {"organization_id": str(found.id)}

    @app.get("/tenant-admin/{resource_organization_id}")
    async def tenant_admin_resource(
        resource_organization_id: UUID,
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        repository: Annotated[OrganizationRepository, Depends(_organization_repository)],
    ) -> dict[str, str]:
        scoped_queries.append((resource_organization_id, principal.organization_id))
        found = await repository.get_in_principal_tenant(
            resource_organization_id, principal.organization_id
        )
        if found is None:
            raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
        # Step 2: only a same-tenant resource can be refused for role reasons.
        _apply_guard(require_role, principal, [Role.ADMIN])
        protected_accesses.append(
            (principal.user_id, principal.organization_id, principal.role.value)
        )
        return {"organization_id": str(found.id)}

    @app.get("/tenant-confirmed/{resource_organization_id}")
    async def tenant_confirmed_resource(
        resource_organization_id: UUID,
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        repository: Annotated[OrganizationRepository, Depends(_organization_repository)],
    ) -> dict[str, str]:
        scoped_queries.append((resource_organization_id, principal.organization_id))
        found = await repository.get_in_principal_tenant(
            resource_organization_id, principal.organization_id
        )
        if found is None:
            raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
        # The frozen helper contract applies the service-side confirmation to
        # whatever the repository returned, so a buggy lookup cannot turn a
        # foreign row into an allowed resource.  For an organization row the
        # tenant *is* the row identity.
        _apply_guard(require_resource_tenant, principal, found.id)
        protected_accesses.append(
            (principal.user_id, principal.organization_id, principal.role.value)
        )
        return {"organization_id": str(found.id)}

    # The real upstream router, unchanged: /threads asserts a caller-supplied
    # user_id and AG-UI forwards caller configurable values.  Both are identity
    # claims that must not become TaskPilot authorization truth.
    app.include_router(upstream_router)

    app.dependency_overrides[auth_dependency.get_session_factory] = lambda: session_factory
    return app


async def _issue(factory: async_sessionmaker[AsyncSession], email: str) -> tuple[str, UUID]:
    async with factory() as session:
        async with session.begin():
            result = await AuthService(session).login(email, PASSWORD)
        assert hasattr(result, "token"), result
        return result.token, result.session_id  # type: ignore[union-attr]


async def _mint(
    factory: async_sessionmaker[AsyncSession],
    *,
    user_id: UUID,
    membership_id: UUID,
    expires_in: timedelta = SESSION_TTL,
    revoked: bool = False,
) -> str:
    """Insert a session row directly, for states login must never issue."""

    token = generate_token()
    async with factory() as session:
        async with session.begin():
            await AuthSessionRepository(session).add(
                AuthSession(
                    user_id=user_id,
                    membership_id=membership_id,
                    token_hash=hash_token(token),
                    expires_at=datetime.now(UTC) + expires_in,
                    revoked_at=datetime.now(UTC) if revoked else None,
                )
            )
    return token


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Create every identity state the authentication matrix must reject."""

    organization = Organization(name="Matrix Org")
    foreign_organization = Organization(name="Matrix Foreign Org")
    inactive_organization = Organization(name="Matrix Inactive Org")
    inactive_organization.is_active = False
    owner = User(email="matrix-owner@example.com", password_hash=hash_password(PASSWORD))
    admin = User(email="matrix-admin@example.com", password_hash=hash_password(PASSWORD))
    member = User(email="matrix-member@example.com", password_hash=hash_password(PASSWORD))
    foreign_owner = User(email="matrix-foreign@example.com", password_hash=hash_password(PASSWORD))
    inactive_user = User(
        email="matrix-inactive-user@example.com",
        password_hash=hash_password(PASSWORD),
        is_active=False,
    )
    dormant_user = User(
        email="matrix-dormant-member@example.com", password_hash=hash_password(PASSWORD)
    )
    for entity in (
        organization,
        foreign_organization,
        inactive_organization,
        owner,
        admin,
        member,
        foreign_owner,
        inactive_user,
        dormant_user,
    ):
        entity.id = uuid4()

    owner_membership = Membership(
        user_id=owner.id, organization_id=organization.id, role=Role.OWNER
    )
    admin_membership = Membership(
        user_id=admin.id, organization_id=organization.id, role=Role.ADMIN
    )
    member_membership = Membership(
        user_id=member.id, organization_id=organization.id, role=Role.MEMBER
    )
    foreign_membership = Membership(
        user_id=foreign_owner.id, organization_id=foreign_organization.id, role=Role.OWNER
    )
    inactive_user_membership = Membership(
        user_id=inactive_user.id, organization_id=organization.id, role=Role.MEMBER
    )
    dormant_membership = Membership(
        user_id=dormant_user.id, organization_id=organization.id, role=Role.MEMBER
    )
    dormant_membership.is_active = False
    inactive_organization_membership = Membership(
        user_id=owner.id, organization_id=inactive_organization.id, role=Role.MEMBER
    )

    async with factory() as session:
        async with session.begin():
            for entity in (
                organization,
                foreign_organization,
                inactive_organization,
                owner,
                admin,
                member,
                foreign_owner,
                inactive_user,
                dormant_user,
            ):
                session.add(entity)
            await session.flush()
            for membership in (
                owner_membership,
                admin_membership,
                member_membership,
                foreign_membership,
                inactive_user_membership,
                dormant_membership,
                inactive_organization_membership,
            ):
                session.add(membership)

    async with factory() as session:
        stored = await UserRepository(session).get_by_email("matrix-owner@example.com")
        assert stored is not None
        stored_password_hash = stored.password_hash

    owner_token, owner_session_id = await _issue(factory, "matrix-owner@example.com")
    admin_token, _ = await _issue(factory, "matrix-admin@example.com")
    member_token, _ = await _issue(factory, "matrix-member@example.com")
    foreign_owner_token, _ = await _issue(factory, "matrix-foreign@example.com")

    return {
        "owner_token": owner_token,
        "owner_session_id": owner_session_id,
        "admin_token": admin_token,
        "member_token": member_token,
        "foreign_owner_token": foreign_owner_token,
        "unknown_token": generate_token(),
        "inactive_user_token": await _mint(
            factory, user_id=inactive_user.id, membership_id=inactive_user_membership.id
        ),
        "inactive_membership_token": await _mint(
            factory, user_id=dormant_user.id, membership_id=dormant_membership.id
        ),
        "inactive_organization_token": await _mint(
            factory,
            user_id=owner.id,
            membership_id=inactive_organization_membership.id,
        ),
        "expired_token": await _mint(
            factory,
            user_id=owner.id,
            membership_id=owner_membership.id,
            expires_in=timedelta(hours=-1),
        ),
        "revoked_token": await _mint(
            factory,
            user_id=owner.id,
            membership_id=owner_membership.id,
            revoked=True,
        ),
        "owner_user_id": owner.id,
        "admin_user_id": admin.id,
        "member_user_id": member.id,
        "owner_membership_id": owner_membership.id,
        "organization_id": organization.id,
        "foreign_organization_id": foreign_organization.id,
        "inactive_organization_id": inactive_organization.id,
        "foreign_owner_user_id": foreign_owner.id,
        "stored_password_hash": stored_password_hash,
    }


@pytest_asyncio.fixture
async def matrix_environment() -> AsyncIterator[MatrixEnvironment]:
    base_url = _configured_test_url()
    database_url = await _create_database(base_url)
    engine: AsyncEngine | None = None
    try:
        await _migrate(database_url)
        engine = create_async_engine(database_url)
        session_factory = create_session_factory(engine)
        seeded = await _seed(session_factory)
        protected_accesses: list[tuple[UUID, UUID, str]] = []
        scoped_queries: list[tuple[UUID, UUID]] = []
        yield MatrixEnvironment(
            app=_build_app(session_factory, protected_accesses, scoped_queries),
            session_factory=session_factory,
            protected_accesses=protected_accesses,
            scoped_queries=scoped_queries,
            **seeded,
        )
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(base_url, database_url)


# --------------------------------------------------------------------------- #
# Authentication matrix
# --------------------------------------------------------------------------- #


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _legacy_bearer() -> str:
    """Return the configured compatibility secret, or an equivalent non-credential."""

    if settings.AUTH_SECRET is None:
        return "legacy-compatibility-secret"
    return settings.AUTH_SECRET.get_secret_value() or "legacy-compatibility-secret"


def _credential_cases(environment: MatrixEnvironment) -> list[tuple[str, dict[str, str]]]:
    """Return (case name, request headers) for every authentication row."""

    return [
        ("missing credential", {}),
        ("malformed scheme", {"Authorization": "Basic dXNlcjpwYXNz"}),
        ("empty bearer", {"Authorization": "Bearer "}),
        ("token without scheme", {"Authorization": environment.owner_token}),
        ("bearer with extra word", _bearer("two words")),
        ("unknown token", _bearer(environment.unknown_token)),
        ("expired session", _bearer(environment.expired_token)),
        ("revoked session", _bearer(environment.revoked_token)),
        ("inactive user", _bearer(environment.inactive_user_token)),
        ("inactive membership", _bearer(environment.inactive_membership_token)),
        ("inactive organization", _bearer(environment.inactive_organization_token)),
        ("legacy AUTH_SECRET bearer", _bearer(_legacy_bearer())),
    ]


@pytest.mark.asyncio
async def test_authentication_matrix_fails_closed_without_reaching_a_resource(
    matrix_environment: MatrixEnvironment,
) -> None:
    """One generic 401, no principal and no protected access for every failure."""

    environment = matrix_environment

    async with environment.client() as client:
        for case, headers in _credential_cases(environment):
            response = await client.get("/whoami", headers=headers)
            assert response.status_code == 401, case
            assert response.json()["detail"] == AUTHENTICATION_ERROR_DETAIL, case
            assert response.headers.get("WWW-Authenticate") == "Bearer", case
            assert environment.owner_token not in response.text, case
            assert hash_token(environment.owner_token) not in response.text, case
        valid = await client.get("/whoami", headers=environment.headers(environment.owner_token))

    assert valid.status_code == 200
    assert valid.json()["user_id"] == str(environment.owner_user_id)
    assert valid.json()["organization_id"] == str(environment.organization_id)
    assert valid.json()["role"] == Role.OWNER.value
    # Only the valid request executed a protected route body: no rejected
    # credential built a CurrentPrincipal or read a protected resource.
    assert environment.protected_accesses == [
        (environment.owner_user_id, environment.organization_id, Role.OWNER.value)
    ]


@pytest.mark.asyncio
async def test_active_session_with_inactive_organization_is_401_and_touches_nothing(
    matrix_environment: MatrixEnvironment,
) -> None:
    """Active session + active user + active membership + inactive organization.

    The organization is deactivated after the session was issued, so this also
    proves the per-request re-read instead of issuance-time state.
    """

    environment = matrix_environment

    async with environment.client() as client:
        before = await client.get("/whoami", headers=environment.headers(environment.member_token))
    assert before.status_code == 200
    assert environment.protected_accesses == [
        (environment.member_user_id, environment.organization_id, Role.MEMBER.value)
    ]

    async with environment.session_factory() as session:
        async with session.begin():
            await session.execute(
                update(Organization)
                .where(Organization.id == environment.organization_id)
                .values(is_active=False)
            )

    environment.protected_accesses.clear()
    async with environment.client() as client:
        rejected = await client.get(
            "/whoami", headers=environment.headers(environment.member_token)
        )
        role_rejected = await client.get(
            "/owner-only", headers=environment.headers(environment.owner_token)
        )

    assert rejected.status_code == 401
    assert rejected.json()["detail"] == AUTHENTICATION_ERROR_DETAIL
    assert role_rejected.status_code == 401
    assert environment.protected_accesses == []

    # The inactive-organization state really exists and is stored inactive.
    async with environment.session_factory() as session:
        stored = await OrganizationRepository(session).get(environment.inactive_organization_id)
    assert stored is not None
    assert stored.is_active is False


# --------------------------------------------------------------------------- #
# Tenant isolation matrix
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_tenant_matrix_allows_same_tenant_and_hides_other_tenants(
    matrix_environment: MatrixEnvironment,
) -> None:
    """Same-tenant 200; foreign and nonexistent lookups answer one identical 404."""

    environment = matrix_environment
    missing_organization_id = uuid4()
    statements: list[str] = []
    # Listen on the very engine the request sessions use, so the captured
    # statements are exactly what the HTTP flow executed against PostgreSQL.
    engine: AsyncEngine = environment.session_factory.kw["bind"]

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:  # noqa: ARG001
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
                headers=environment.headers(environment.member_token),
            )
            missing = await client.get(
                f"/tenant/{missing_organization_id}",
                headers=environment.headers(environment.member_token),
            )
            foreign_admin_gated = await client.get(
                f"/tenant-admin/{environment.foreign_organization_id}",
                headers=environment.headers(environment.admin_token),
            )
            foreign_owner = await client.get(
                f"/tenant-admin/{environment.organization_id}",
                headers=environment.headers(environment.foreign_owner_token),
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert same_tenant.status_code == 200
    assert same_tenant.json()["organization_id"] == str(environment.organization_id)
    assert environment.scoped_queries[0] == (
        environment.organization_id,
        environment.organization_id,
    )

    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": RESOURCE_NOT_FOUND_DETAIL}
    assert str(environment.foreign_organization_id) not in foreign.text

    # The foreign row exists: the tenant-scoped lookup is what hides it.
    async with environment.session_factory() as session:
        stored = await OrganizationRepository(session).get(environment.foreign_organization_id)
    assert stored is not None

    # A sufficient role does not cross tenants, and another tenant's owner
    # holds no authority here.
    assert foreign_admin_gated.status_code == 404
    assert foreign_owner.status_code == 404
    assert foreign_owner.json() == foreign.json()

    # The scope handed to the repository is always the principal's tenant.
    assert environment.scoped_queries[-1] == (
        environment.organization_id,
        environment.foreign_organization_id,
    )
    assert environment.protected_accesses == [
        (environment.owner_user_id, environment.organization_id, Role.OWNER.value)
    ]

    # Visibility is decided by the tenant predicate inside the query, not by a
    # global fetch followed by a Python filter.
    scoped_statements = [
        statement
        for statement in statements
        if statement.casefold().count("taskpilot.organizations.id =") >= 2
    ]
    assert scoped_statements, "the HTTP flow must run the tenant-scoped lookup"
    assert any(
        "from taskpilot.organizations" in statement.casefold() for statement in scoped_statements
    )


@pytest.mark.asyncio
async def test_forged_identity_inputs_cannot_select_the_taskpilot_scope(
    matrix_environment: MatrixEnvironment,
) -> None:
    """Query/header user, organization and role claims are never authorization truth."""

    environment = matrix_environment
    forged_user_id = environment.member_user_id
    forged_organization_id = environment.foreign_organization_id
    forged_headers = environment.headers(
        environment.owner_token,
        **{
            "X-User-Id": str(forged_user_id),
            "X-Organization-Id": str(forged_organization_id),
            "X-Role": FORGED_ROLE,
        },
    )
    forged_params = {
        "user_id": str(forged_user_id),
        "organization_id": str(forged_organization_id),
        "role": FORGED_ROLE,
    }

    async with environment.client() as client:
        whoami = await client.get("/whoami", headers=forged_headers, params=forged_params)
        cross_tenant = await client.get(
            f"/tenant/{forged_organization_id}", headers=forged_headers, params=forged_params
        )

    assert whoami.status_code == 200
    body = whoami.json()
    assert body["user_id"] == str(environment.owner_user_id)
    assert body["organization_id"] == str(environment.organization_id)
    assert body["role"] == Role.OWNER.value
    assert body["user_id"] != str(forged_user_id)
    assert body["organization_id"] != str(forged_organization_id)

    # Claiming ownership of the foreign organization changes nothing: the
    # resource is still not found and the repository scope stayed the principal's.
    assert cross_tenant.status_code == 404
    assert environment.scoped_queries[-1] == (
        forged_organization_id,
        environment.organization_id,
    )


@pytest.mark.asyncio
async def test_buggy_repository_result_cannot_turn_a_foreign_row_into_access(
    matrix_environment: MatrixEnvironment,
) -> None:
    """A lookup bug cannot bypass the service-side tenant confirmation."""

    environment = matrix_environment

    class _BuggyRepository:
        """Return another tenant's row for any lookup, as a repository bug would."""

        def __init__(self, foreign: Organization) -> None:
            self._foreign = foreign

        async def get_in_principal_tenant(
            self, organization_id: UUID, principal_organization_id: UUID
        ) -> Organization:
            del organization_id, principal_organization_id
            return self._foreign

    async with environment.session_factory() as session:
        foreign = await OrganizationRepository(session).get(environment.foreign_organization_id)
    assert foreign is not None

    # Healthy repository: the caller's own tenant is visible on this route.
    async with environment.client() as client:
        healthy = await client.get(
            f"/tenant-confirmed/{environment.organization_id}",
            headers=environment.headers(environment.owner_token),
        )
        environment.app.dependency_overrides[_organization_repository] = lambda: _BuggyRepository(
            foreign
        )
        try:
            bugged = await client.get(
                f"/tenant-confirmed/{environment.foreign_organization_id}",
                headers=environment.headers(environment.owner_token),
            )
        finally:
            environment.app.dependency_overrides.pop(_organization_repository, None)

    assert healthy.status_code == 200
    assert healthy.json()["organization_id"] == str(environment.organization_id)

    # The row the buggy lookup returned is foreign, so the confirmation answers
    # the same 404 as a missing resource instead of granting access.
    assert bugged.status_code == 404
    assert bugged.json() == {"detail": RESOURCE_NOT_FOUND_DETAIL}
    assert str(environment.foreign_organization_id) not in bugged.text
    assert environment.protected_accesses == [
        (environment.owner_user_id, environment.organization_id, Role.OWNER.value)
    ]


# --------------------------------------------------------------------------- #
# Caller-identity channels: /threads and AG-UI
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_threads_query_identity_cannot_select_the_taskpilot_scope(
    matrix_environment: MatrixEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``/threads?user_id=`` is an upstream claim, never a TaskPilot scope selector."""

    environment = matrix_environment
    checkpointer = _RecordingCheckpointer()
    monkeypatch.setattr(
        "service.service.get_agent",
        lambda agent_id: type(
            "Agent", (), {"checkpointer": checkpointer, "store": None, "id": agent_id}
        )(),
    )
    forged_user_id = str(environment.member_user_id)

    async with environment.client() as client:
        unauthenticated = await client.get("/whoami", params={"user_id": forged_user_id})
        whoami = await client.get(
            "/whoami",
            params={"user_id": forged_user_id},
            headers=environment.headers(environment.owner_token),
        )
        threads = await client.get(
            "/threads",
            params={"user_id": forged_user_id},
            headers=environment.headers(environment.owner_token),
        )

    # Without a TaskPilot credential the claim authenticates nothing.
    assert unauthenticated.status_code == 401
    # With one, the principal is still the token's user and tenant.
    assert whoami.status_code == 200
    assert whoami.json()["user_id"] == str(environment.owner_user_id)
    assert whoami.json()["user_id"] != forged_user_id
    assert whoami.json()["organization_id"] == str(environment.organization_id)

    # The upstream endpoint still consumes the caller's user_id for its own
    # LangGraph scope - the documented, unchanged upstream trust model ...
    assert threads.status_code == 200
    assert checkpointer.filters == [
        {"user_id": forged_user_id, "agent_id": DEFAULT_AGENT, "step": THREAD_HEAD_STEP}
    ]
    # ... while that claim neither authenticates nor discloses TaskPilot state.
    assert str(environment.organization_id) not in threads.text
    assert str(environment.owner_session_id) not in threads.text


@pytest.mark.asyncio
async def test_agui_configurable_identity_cannot_select_the_taskpilot_scope(
    matrix_environment: MatrixEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AG-UI forwarded identity reaches LangGraph config, never TaskPilot policy."""

    environment = matrix_environment
    monkeypatch.setattr("service.agui.get_agent", lambda agent_id: _build_upstream_agui_agent())
    _agui_configurable.clear()

    forged_configurable = {
        "user_id": str(environment.member_user_id),
        "organization_id": str(environment.foreign_organization_id),
        "role": FORGED_ROLE,
    }
    body = {
        "threadId": "t026-thread",
        "runId": "t026-run",
        "messages": [{"id": "msg-1", "role": "user", "content": "Hello"}],
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {"configurable": forged_configurable},
    }

    async with environment.client() as client:
        unauthenticated = await client.get("/whoami", params=forged_configurable)
        whoami = await client.get(
            "/whoami",
            params=forged_configurable,
            headers=environment.headers(environment.owner_token),
        )
        run = await client.post(
            "/agui/run", json=body, headers=environment.headers(environment.owner_token)
        )

    assert unauthenticated.status_code == 401
    assert whoami.status_code == 200
    assert whoami.json()["user_id"] == str(environment.owner_user_id)
    assert whoami.json()["organization_id"] == str(environment.organization_id)

    # The real AG-UI router forwarded the caller's identity claim unchanged ...
    assert run.status_code == 200
    assert _agui_configurable["user_id"] == str(environment.member_user_id)
    assert _agui_configurable["organization_id"] == str(environment.foreign_organization_id)
    assert _agui_configurable["role"] == FORGED_ROLE
    # ... and it changed neither the TaskPilot principal nor the response body.
    assert str(environment.foreign_organization_id) not in run.text
    assert environment.owner_token not in run.text
    assert environment.protected_accesses == [
        (environment.owner_user_id, environment.organization_id, Role.OWNER.value)
    ]


# --------------------------------------------------------------------------- #
# Role and approval matrix
# --------------------------------------------------------------------------- #


ROLE_OPERATION_MATRIX: list[tuple[str, str, str, int]] = [
    # (method, path, role, expected status)
    ("GET", "/owner-only", "owner", 200),
    ("GET", "/owner-only", "admin", 403),
    ("GET", "/owner-only", "member", 403),
    ("GET", "/admin-only", "owner", 403),
    ("GET", "/admin-only", "admin", 200),
    ("GET", "/admin-only", "member", 403),
    ("GET", "/member-only", "owner", 403),
    ("GET", "/member-only", "admin", 403),
    ("GET", "/member-only", "member", 200),
    ("POST", "/approval-decision", "owner", 200),
    ("POST", "/approval-decision", "admin", 200),
    ("POST", "/approval-decision", "member", 403),
]


@pytest.mark.asyncio
async def test_role_and_approval_matrix_is_decided_by_the_server_derived_role(
    matrix_environment: MatrixEnvironment,
) -> None:
    """Allowed roles succeed and insufficient roles get one non-disclosing 403.

    No role hierarchy is assumed: a ``member``-only operation rejects an owner,
    and the L2 approval decision (``APPROVAL_DECISION_ROLES``) allows owner and
    admin while refusing a member.
    """

    environment = matrix_environment
    denials: list[httpx.Response] = []
    allowed: list[httpx.Response] = []

    async with environment.client() as client:
        for method, path, role, expected in ROLE_OPERATION_MATRIX:
            response = await getattr(client, method.lower())(
                path, headers=environment.headers(environment.token_for_role(role))
            )
            case = f"{method} {path} as {role}"
            assert response.status_code == expected, case
            (denials if expected == 403 else allowed).append(response)

    assert len(allowed) == 5
    assert len(denials) == 7
    assert {response.json()["detail"] for response in denials} == {AUTHORIZATION_FORBIDDEN_DETAIL}
    # Every allowed operation really executed its protected body, in matrix order.
    assert [access[2] for access in environment.protected_accesses] == [
        "owner",
        "admin",
        "member",
        "owner",
        "admin",
    ]


@pytest.mark.asyncio
async def test_unauthenticated_requests_never_reach_a_role_decision(
    matrix_environment: MatrixEnvironment,
) -> None:
    """A missing principal is 401 for every operation, never a 403."""

    environment = matrix_environment

    async with environment.client() as client:
        responses = [
            await client.get("/owner-only"),
            await client.get("/admin-only"),
            await client.get("/member-only"),
            await client.post("/approval-decision"),
            await client.get(f"/tenant/{environment.organization_id}"),
        ]

    assert [response.status_code for response in responses] == [401, 401, 401, 401, 401]
    assert {response.json()["detail"] for response in responses} == {AUTHENTICATION_ERROR_DETAIL}
    assert environment.protected_accesses == []
    assert environment.scoped_queries == []


# --------------------------------------------------------------------------- #
# Secret matrix
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_credentials_never_appear_in_responses_or_structured_logs(
    matrix_environment: MatrixEnvironment, caplog: pytest.LogCaptureFixture
) -> None:
    """Password, hash, raw token and digest stay out of every response and log."""

    environment = matrix_environment
    configure_logging()
    secrets = {
        "plaintext password": PASSWORD,
        "stored password hash": environment.stored_password_hash,
        "raw session token": environment.owner_token,
        "session token digest": hash_token(environment.owner_token),
    }

    with caplog.at_level(logging.DEBUG):
        async with environment.client() as client:
            responses = [
                await client.get("/whoami", headers=environment.headers(environment.owner_token)),
                await client.get("/whoami"),
                await client.get(
                    "/owner-only", headers=environment.headers(environment.member_token)
                ),
                await client.get(
                    f"/tenant/{environment.foreign_organization_id}",
                    headers=environment.headers(environment.owner_token),
                ),
            ]
        # This is what a debug log of the persisted rows would emit.
        async with environment.session_factory() as session:
            user = await UserRepository(session).get_by_email("matrix-owner@example.com")
            auth_session = await AuthSessionRepository(session).get(environment.owner_session_id)
        assert user is not None and auth_session is not None
        logging.getLogger("tests.t026").debug("user=%r session=%r", user, auth_session)

    assert [response.status_code for response in responses] == [200, 401, 403, 404]
    for response in responses:
        for label, secret in secrets.items():
            assert secret not in response.text, label
            assert secret not in repr(dict(response.headers)), label
        assert "$argon2id$" not in response.text
        assert "token_hash" not in response.text

    for label, secret in secrets.items():
        assert secret not in caplog.text, label
    assert "$argon2id$" not in caplog.text
    assert "token_hash" not in caplog.text
    # Denials stay non-disclosing: no tenant, user or membership identifier.
    denied = responses[2]
    assert str(environment.organization_id) not in denied.text
    assert str(environment.member_user_id) not in denied.text
