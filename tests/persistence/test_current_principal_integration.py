"""T024 live-PostgreSQL tests for the CurrentPrincipal request dependency.

These drive a real FastAPI app through the real ``require_principal``
dependency, the real ``AuthService``, and a real disposable PostgreSQL database.
Requests go through ``httpx.ASGITransport`` inside the test's own event loop so
psycopg keeps using the selector loop on Windows.  The only route involved is a
test-only protected route; T024 adds no production endpoint.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI
from psycopg import sql
from sqlalchemy import make_url, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

try:
    from alembic import command
    from alembic.config import Config
except ModuleNotFoundError:  # pragma: no cover - host environment guard
    pytest.skip("HOST_ENVIRONMENT: Alembic dependency is not installed", allow_module_level=True)

from persistence.engine import create_async_engine, create_session_factory
from persistence.models import AuthSession, Membership, Organization, Role, User
from persistence.passwords import hash_password
from persistence.repositories import AuthSessionRepository, MembershipRepository
from persistence.tokens import generate_token, hash_token
from service import auth_dependency
from service.auth_dependency import AUTHENTICATION_ERROR_DETAIL, require_principal
from service.session import SESSION_TTL, AuthService, CurrentPrincipal

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
    name = f"taskpilot_t024_{uuid4().hex}"
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
class PrincipalEnvironment:
    """Live handles for exercising the authentication boundary."""

    app: FastAPI
    session_factory: async_sessionmaker[AsyncSession]
    database_url: str
    owner_token: str
    member_token: str
    inactive_user_token: str
    inactive_membership_token: str
    inactive_organization_token: str
    expired_token: str
    revoked_token: str
    mismatched_membership_token: str
    user_id: UUID
    organization_id: UUID
    membership_id: UUID
    session_id: UUID

    @asynccontextmanager
    async def client(self) -> AsyncIterator[httpx.AsyncClient]:
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://taskpilot.test"
        ) as client:
            yield client

    @staticmethod
    def auth_headers(token: str | None, **extra: str) -> dict[str, str]:
        headers = dict(extra)
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def _build_app(session_factory: async_sessionmaker[AsyncSession]) -> FastAPI:
    app = FastAPI()

    @app.get("/whoami")
    async def whoami(
        principal: Annotated[CurrentPrincipal, Depends(require_principal)],
        user_id: str | None = None,
        organization_id: str | None = None,
        role: str | None = None,
    ) -> dict[str, str]:
        # Caller-supplied identity inputs exist only to prove they are ignored.
        del user_id, organization_id, role
        return {
            "user_id": str(principal.user_id),
            "membership_id": str(principal.membership_id),
            "organization_id": str(principal.organization_id),
            "role": principal.role.value,
            "session_id": str(principal.session_id),
        }

    app.dependency_overrides[auth_dependency.get_session_factory] = lambda: session_factory
    return app


async def _insert_membership(
    factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    organization_id: UUID,
    role: Role,
    *,
    is_active: bool = True,
) -> Membership:
    membership = Membership(
        user_id=user_id,
        organization_id=organization_id,
        role=role,
        is_active=is_active,
    )
    async with factory() as session:
        async with session.begin():
            await MembershipRepository(session).add(membership)
    return membership


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    owner = User(email="owner-principal@example.com", password_hash=hash_password(PASSWORD))
    member = User(email="member-principal@example.com", password_hash=hash_password(PASSWORD))
    inactive_user = User(
        email="inactive-principal@example.com", password_hash=hash_password(PASSWORD)
    )
    inactive_user.is_active = False
    organization = Organization(name="Principal Org")
    other_organization = Organization(name="Other Org")
    inactive_organization = Organization(name="Inactive Org")
    inactive_organization.is_active = False

    # Assign identifiers explicitly so memberships can reference them before the
    # rows are flushed.
    for user in (owner, member, inactive_user):
        user.id = uuid4()
    for tenant in (organization, other_organization, inactive_organization):
        tenant.id = uuid4()

    owner_membership = Membership(
        user_id=owner.id, organization_id=organization.id, role=Role.OWNER, is_active=True
    )
    member_membership = Membership(
        user_id=member.id,
        organization_id=other_organization.id,
        role=Role.MEMBER,
        is_active=True,
    )
    inactive_user_membership = Membership(
        user_id=inactive_user.id,
        organization_id=organization.id,
        role=Role.MEMBER,
        is_active=True,
    )
    inactive_organization_membership = Membership(
        user_id=owner.id,
        organization_id=inactive_organization.id,
        role=Role.MEMBER,
        is_active=True,
    )
    inactive_membership = Membership(
        user_id=owner.id,
        organization_id=other_organization.id,
        role=Role.MEMBER,
        is_active=False,
    )

    async with factory() as session:
        async with session.begin():
            # Parents first: without ORM relationships the unit of work cannot
            # infer insert ordering between users/organizations and memberships.
            for entity in (
                organization,
                other_organization,
                inactive_organization,
                owner,
                member,
                inactive_user,
            ):
                session.add(entity)
            await session.flush()
            for membership in (
                owner_membership,
                member_membership,
                inactive_user_membership,
                inactive_organization_membership,
                inactive_membership,
            ):
                session.add(membership)

    async def issue(email: str) -> tuple[str, UUID]:
        async with factory() as session:
            async with session.begin():
                result = await AuthService(session).login(email, PASSWORD)
            assert hasattr(result, "token"), result
            return result.token, result.session_id  # type: ignore[union-attr]

    async def mint(
        *,
        user_id: UUID,
        membership_id: UUID,
        expires_in: timedelta = SESSION_TTL,
        revoked: bool = False,
    ) -> str:
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

    owner_token, owner_session_id = await issue("owner-principal@example.com")
    member_token, _ = await issue("member-principal@example.com")

    return {
        "owner_token": owner_token,
        "owner_session_id": owner_session_id,
        "member_token": member_token,
        "inactive_user_token": await mint(
            user_id=inactive_user.id, membership_id=inactive_user_membership.id
        ),
        "inactive_membership_token": await mint(
            user_id=owner.id, membership_id=inactive_membership.id
        ),
        "inactive_organization_token": await mint(
            user_id=owner.id, membership_id=inactive_organization_membership.id
        ),
        "expired_token": await mint(
            user_id=owner.id, membership_id=owner_membership.id, expires_in=timedelta(hours=-1)
        ),
        "revoked_token": await mint(
            user_id=owner.id, membership_id=owner_membership.id, revoked=True
        ),
        "mismatched_membership_token": await mint(
            user_id=owner.id, membership_id=member_membership.id
        ),
        "user_id": owner.id,
        "organization_id": organization.id,
        "membership_id": owner_membership.id,
    }


@pytest_asyncio.fixture
async def principal_environment() -> AsyncIterator[PrincipalEnvironment]:
    base_url = _configured_test_url()
    database_url = await _create_database(base_url)
    engine: AsyncEngine | None = None
    try:
        await _migrate(database_url)
        engine = create_async_engine(database_url)
        session_factory = create_session_factory(engine)
        seeded = await _seed(session_factory)

        yield PrincipalEnvironment(
            app=_build_app(session_factory),
            session_factory=session_factory,
            database_url=database_url,
            owner_token=seeded["owner_token"],
            member_token=seeded["member_token"],
            inactive_user_token=seeded["inactive_user_token"],
            inactive_membership_token=seeded["inactive_membership_token"],
            inactive_organization_token=seeded["inactive_organization_token"],
            expired_token=seeded["expired_token"],
            revoked_token=seeded["revoked_token"],
            mismatched_membership_token=seeded["mismatched_membership_token"],
            user_id=seeded["user_id"],
            organization_id=seeded["organization_id"],
            membership_id=seeded["membership_id"],
            session_id=seeded["owner_session_id"],
        )
    finally:
        if engine is not None:
            await engine.dispose()
        await _drop_database(base_url, database_url)


# --------------------------------------------------------------------------- #
# Valid credential
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_valid_session_yields_server_derived_principal(
    principal_environment: PrincipalEnvironment,
) -> None:
    async with principal_environment.client() as client:
        response = await client.get(
            "/whoami", headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token)
        )

    assert response.status_code == 200
    body = response.json()
    assert body["user_id"] == str(principal_environment.user_id)
    assert body["membership_id"] == str(principal_environment.membership_id)
    assert body["organization_id"] == str(principal_environment.organization_id)
    assert body["session_id"] == str(principal_environment.session_id)
    assert body["role"] == "owner"


@pytest.mark.asyncio
async def test_caller_supplied_identity_cannot_change_the_principal(
    principal_environment: PrincipalEnvironment,
) -> None:
    forged_user = str(uuid4())
    forged_organization = str(uuid4())

    async with principal_environment.client() as client:
        response = await client.get(
            "/whoami",
            headers=PrincipalEnvironment.auth_headers(
                principal_environment.owner_token,
                **{
                    "X-User-Id": forged_user,
                    "X-Organization-Id": forged_organization,
                    "X-Role": "owner",
                },
            ),
            params={
                "user_id": forged_user,
                "organization_id": forged_organization,
                "role": "member",
            },
        )

    assert response.status_code == 200
    body = response.json()
    # Every value still comes from the database, not the query string or headers.
    assert body["user_id"] == str(principal_environment.user_id)
    assert body["organization_id"] == str(principal_environment.organization_id)
    assert body["user_id"] != forged_user
    assert body["organization_id"] != forged_organization


@pytest.mark.asyncio
async def test_legacy_auth_secret_is_not_a_taskpilot_credential(
    principal_environment: PrincipalEnvironment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_SECRET", "legacy-compatibility-secret")

    async with principal_environment.client() as client:
        response = await client.get(
            "/whoami",
            headers=PrincipalEnvironment.auth_headers("legacy-compatibility-secret"),
        )

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_ERROR_DETAIL


# --------------------------------------------------------------------------- #
# Credential failures
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": ""},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic dXNlcjpwYXNz"},
        {"Authorization": "Token abcdef"},
        {"Authorization": "opaque-session-token-without-scheme"},
        {"Authorization": "Bearer two words"},
    ],
)
async def test_missing_or_malformed_credentials_return_one_generic_401(
    principal_environment: PrincipalEnvironment, headers: dict[str, str]
) -> None:
    async with principal_environment.client() as client:
        response = await client.get("/whoami", headers=headers)

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_ERROR_DETAIL
    assert response.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token_attribute",
    [
        "inactive_user_token",
        "inactive_membership_token",
        "inactive_organization_token",
        "expired_token",
        "revoked_token",
        "mismatched_membership_token",
    ],
)
async def test_invalid_or_inconsistent_sessions_return_one_generic_401(
    principal_environment: PrincipalEnvironment, token_attribute: str
) -> None:
    token = getattr(principal_environment, token_attribute)

    async with principal_environment.client() as client:
        response = await client.get("/whoami", headers=PrincipalEnvironment.auth_headers(token))

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_ERROR_DETAIL
    assert token not in response.text
    assert token not in dict(response.headers).values().__str__()


@pytest.mark.asyncio
async def test_unknown_token_returns_401(principal_environment: PrincipalEnvironment) -> None:
    async with principal_environment.client() as client:
        response = await client.get(
            "/whoami", headers=PrincipalEnvironment.auth_headers(generate_token())
        )

    assert response.status_code == 401
    assert response.json()["detail"] == AUTHENTICATION_ERROR_DETAIL


@pytest.mark.asyncio
async def test_authentication_failure_never_leaks_credential_material(
    principal_environment: PrincipalEnvironment,
) -> None:
    token = principal_environment.owner_token

    async with principal_environment.client() as client:
        failed = await client.get(
            "/whoami", headers=PrincipalEnvironment.auth_headers(token + "tampered")
        )
        succeeded = await client.get("/whoami", headers=PrincipalEnvironment.auth_headers(token))

    for response in (failed, succeeded):
        assert token not in response.text
        assert hash_token(token) not in response.text
        assert "Authorization" not in response.text
    assert succeeded.status_code == 200
    assert "token" not in succeeded.text


# --------------------------------------------------------------------------- #
# Live state changes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_state_deactivated_after_issuance_is_rejected_on_the_next_request(
    principal_environment: PrincipalEnvironment,
) -> None:
    async def statement_for(target: str) -> Any:
        if target == "user":
            return update(User).where(User.id == principal_environment.user_id)
        if target == "membership":
            return (
                update(Membership)
                .where(Membership.id == principal_environment.membership_id)
                .values(is_active=False)
            )
        return (
            update(Organization)
            .where(Organization.id == principal_environment.organization_id)
            .values(is_active=False)
        )

    async with principal_environment.client() as client:
        assert (
            await client.get(
                "/whoami",
                headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token),
            )
        ).status_code == 200

        for target in ("membership", "organization"):
            async with principal_environment.session_factory() as session:
                async with session.begin():
                    await session.execute(await statement_for(target))
            response = await client.get(
                "/whoami",
                headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token),
            )
            assert response.status_code == 401, target


@pytest.mark.asyncio
async def test_role_change_is_reflected_without_revoking_the_session(
    principal_environment: PrincipalEnvironment,
) -> None:
    async with principal_environment.client() as client:
        first = await client.get(
            "/whoami",
            headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token),
        )
        assert first.status_code == 200
        assert first.json()["role"] == "owner"

        async with principal_environment.session_factory() as session:
            async with session.begin():
                await session.execute(
                    update(Membership)
                    .where(Membership.id == principal_environment.membership_id)
                    .values(role=Role.MEMBER.value)
                )

        second = await client.get(
            "/whoami",
            headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token),
        )

    assert second.status_code == 200
    # Fresh role from the current membership row; same session, same tenant.
    assert second.json()["role"] == "member"
    assert second.json()["session_id"] == first.json()["session_id"]
    assert second.json()["membership_id"] == first.json()["membership_id"]


# --------------------------------------------------------------------------- #
# Request-scoped session lifecycle with real sessions
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_unauthenticated_requests_never_leak_a_pooled_connection(
    principal_environment: PrincipalEnvironment,
) -> None:
    """Rejected requests must still close their real request-scoped session.

    Every real ``AsyncSession`` created by the dependency is instrumented with
    SQLAlchemy's ``after_close`` event, so the exact close count is asserted
    rather than inferred from pool behaviour.  The requests run through the real
    dependency chain, including the ones rejected before authentication.
    """

    close_counts: dict[int, int] = {}
    rollback_counts: dict[int, int] = {}
    wrapped: list[Any] = []
    real_factory = principal_environment.session_factory

    class _TrackedSessionProxy:
        """Delegate to a real session while counting lifecycle calls."""

        def __init__(self, inner: AsyncSession) -> None:
            self._inner = inner
            close_counts[id(self)] = 0
            rollback_counts[id(self)] = 0

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def close(self) -> None:
            close_counts[id(self)] += 1
            await self._inner.close()

        async def rollback(self) -> None:
            rollback_counts[id(self)] += 1
            await self._inner.rollback()

    def factory() -> _TrackedSessionProxy:
        proxy = _TrackedSessionProxy(real_factory())
        wrapped.append(proxy)
        return proxy

    app = _build_app(factory)  # type: ignore[arg-type]
    app.dependency_overrides[auth_dependency.get_session_factory] = lambda: factory

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        statuses = []
        for headers in (
            None,
            {"Authorization": "Basic dXNlcjpwYXNz"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer unknown-token"},
        ):
            response = await client.get("/whoami", headers=headers or {})
            statuses.append(response.status_code)

        ok = await client.get(
            "/whoami",
            headers=PrincipalEnvironment.auth_headers(principal_environment.owner_token),
        )
        statuses.append(ok.status_code)

    assert statuses == [401, 401, 401, 401, 200]
    # One real session per request, each closed exactly once - including the
    # requests rejected before any authentication call.
    assert len(wrapped) == len(statuses)
    assert [close_counts[id(proxy)] for proxy in wrapped] == [1] * len(statuses)
    assert [rollback_counts[id(proxy)] for proxy in wrapped] == [0] * len(statuses)
    # Leaving tracked connections checked in proves they were really released.
    await real_factory.kw["bind"].dispose()
