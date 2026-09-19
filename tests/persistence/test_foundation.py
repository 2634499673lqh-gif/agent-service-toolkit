import ast
import asyncio
import sys
import traceback
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import UniqueConstraint

from core.settings import Settings
from persistence.base import Base
from persistence.engine import normalize_business_database_url
from persistence.identity import canonicalize_email
from persistence.migration_filters import include_name
from persistence.models import (
    AuthSession,
    Membership,
    Organization,
    Role,
    Task,
    TaskStatus,
    User,
    utc_now,
)
from persistence.repositories import (
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)


def _fake_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"USE_FAKE_MODEL": True, "_env_file": None}
    values.update(overrides)
    return Settings(**values)


def test_taskpilot_metadata_is_schema_scoped() -> None:
    assert Base.metadata.schema == "taskpilot"
    assert set(Base.metadata.tables) == {
        "taskpilot.organizations",
        "taskpilot.users",
        "taskpilot.memberships",
        "taskpilot.auth_sessions",
        "taskpilot.tasks",
    }
    assert Organization.__table__.schema == "taskpilot"
    assert User.__table__.schema == "taskpilot"
    assert Membership.__table__.schema == "taskpilot"
    assert AuthSession.__table__.schema == "taskpilot"
    assert Task.__table__.schema == "taskpilot"


def test_task_status_and_defaults_are_frozen() -> None:
    assert [status.value for status in TaskStatus] == [
        "draft",
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ]
    task = Task(
        organization_id=UUID("11111111-1111-4111-8111-111111111111"),
        created_by_user_id=UUID("22222222-2222-4222-8222-222222222222"),
        title="Research task",
    )
    assert task.status is TaskStatus.DRAFT
    assert task.description is None
    assert task.id is None
    assert task.created_at is None
    assert task.updated_at is None


def test_task_metadata_declares_tenant_creator_and_status_constraints() -> None:
    table = Task.__table__
    assert table.c.id.type.python_type is UUID
    assert table.c.organization_id.nullable is False
    assert table.c.created_by_user_id.nullable is False
    assert table.c.title.nullable is False
    assert table.c.description.nullable is True
    assert table.c.status.nullable is False
    assert table.c.status.server_default is not None
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert {constraint.name for constraint in table.constraints if constraint.name} == {
        "ck_tasks_task_title_not_blank",
        "ck_tasks_task_status_valid",
        "pk_tasks",
        "fk_tasks_organization_id_organizations",
        "fk_tasks_created_by_user_id_users",
    }
    foreign_keys = {fk.parent.name: fk for fk in table.foreign_keys}
    assert foreign_keys["organization_id"].target_fullname == "taskpilot.organizations.id"
    assert foreign_keys["created_by_user_id"].target_fullname == "taskpilot.users.id"
    assert all(fk.ondelete == "RESTRICT" for fk in foreign_keys.values())
    assert {index.name for index in table.indexes} == {
        "ix_tasks_organization_id",
        "ix_tasks_created_by_user_id",
        "ix_tasks_status",
    }


def test_role_enum_matches_the_frozen_v1_role_set() -> None:
    assert [role.value for role in Role] == ["owner", "admin", "member"]


def test_membership_defaults_and_column_contract() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    organization_id = UUID("22222222-2222-4222-8222-222222222222")
    membership = Membership(user_id=user_id, organization_id=organization_id, role="member")

    assert membership.id is None  # SQLAlchemy applies uuid4 on flush.
    assert membership.user_id == user_id
    assert membership.organization_id == organization_id
    assert membership.role is Role.MEMBER
    assert membership.is_active is True
    assert membership.created_at is None
    assert membership.updated_at is None

    table = Membership.__table__
    assert table.c.id.type.python_type is UUID
    assert table.c.id.primary_key is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert set(Membership.__table__.c) >= {
        Membership.__table__.c.user_id,
        Membership.__table__.c.organization_id,
    }


def test_membership_role_is_constrained_to_the_frozen_set() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    organization_id = UUID("22222222-2222-4222-8222-222222222222")

    for role in Role:
        membership = Membership(user_id=user_id, organization_id=organization_id, role=role)
        assert membership.role is role
    assert Membership(user_id=user_id, organization_id=organization_id, role="admin").role is (
        Role.ADMIN
    )

    with pytest.raises(ValueError):
        Membership(user_id=user_id, organization_id=organization_id, role="superadmin")


