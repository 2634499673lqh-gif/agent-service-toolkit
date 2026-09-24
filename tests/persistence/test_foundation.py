import ast
import asyncio
import json
import sys
import traceback
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Index, UniqueConstraint

from core.settings import Settings
from persistence.base import Base
from persistence.engine import normalize_business_database_url
from persistence.identity import canonicalize_email
from persistence.migration_filters import include_name
from persistence.models import (
    APPROVAL_JSON_MAX_BYTES,
    Approval,
    ApprovalActionState,
    ApprovalRiskLevel,
    ApprovalStatus,
    AuthSession,
    Membership,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    User,
    utc_now,
)
from persistence.repositories import (
    ApprovalRepository,
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    TaskRepository,
    TaskRunRepository,
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
        "taskpilot.task_runs",
        "taskpilot.approvals",
    }
    assert Organization.__table__.schema == "taskpilot"
    assert User.__table__.schema == "taskpilot"
    assert Membership.__table__.schema == "taskpilot"
    assert AuthSession.__table__.schema == "taskpilot"
    assert Task.__table__.schema == "taskpilot"
    assert TaskRun.__table__.schema == "taskpilot"
    assert Approval.__table__.schema == "taskpilot"


def test_approval_metadata_declares_frozen_identity_and_integrity_contract() -> None:
    table = Approval.__table__
    assert set(table.c.keys()) == {
        "id",
        "task_run_id",
        "replan_count",
        "step_position",
        "action_name",
        "action_version",
        "proposed_action",
        "risk_level",
        "requester_membership_id",
        "status",
        "decider_membership_id",
        "decided_at",
        "decision_reason",
        "created_at",
        "updated_at",
        "action_state",
        "outcome",
        "action_finished_at",
    }
    assert table.c.id.type.python_type is UUID
    assert table.c.task_run_id.type.python_type is UUID
    assert table.c.replan_count.nullable is False
    assert table.c.step_position.nullable is False
    assert table.c.proposed_action.nullable is False
    assert table.c.decider_membership_id.nullable is True
    assert table.c.decided_at.type.timezone is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert table.c.action_finished_at.type.timezone is True
    assert table.c.status.server_default is not None
    assert table.c.action_state.server_default is not None
    assert {constraint.name for constraint in table.constraints if constraint.name} == {
        "ck_approvals_approval_replan_count_range",
        "ck_approvals_approval_step_position_nonnegative",
        "ck_approvals_approval_action_name_not_blank",
        "ck_approvals_approval_action_version_not_blank",
        "ck_approvals_approval_action_identity_length",
        "ck_approvals_approval_risk_level_valid",
        "ck_approvals_approval_status_valid",
        "ck_approvals_approval_action_state_valid",
        "ck_approvals_approval_decision_reason_length",
        "ck_approvals_approval_proposed_action_object",
        "ck_approvals_approval_outcome_object",
        "ck_approvals_approval_decision_fields_consistent",
        "ck_approvals_approval_action_outcome_consistent",
        "pk_approvals",
        "uq_approvals_run_replan_step",
        "fk_approvals_task_run_id_task_runs",
        "fk_approvals_requester_membership_id_memberships",
        "fk_approvals_decider_membership_id_memberships",
    }
    unique_constraints = {
        (constraint.name, tuple(constraint.columns.keys()))
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert unique_constraints == {
        ("uq_approvals_run_replan_step", ("task_run_id", "replan_count", "step_position"))
    }
    foreign_keys = {fk.parent.name: fk for fk in table.foreign_keys}
    assert set(foreign_keys) == {
        "task_run_id",
        "requester_membership_id",
        "decider_membership_id",
    }
    assert foreign_keys["task_run_id"].target_fullname == "taskpilot.task_runs.id"
    assert foreign_keys["requester_membership_id"].target_fullname == "taskpilot.memberships.id"
    assert foreign_keys["decider_membership_id"].target_fullname == "taskpilot.memberships.id"
    assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in foreign_keys.values())
    assert {index.name for index in table.indexes} == {"ix_approvals_task_run_id_status"}
    index = next(iter(table.indexes))
    assert tuple(column.name for column in index.columns) == ("task_run_id", "status")