def test_membership_metadata_declares_tenant_uniqueness_and_restrict_foreign_keys() -> None:
    table = Membership.__table__
    unique_columns = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("organization_id", "user_id") in unique_columns

    foreign_keys = {fk.parent.name: fk for fk in table.foreign_keys}
    assert set(foreign_keys) == {"user_id", "organization_id"}
    assert foreign_keys["user_id"].target_fullname == "taskpilot.users.id"
    assert foreign_keys["user_id"].ondelete == "RESTRICT"
    assert foreign_keys["organization_id"].target_fullname == "taskpilot.organizations.id"
    assert foreign_keys["organization_id"].ondelete == "RESTRICT"

    assert {index.name for index in table.indexes} == {
        "ix_memberships_user_id",
        "ix_memberships_organization_id",
    }


def test_auth_session_metadata_declares_digest_uniqueness_and_restrict_foreign_keys() -> None:
    table = AuthSession.__table__

    assert table.c.id.type.python_type is UUID
    assert table.c.id.primary_key is True
    assert table.c.token_hash.type.length == 64
    assert table.c.token_hash.nullable is False
    assert table.c.expires_at.type.timezone is True
    assert table.c.expires_at.nullable is False
    assert table.c.revoked_at.nullable is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True

    unique_columns = {
        tuple(sorted(constraint.columns.keys()))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("token_hash",) in unique_columns

    foreign_keys = {fk.parent.name: fk for fk in table.foreign_keys}
    assert set(foreign_keys) == {"user_id", "membership_id"}
    assert foreign_keys["user_id"].target_fullname == "taskpilot.users.id"
    assert foreign_keys["user_id"].ondelete == "RESTRICT"
    assert foreign_keys["membership_id"].target_fullname == "taskpilot.memberships.id"
    assert foreign_keys["membership_id"].ondelete == "RESTRICT"

    assert {index.name for index in table.indexes} == {
        "ix_auth_sessions_user_id",
        "ix_auth_sessions_membership_id",
        "ix_auth_sessions_expires_at",
    }


def test_organization_defaults_are_application_safe() -> None:
    organization = Organization(name="Acme")
    assert organization.id is None  # SQLAlchemy applies uuid4 on flush, not import/constructor.
    assert organization.name == "Acme"
    assert organization.is_active is None
    assert organization.created_at is None

    now = utc_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == UTC.utcoffset(now)
    assert Organization.__table__.c.id.type.python_type is UUID


def test_email_canonicalization_preserves_display_and_uses_casefold() -> None:
    display_email, normalized_email = canonicalize_email("\u00a0Straße@Example.COM\u2003")

    assert display_email == "Straße@Example.COM"
    assert normalized_email == "strasse@example.com"
    assert display_email.lower() != normalized_email


def test_email_canonicalization_uses_real_unicode_casefold() -> None:
    # ``lower()`` alone cannot produce these identities, so this pins the
    # contract to ``str.casefold`` instead of PostgreSQL ``lower()``/collation.
    assert canonicalize_email("Straße@Example.COM") == (
        "Straße@Example.COM",
        "strasse@example.com",
    )
    assert canonicalize_email("İstanbul@Example.COM") == (
        "İstanbul@Example.COM",
        "i\u0307stanbul@example.com",
    )
    assert canonicalize_email("GREEK\u03a3@Example.COM")[1] == "greek\u03c3@example.com"
    assert (
        canonicalize_email("GREEK\u03c2@Example.COM")[1]
        == canonicalize_email("GREEK\u03a3@Example.COM")[1]
    )


def test_user_derives_normalized_email_and_hides_password_hash() -> None:
    password_hash = "$argon2id$v=19$test-only-opaque-value"
    user = User(email="  Alice@Example.COM  ", password_hash=password_hash)

    assert user.id is None
    assert user.email == "Alice@Example.COM"
    assert user.normalized_email == "alice@example.com"
    assert user.is_active is None
    assert user.created_at is None
    assert password_hash not in repr(user)
    assert "password_hash" not in repr(user)
    assert password_hash not in str(user)
    assert User.__table__.c.id.type.python_type is UUID
    assert User.__table__.c.created_at.type.timezone is True
    assert User.__table__.c.updated_at.type.timezone is True

    user.email = " Bob@Example.COM "
    assert user.email == "Bob@Example.COM"
    assert user.normalized_email == "bob@example.com"


def test_empty_email_is_rejected_by_the_identity_helper_and_user_model() -> None:
    with pytest.raises(ValueError, match="must not be blank"):
        canonicalize_email("\u2003\t\n")
    with pytest.raises(ValueError, match="must not be blank"):
        User(email=" \u00a0 ", password_hash="opaque-test-hash")


def test_user_model_rejects_non_string_email() -> None:
    with pytest.raises(TypeError, match="must be a string"):
        User(email=None, password_hash="opaque-test-hash")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="derived from email"):
        User(  # type: ignore[call-arg]
            email="user@example.com",
            normalized_email="forged@example.com",
            password_hash="opaque-test-hash",
        )


def test_business_url_normalizes_postgresql_and_rejects_sqlite() -> None:
    normalized = normalize_business_database_url(
        SecretStr("postgresql://user:secret@localhost/taskpilot")
    )
    assert normalized.startswith("postgresql+psycopg://user:secret@localhost/taskpilot")
    with pytest.raises(ValueError, match="PostgreSQL"):
        normalize_business_database_url("sqlite+aiosqlite:///business.db")


def test_production_code_never_imports_the_migration_toolchain() -> None:
    """T026 persistence row: migrations stay an out-of-band, forward-only step.

    ADR-004 decision 6: production deploys run ``alembic upgrade head`` as a
    release/job step before application startup, and application startup never
    auto-migrates.  Downgrade exists only as the development/test one-revision
    verification procedure, so no production module may import the migration
    toolchain and no startup path can migrate or downgrade the schema.
    """

    source_root = Path(__file__).resolve().parents[2] / "src"
    offenders: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module.split(".")[0] == "alembic" for module in modules):
                offenders.append(f"{path.name}:{node.lineno}")

    assert offenders == []


def test_migration_reflection_filters_only_taskpilot() -> None:
    assert include_name("taskpilot", "schema", {}) is True
    assert include_name("public", "schema", {}) is False
    assert include_name(None, "schema", {}) is False
    assert include_name("organizations", "table", {"schema_name": "taskpilot"}) is True
    assert include_name("checkpoints", "table", {"schema_name": "public"}) is False
    assert include_name("checkpoints", "table", {"schema_name": None}) is False


def test_invalid_business_url_errors_do_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "super-secret-password"
    dsn = f"postgresql://user:{secret}@host/taskpilot"

    def raising_parser(value: str) -> object:
        raise ValueError(f"parser saw {value}")

    monkeypatch.setattr("persistence.engine.make_url", raising_parser)
    try:
        normalize_business_database_url(dsn)
    except ValueError:
        trace = traceback.format_exc()
    else:
        pytest.fail("invalid DSN unexpectedly parsed")
    assert secret not in trace
    assert dsn not in trace


def test_settings_rejects_sqlite_business_url_without_affecting_langgraph_default() -> None:
    with pytest.raises(ValidationError, match="TASKPILOT_DATABASE_URL"):
        _fake_settings(TASKPILOT_DATABASE_URL="sqlite+aiosqlite:///business.db")
    settings = _fake_settings()
    assert settings.DATABASE_TYPE.value == "sqlite"
    assert settings.TASKPILOT_DATABASE_URL is None


@pytest.mark.asyncio
async def test_windows_persistence_tests_use_selector_event_loop() -> None:
    if sys.platform != "win32":
        pytest.skip("Windows-only psycopg event-loop compatibility check")
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)