def test_approval_model_defaults_and_canonical_json_bounds() -> None:
    approval = Approval(
        task_run_id=UUID("11111111-1111-4111-8111-111111111111"),
        replan_count=0,
        step_position=0,
        action_name="send_report",
        action_version="1",
        proposed_action={"subject": "Résumé"},
        requester_membership_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    assert approval.risk_level is ApprovalRiskLevel.L2
    assert approval.status is ApprovalStatus.PENDING
    assert approval.action_state is ApprovalActionState.AVAILABLE
    assert approval.id is None
    assert repr(approval).find("Résumé") == -1

    at_bound = {"value": "x" * (APPROVAL_JSON_MAX_BYTES - len('{"value":""}'))}
    bounded = Approval(
        task_run_id=UUID("11111111-1111-4111-8111-111111111111"),
        replan_count=0,
        step_position=0,
        action_name="send_report",
        action_version="1",
        proposed_action=at_bound,
        requester_membership_id=UUID("22222222-2222-4222-8222-222222222222"),
    )
    assert (
        len(
            json.dumps(
                bounded.proposed_action,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        )
        == APPROVAL_JSON_MAX_BYTES
    )

    with pytest.raises(ValueError, match="UTF-8 bytes"):
        Approval(
            task_run_id=UUID("11111111-1111-4111-8111-111111111111"),
            replan_count=0,
            step_position=0,
            action_name="send_report",
            action_version="1",
            proposed_action={"value": "é" * (APPROVAL_JSON_MAX_BYTES // 2)},
            requester_membership_id=UUID("22222222-2222-4222-8222-222222222222"),
        )
    with pytest.raises(ValueError, match="JSON object"):
        Approval(
            task_run_id=UUID("11111111-1111-4111-8111-111111111111"),
            replan_count=0,
            step_position=0,
            action_name="send_report",
            action_version="1",
            proposed_action={"value": float("nan")},
            requester_membership_id=UUID("22222222-2222-4222-8222-222222222222"),
        )
    cyclic_value: list[object] = []
    cyclic_value.append(cyclic_value)
    with pytest.raises(ValueError, match="JSON object"):
        Approval(
            task_run_id=UUID("11111111-1111-4111-8111-111111111111"),
            replan_count=0,
            step_position=0,
            action_name="send_report",
            action_version="1",
            proposed_action={"cycle": cyclic_value},
            requester_membership_id=UUID("22222222-2222-4222-8222-222222222222"),
        )
    with pytest.raises(ValueError, match="UTF-8 bytes"):
        approval.outcome = {"value": "é" * (APPROVAL_JSON_MAX_BYTES // 2)}


def test_task_run_status_defaults_and_ordering_contract() -> None:
    assert [status.value for status in TaskRunStatus] == [
        "pending",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    ]
    task_run = TaskRun(
        task_id=UUID("11111111-1111-4111-8111-111111111111"),
        run_number=1,
    )
    assert task_run.status is TaskRunStatus.PENDING
    assert task_run.run_number == 1
    assert task_run.id is None
    assert task_run.created_at is None
    assert task_run.updated_at is None


def test_task_run_metadata_declares_task_fk_and_active_run_index() -> None:
    table = TaskRun.__table__
    assert table.c.id.type.python_type is UUID
    assert table.c.task_id.nullable is False
    assert table.c.run_number.nullable is False
    assert table.c.status.nullable is False
    assert table.c.status.server_default is not None
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert {constraint.name for constraint in table.constraints if constraint.name} == {
        "ck_task_runs_task_run_number_positive",
        "ck_task_runs_task_run_status_valid",
        "pk_task_runs",
        "uq_task_runs_task_run_number",
        "fk_task_runs_task_id_tasks",
    }
    unique_columns = {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("task_id", "run_number") in unique_columns
    foreign_keys = {fk.parent.name: fk for fk in table.foreign_keys}
    assert set(foreign_keys) == {"task_id"}
    assert foreign_keys["task_id"].target_fullname == "taskpilot.tasks.id"
    assert foreign_keys["task_id"].ondelete == "RESTRICT"
    indexes = {index.name: index for index in table.indexes if isinstance(index, Index)}
    assert set(indexes) == {
        "ix_task_runs_task_id",
        "ix_task_runs_status",
        "uq_task_runs_one_active_per_task",
    }
    assert indexes["uq_task_runs_one_active_per_task"].unique is True
    assert (
        str(indexes["uq_task_runs_one_active_per_task"].dialect_options["postgresql"]["where"])
        == "status IN ('pending', 'running')"
    )


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

    task_session = Mock()
    task_session.flush = AsyncMock()
    task_repository = TaskRepository(task_session)
    task = Task(
        organization_id=UUID("33333333-3333-4333-8333-333333333333"),
        created_by_user_id=UUID("44444444-4444-4444-8444-444444444444"),
        title="Repository task",
    )

    assert await task_repository.add(task) is task
    task_session.add.assert_called_once_with(task)
    task_session.flush.assert_awaited_once_with()
    task_session.commit.assert_not_called()
    task_session.rollback.assert_not_called()
    task_session.close.assert_not_called()

    task_run_session = Mock()
    task_run_session.flush = AsyncMock()
    task_run_repository = TaskRunRepository(task_run_session)
    task_run = TaskRun(
        task_id=task.id or UUID("55555555-5555-4555-8555-555555555555"),
        run_number=1,
    )

    assert await task_run_repository.add(task_run) is task_run
    task_run_session.add.assert_called_once_with(task_run)
    task_run_session.flush.assert_awaited_once_with()
    task_run_session.commit.assert_not_called()
    task_run_session.rollback.assert_not_called()
    task_run_session.close.assert_not_called()

    approval_session = Mock()
    approval_session.flush = AsyncMock()
    approval_repository = ApprovalRepository(approval_session)
    approval = Approval(
        task_run_id=UUID("66666666-6666-4666-8666-666666666666"),
        replan_count=0,
        step_position=0,
        action_name="send_report",
        action_version="1",
        proposed_action={"recipient": "person@example.com"},
        requester_membership_id=UUID("77777777-7777-4777-8777-777777777777"),
    )

    assert await approval_repository.add(approval) is approval
    approval_session.add.assert_called_once_with(approval)
    approval_session.flush.assert_awaited_once_with()
    approval_session.commit.assert_not_called()
    approval_session.rollback.assert_not_called()
    approval_session.close.assert_not_called()


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


@pytest.mark.asyncio
async def test_task_repositories_keep_tenant_scope_in_the_database() -> None:
    task_id = UUID("55555555-5555-4555-8555-555555555555")
    task_run_id = UUID("66666666-6666-4666-8666-666666666666")
    organization_id = UUID("77777777-7777-4777-8777-777777777777")

    task_session = Mock()
    task_session.scalar = AsyncMock(return_value=None)
    task_session.scalars = AsyncMock(return_value=[])
    task_repository = TaskRepository(task_session)

    assert await task_repository.get_in_principal_tenant(task_id, organization_id) is None
    task_statement = task_session.scalar.await_args.args[0]
    task_sql = str(task_statement.compile(compile_kwargs={"literal_binds": True})).casefold()
    assert "taskpilot.tasks.id" in task_sql
    assert "taskpilot.tasks.organization_id" in task_sql

    assert await task_repository.list_for_organization(organization_id) == []
    list_sql = str(
        task_session.scalars.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "taskpilot.tasks.organization_id" in list_sql

    task_run_session = Mock()
    task_run_session.scalar = AsyncMock(return_value=None)
    task_run_session.scalars = AsyncMock(return_value=[])
    task_run_repository = TaskRunRepository(task_run_session)

    assert await task_run_repository.get_in_principal_tenant(task_run_id, organization_id) is None
    task_run_statement = task_run_session.scalar.await_args.args[0]
    task_run_sql = str(
        task_run_statement.compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "join taskpilot.tasks" in task_run_sql
    assert "taskpilot.task_runs.id" in task_run_sql
    assert "taskpilot.tasks.organization_id" in task_run_sql

    assert await task_run_repository.list_for_task_in_organization(task_id, organization_id) == []
    task_run_list_sql = str(
        task_run_session.scalars.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "join taskpilot.tasks" in task_run_list_sql
    assert "taskpilot.tasks.organization_id" in task_run_list_sql


@pytest.mark.asyncio
async def test_approval_repository_reads_join_the_normalized_tenant_path() -> None:
    task_id = UUID("11111111-1111-4111-8111-111111111111")
    task_run_id = UUID("22222222-2222-4222-8222-222222222222")
    approval_id = UUID("33333333-3333-4333-8333-333333333333")
    organization_id = UUID("44444444-4444-4444-8444-444444444444")

    session = Mock()
    session.scalar = AsyncMock(return_value=None)
    session.scalars = AsyncMock(return_value=[])
    repository = ApprovalRepository(session)

    assert (
        await repository.get_for_task_run_in_principal_tenant(
            task_id, task_run_id, approval_id, organization_id
        )
        is None
    )
    by_id_sql = str(
        session.scalar.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "join taskpilot.task_runs" in by_id_sql
    assert "join taskpilot.tasks" in by_id_sql
    assert "taskpilot.task_runs.task_id" in by_id_sql
    assert "taskpilot.tasks.organization_id" in by_id_sql
    assert task_id.hex in by_id_sql
    assert task_run_id.hex in by_id_sql
    assert approval_id.hex in by_id_sql
    assert organization_id.hex in by_id_sql

    assert (
        await repository.get_for_action_identity_in_principal_tenant(
            task_id, task_run_id, 1, 4, organization_id
        )
        is None
    )
    by_identity_sql = str(
        session.scalar.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "join taskpilot.task_runs" in by_identity_sql
    assert "join taskpilot.tasks" in by_identity_sql
    assert "taskpilot.approvals.replan_count = 1" in by_identity_sql
    assert "taskpilot.approvals.step_position = 4" in by_identity_sql
    assert "taskpilot.tasks.organization_id" in by_identity_sql

    assert (
        await repository.list_for_task_run_in_principal_tenant(
            task_id, task_run_id, organization_id
        )
        == []
    )
    list_sql = str(
        session.scalars.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    ).casefold()
    assert "join taskpilot.task_runs" in list_sql
    assert "join taskpilot.tasks" in list_sql
    assert "taskpilot.tasks.organization_id" in list_sql
    assert "taskpilot.task_runs.id" in list_sql
    assert "taskpilot.task_runs.task_id" in list_sql