@pytest.mark.asyncio
async def test_repository_flushes_without_commit() -> None:
    session = Mock()
    session.flush = AsyncMock()
    repository = OrganizationRepository(session)
    organization = Organization(name="Acme")

    assert await repository.add(organization) is organization
    session.add.assert_called_once_with(organization)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_called()

    user_session = Mock()
    user_session.flush = AsyncMock()
    user_repository = UserRepository(user_session)
    user = User(email="user@example.com", password_hash="opaque-test-hash")

    assert await user_repository.add(user) is user
    user_session.add.assert_called_once_with(user)
    user_session.flush.assert_awaited_once_with()
    user_session.commit.assert_not_called()

    membership_session = Mock()
    membership_session.flush = AsyncMock()
    membership_repository = MembershipRepository(membership_session)
    membership = Membership(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        organization_id=UUID("22222222-2222-4222-8222-222222222222"),
        role="owner",
    )

    assert await membership_repository.add(membership) is membership
    membership_session.add.assert_called_once_with(membership)
    membership_session.flush.assert_awaited_once_with()
    membership_session.commit.assert_not_called()

    session_session = Mock()
    session_session.flush = AsyncMock()
    session_repository = AuthSessionRepository(session_session)
    auth_session = AuthSession(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        membership_id=UUID("22222222-2222-4222-8222-222222222222"),
        token_hash="0" * 64,
        expires_at=utc_now(),
    )

    assert await session_repository.add(auth_session) is auth_session
    session_session.add.assert_called_once_with(auth_session)
    session_session.flush.assert_awaited_once_with()
    session_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_auth_session_repository_looks_up_by_digest_only() -> None:
    digest = "a" * 64
    session = Mock()
    session.scalar = AsyncMock(return_value=None)
    repository = AuthSessionRepository(session)

    assert await repository.get_by_token_hash(digest) is None
    statement = session.scalar.await_args.args[0]
    assert statement.compile().params["token_hash_1"] == digest

    revoked = Mock()
    revoked.flush = AsyncMock()
    revoked_session = AuthSession(
        user_id=UUID("11111111-1111-4111-8111-111111111111"),
        membership_id=UUID("22222222-2222-4222-8222-222222222222"),
        token_hash=digest,
        expires_at=utc_now(),
    )
    revoke_repository = AuthSessionRepository(revoked)
    now = utc_now()
    assert await revoke_repository.revoke(revoked_session, now) is revoked_session
    assert revoked_session.revoked_at == now
    revoked.flush.assert_awaited_once_with()
    revoked.commit.assert_not_called()


@pytest.mark.asyncio
async def test_repository_email_coercion_reuses_the_identity_contract() -> None:
    session = Mock()
    session.scalar = AsyncMock(return_value=None)
    repository = UserRepository(session)

    assert await repository.get_by_email("  Mixed@Example.COM ") is None
    statement = session.scalar.await_args.args[0]
    assert statement.compile().params["normalized_email_1"] == "mixed@example.com"

    with pytest.raises(ValueError, match="must not be blank"):
        await repository.get_by_email("   ")


@pytest.mark.asyncio
async def test_membership_repository_queries_always_name_their_scope() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    organization_id = UUID("22222222-2222-4222-8222-222222222222")

    scoped_session = Mock()
    scoped_session.scalar = AsyncMock(return_value=None)
    scoped_repository = MembershipRepository(scoped_session)

    assert await scoped_repository.get_for_user_in_organization(user_id, organization_id) is None
    scoped_params = set(scoped_session.scalar.await_args.args[0].compile().params)
    assert scoped_params == {"user_id_1", "organization_id_1"}

    tenant_session = Mock()
    tenant_session.scalars = AsyncMock(return_value=[])
    tenant_repository = MembershipRepository(tenant_session)

    assert await tenant_repository.list_for_organization(organization_id) == []
    # The organization-scoped query never filters on user identity.
    tenant_params = set(tenant_session.scalars.await_args.args[0].compile().params)
    assert tenant_params == {"organization_id_1"}


@pytest.mark.asyncio
async def test_eligible_membership_query_is_scoped_in_the_database() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")
    session = Mock()
    session.scalars = AsyncMock(return_value=[])
    repository = MembershipRepository(session)

    assert await repository.list_eligible_for_user(user_id) == []

    statement = session.scalars.await_args.args[0]
    compiled = statement.compile(compile_kwargs={"literal_binds": True})
    sql = str(compiled).casefold()
    # User scope, active membership, and an active joined organization are all
    # enforced by PostgreSQL rather than by a Python-side filter.
    assert "join taskpilot.organizations" in sql
    assert "taskpilot.memberships.user_id" in sql
    assert "taskpilot.memberships.is_active" in sql
    assert "taskpilot.organizations.is_active" in sql
    assert set(statement.compile().params) == {"user_id_1"}


@pytest.mark.asyncio
async def test_principal_tenant_organization_lookup_is_scoped_in_the_database() -> None:
    organization_id = UUID("22222222-2222-4222-8222-222222222222")
    session = Mock()
    session.scalar = AsyncMock(return_value=None)
    repository = OrganizationRepository(session)

    assert await repository.get_in_principal_tenant(organization_id, organization_id) is None

    statement = session.scalar.await_args.args[0]
    sql = str(statement.compile(compile_kwargs={"literal_binds": True})).casefold()
    # The tenant predicate is part of the query: a foreign row is never fetched
    # and then compared in Python.
    assert sql.count("taskpilot.organizations.id") >= 2
    assert "where" in sql
