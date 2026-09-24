"""Disposable PostgreSQL verification for TaskPilot migration coexistence."""

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

try:
    from alembic import command
    from alembic.config import Config
except ModuleNotFoundError:
    pytest.skip("HOST_ENVIRONMENT: Alembic dependency is not installed", allow_module_level=True)

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from psycopg import AsyncConnection, errors, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import delete, insert, inspect, make_url, select, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from persistence.engine import create_async_engine, create_session_factory, get_business_session
from persistence.models import (
    AgentRun,
    AgentRunStatus,
    Approval,
    ApprovalActionState,
    ApprovalStatus,
    AuthSession,
    Membership,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    ToolCall,
    ToolCallStatus,
    User,
)
from persistence.passwords import hash_password, verify_password
from persistence.repositories import (
    AgentRunRepository,
    ApprovalRepository,
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    TaskRepository,
    TaskRunRepository,
    ToolCallRepository,
    UserRepository,
)
from persistence.tokens import generate_token, hash_token
from service import bootstrap_cli
from service.bootstrap import (
    BootstrapError,
    BootstrapOutcome,
    bootstrap_owner,
)
from service.logging import configure_logging
from service.session import (
    ORGANIZATION_SELECTION_REQUIRED,
    SESSION_TTL,
    AuthenticatedSession,
    AuthService,
    LoginError,
    OrganizationSelectionRequired,
)

pytestmark = pytest.mark.postgres
T021_REVISION = "t021_organization"
T022_REVISION = "t022_user"
T022A_REVISION = "t022a_membership"
T023_REVISION = "t023_auth_session"
T031_REVISION = "t031_task"
T032_REVISION = "t032_task_run"
T033_REVISION = "t033_approval"
T034_REVISION = "t034_observability"
EXPECTED_REVISION = T034_REVISION


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


@asynccontextmanager
async def disposable_database(base_url: str) -> AsyncIterator[str]:
    """Create and destroy one uniquely named database; never alter the base DB."""

    base = make_url(base_url)
    base_name = base.database or ""
    if "test" not in base_name.casefold():
        raise RuntimeError("Refusing database lifecycle operation outside a test database")
    database_name = f"taskpilot_t022a_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    isolated_url = base.set(database=database_name).render_as_string(hide_password=False)
    created = False
    try:
        async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
            try:
                await connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )
            except errors.InsufficientPrivilege as exc:
                raise RuntimeError(
                    "TASKPILOT_TEST_DATABASE_URL role must have CREATE DATABASE privilege; "
                    "refusing to fall back to a shared database"
                ) from exc
        created = True
        yield isolated_url
    finally:
        if created:
            async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
                await connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )


def _alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


async def _run_alembic(config: Config, operation: str, revision: str) -> None:
    await asyncio.to_thread(getattr(command, operation), config, revision)


async def _langgraph_roundtrip(database_url: str, key: str) -> None:
    psycopg_url = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    async with AsyncConnectionPool(
        psycopg_url,
        min_size=1,
        max_size=1,
        kwargs={"autocommit": True, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        await AsyncPostgresSaver(pool).setup()  # type: ignore[bad-argument-type]
        store = AsyncPostgresStore(pool)
        await store.setup()
        await store.aput(("t021",), key, {"value": key}, index=False)
        item = await store.aget(("t021",), key)
        assert item is not None
        assert item.value == {"value": key}


async def _langgraph_read(database_url: str, key: str) -> None:
    psycopg_url = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    async with AsyncConnectionPool(
        psycopg_url,
        min_size=1,
        max_size=1,
        kwargs={"autocommit": True, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        item = await AsyncPostgresStore(pool).aget(("t021",), key)
        assert item is not None
        assert item.value == {"value": key}


async def _assert_taskpilot_schema(
    engine: AsyncEngine,
    *,
    expected_revision: str = EXPECTED_REVISION,
    users_expected: bool = True,
    memberships_expected: bool = True,
    sessions_expected: bool = True,
    approvals_expected: bool | None = None,
    observability_expected: bool | None = None,
    expected_tables: set[str] | None = None,
) -> None:
    if approvals_expected is None:
        approvals_expected = expected_revision in {T033_REVISION, T034_REVISION}
    if observability_expected is None:
        observability_expected = expected_revision == T034_REVISION
    async with engine.connect() as connection:
        schemas = set(await connection.run_sync(lambda conn: inspect(conn).get_schema_names()))
        tables = set(
            await connection.run_sync(
                lambda conn: inspect(conn).get_table_names(schema="taskpilot")
            )
        )
        revision = await connection.scalar(
            text("SELECT version_num FROM taskpilot.alembic_version")
        )
        columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda conn: inspect(conn).get_columns("organizations", schema="taskpilot")
            )
        }
        # The users relation is absent after a T022 -> T021 downgrade, so only
        # reflect it when this revision is expected to own it.
        user_columns = (
            {
                column["name"]: column
                for column in await connection.run_sync(
                    lambda conn: inspect(conn).get_columns("users", schema="taskpilot")
                )
            }
            if users_expected
            else {}
        )
        version_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.alembic_version"},
        )
        organizations_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.organizations"},
        )
        users_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.users"},
        )
        checks = await connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("organizations", schema="taskpilot")
        )
        user_checks = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        user_unique_constraints = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_unique_constraints("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        user_indexes = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        # The memberships relation only exists from the T022A revision onward.
        membership_columns = (
            {
                column["name"]: column
                for column in await connection.run_sync(
                    lambda conn: inspect(conn).get_columns("memberships", schema="taskpilot")
                )
            }
            if memberships_expected
            else {}
        )
        memberships_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.memberships"},
        )
        membership_checks = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_unique_constraints = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_unique_constraints("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_indexes = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_foreign_keys = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        session_columns = (
            {
                column["name"]: column
                for column in await connection.run_sync(
                    lambda conn: inspect(conn).get_columns("auth_sessions", schema="taskpilot")
                )
            }
            if sessions_expected
            else {}
        )
        sessions_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.auth_sessions"},
        )
        session_unique_constraints = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_unique_constraints(
                    "auth_sessions", schema="taskpilot"
                )
            )
            if sessions_expected
            else []
        )
        session_indexes = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("auth_sessions", schema="taskpilot")
            )
            if sessions_expected
            else []
        )
        session_foreign_keys = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("auth_sessions", schema="taskpilot")
            )
            if sessions_expected
            else []
        )
    assert "taskpilot" in schemas
    if expected_tables is None:
        expected_tables = {"alembic_version", "organizations"}
        if users_expected:
            expected_tables.add("users")
        if memberships_expected:
            expected_tables.add("memberships")
        if sessions_expected:
            expected_tables.add("auth_sessions")
        expected_tables.add("tasks")
        if expected_revision in {T032_REVISION, T033_REVISION, T034_REVISION}:
            expected_tables.add("task_runs")
        if approvals_expected:
            expected_tables.add("approvals")
        if observability_expected:
            expected_tables.update({"agent_runs", "tool_calls"})
    assert tables == expected_tables
    assert revision == expected_revision
    assert version_relation == "taskpilot.alembic_version"
    assert organizations_relation == "taskpilot.organizations"
    assert set(columns) == {"id", "name", "is_active", "created_at", "updated_at"}
    assert str(columns["id"]["type"]) == "UUID"
    assert columns["created_at"]["type"].timezone is True
    assert columns["updated_at"]["type"].timezone is True
    assert {constraint["name"] for constraint in checks} == {
        "ck_organizations_organization_name_not_blank"
    }
    if users_expected:
        assert users_relation == "taskpilot.users"
        assert set(user_columns) == {
            "id",
            "email",
            "normalized_email",
            "password_hash",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert str(user_columns["id"]["type"]) == "UUID"
        assert user_columns["created_at"]["type"].timezone is True
        assert user_columns["updated_at"]["type"].timezone is True
        assert {constraint["name"] for constraint in user_checks} == {
            "ck_users_user_email_not_blank"
        }
        assert {constraint["name"] for constraint in user_unique_constraints} == {
            "uq_users_normalized_email"
        }
        # PostgreSQL backs the UNIQUE(normalized_email) constraint with a
        # same-named index, so assert the explicit index is present instead of
        # requiring exact set equality.
        assert "ix_taskpilot_users_is_active" in {index["name"] for index in user_indexes}
    else:
        assert users_relation is None
    if memberships_expected:
        assert memberships_relation == "taskpilot.memberships"
        assert set(membership_columns) == {
            "id",
            "user_id",
            "organization_id",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert str(membership_columns["id"]["type"]) == "UUID"
        assert str(membership_columns["user_id"]["type"]) == "UUID"
        assert str(membership_columns["organization_id"]["type"]) == "UUID"
        assert all(
            membership_columns[column]["nullable"] is False
            for column in ("user_id", "organization_id", "role", "is_active")
        )
        assert membership_columns["created_at"]["type"].timezone is True
        assert membership_columns["updated_at"]["type"].timezone is True
        assert {constraint["name"] for constraint in membership_checks} == {
            "ck_memberships_membership_role_valid"
        }
        assert {constraint["name"] for constraint in membership_unique_constraints} == {
            "uq_memberships_user_organization"
        }
        assert {
            tuple(constraint["column_names"]) for constraint in membership_unique_constraints
        } == {("user_id", "organization_id")}
        membership_index_names = {index["name"] for index in membership_indexes}
        assert "ix_memberships_user_id" in membership_index_names
        assert "ix_memberships_organization_id" in membership_index_names
        foreign_keys = {tuple(fk["constrained_columns"]): fk for fk in membership_foreign_keys}
        assert set(foreign_keys) == {("user_id",), ("organization_id",)}
        assert foreign_keys[("user_id",)]["referred_schema"] == "taskpilot"
        assert foreign_keys[("user_id",)]["referred_table"] == "users"
        assert foreign_keys[("user_id",)]["options"].get("ondelete") == "RESTRICT"
        assert foreign_keys[("organization_id",)]["referred_schema"] == "taskpilot"
        assert foreign_keys[("organization_id",)]["referred_table"] == "organizations"
        assert foreign_keys[("organization_id",)]["options"].get("ondelete") == "RESTRICT"
    else:
        assert memberships_relation is None
    if sessions_expected:
        assert sessions_relation == "taskpilot.auth_sessions"
        assert set(session_columns) == {
            "id",
            "user_id",
            "membership_id",
            "token_hash",
            "expires_at",
            "revoked_at",
            "created_at",
            "updated_at",
        }
        assert str(session_columns["id"]["type"]) == "UUID"
        assert str(session_columns["user_id"]["type"]) == "UUID"
        assert str(session_columns["membership_id"]["type"]) == "UUID"
        assert str(session_columns["token_hash"]["type"]) == "VARCHAR(64)"
        assert session_columns["token_hash"]["nullable"] is False
        assert session_columns["expires_at"]["nullable"] is False
        assert session_columns["revoked_at"]["nullable"] is True
        assert session_columns["expires_at"]["type"].timezone is True
        assert session_columns["created_at"]["type"].timezone is True
        assert session_columns["updated_at"]["type"].timezone is True
        assert {constraint["name"] for constraint in session_unique_constraints} == {
            "uq_auth_sessions_token_hash"
        }
        session_index_names = {index["name"] for index in session_indexes}
        assert {
            "ix_auth_sessions_user_id",
            "ix_auth_sessions_membership_id",
            "ix_auth_sessions_expires_at",
        } <= session_index_names
        session_foreign_keys_by_column = {
            tuple(fk["constrained_columns"]): fk for fk in session_foreign_keys
        }
        assert set(session_foreign_keys_by_column) == {("user_id",), ("membership_id",)}
        assert session_foreign_keys_by_column[("user_id",)]["referred_table"] == "users"
        assert session_foreign_keys_by_column[("user_id",)]["options"].get("ondelete") == "RESTRICT"
        assert session_foreign_keys_by_column[("membership_id",)]["referred_table"] == "memberships"
        assert (
            session_foreign_keys_by_column[("membership_id",)]["options"].get("ondelete")
            == "RESTRICT"
        )
    else:
        assert sessions_relation is None

    task_connection = await engine.connect()
    tasks_relation = await task_connection.scalar(
        text("SELECT to_regclass(:qualified_name)"),
        {"qualified_name": "taskpilot.tasks"},
    )
    if expected_revision in {T031_REVISION, T032_REVISION, T033_REVISION, T034_REVISION}:
        assert tasks_relation == "taskpilot.tasks"
        task_columns = {
            column["name"]: column
            for column in await task_connection.run_sync(
                lambda conn: inspect(conn).get_columns("tasks", schema="taskpilot")
            )
        }
        assert set(task_columns) == {
            "id",
            "organization_id",
            "created_by_user_id",
            "title",
            "description",
            "status",
            "created_at",
            "updated_at",
        }
        assert str(task_columns["id"]["type"]) == "UUID"
        assert str(task_columns["organization_id"]["type"]) == "UUID"
        assert str(task_columns["created_by_user_id"]["type"]) == "UUID"
        assert task_columns["description"]["nullable"] is True
        assert task_columns["status"]["nullable"] is False
        assert task_columns["created_at"]["type"].timezone is True
        assert task_columns["updated_at"]["type"].timezone is True
        task_checks = await task_connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("tasks", schema="taskpilot")
        )
        assert {constraint["name"] for constraint in task_checks} == {
            "ck_tasks_task_title_not_blank",
            "ck_tasks_task_status_valid",
        }
        task_indexes = await task_connection.run_sync(
            lambda conn: inspect(conn).get_indexes("tasks", schema="taskpilot")
        )
        assert {
            "ix_tasks_organization_id",
            "ix_tasks_created_by_user_id",
            "ix_tasks_status",
        } <= {index["name"] for index in task_indexes}
        task_foreign_keys = await task_connection.run_sync(
            lambda conn: inspect(conn).get_foreign_keys("tasks", schema="taskpilot")
        )
        foreign_keys_by_column = {tuple(fk["constrained_columns"]): fk for fk in task_foreign_keys}
        assert set(foreign_keys_by_column) == {
            ("organization_id",),
            ("created_by_user_id",),
        }
        assert foreign_keys_by_column[("organization_id",)]["referred_table"] == "organizations"
        assert foreign_keys_by_column[("created_by_user_id",)]["referred_table"] == "users"
        assert all(
            fk["options"].get("ondelete") == "RESTRICT" for fk in foreign_keys_by_column.values()
        )
    else:
        assert tasks_relation is None

    task_runs_relation = await task_connection.scalar(
        text("SELECT to_regclass(:qualified_name)"),
        {"qualified_name": "taskpilot.task_runs"},
    )
    if expected_revision in {T032_REVISION, T033_REVISION, T034_REVISION}:
        assert task_runs_relation == "taskpilot.task_runs"
        task_run_columns = {
            column["name"]: column
            for column in await task_connection.run_sync(
                lambda conn: inspect(conn).get_columns("task_runs", schema="taskpilot")
            )
        }
        assert set(task_run_columns) == {
            "id",
            "task_id",
            "run_number",
            "status",
            "created_at",
            "updated_at",
        }
        assert str(task_run_columns["id"]["type"]) == "UUID"
        assert str(task_run_columns["task_id"]["type"]) == "UUID"
        assert str(task_run_columns["run_number"]["type"]) == "INTEGER"
        assert task_run_columns["run_number"]["nullable"] is False
        assert task_run_columns["status"]["nullable"] is False
        assert task_run_columns["created_at"]["type"].timezone is True
        assert task_run_columns["updated_at"]["type"].timezone is True
        task_run_checks = await task_connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("task_runs", schema="taskpilot")
        )
        assert {constraint["name"] for constraint in task_run_checks} == {
            "ck_task_runs_task_run_number_positive",
            "ck_task_runs_task_run_status_valid",
        }
        task_run_unique_constraints = await task_connection.run_sync(
            lambda conn: inspect(conn).get_unique_constraints("task_runs", schema="taskpilot")
        )
        assert {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in task_run_unique_constraints
        } == {("uq_task_runs_task_run_number", ("task_id", "run_number"))}
        task_run_indexes = await task_connection.run_sync(
            lambda conn: inspect(conn).get_indexes("task_runs", schema="taskpilot")
        )
        task_run_index_by_name = {index["name"]: index for index in task_run_indexes}
        assert {
            "ix_task_runs_task_id",
            "ix_task_runs_status",
            "uq_task_runs_one_active_per_task",
        } <= set(task_run_index_by_name)
        assert task_run_index_by_name["uq_task_runs_one_active_per_task"]["unique"] is True
        assert "pending" in task_run_index_by_name["uq_task_runs_one_active_per_task"][
            "dialect_options"
        ].get("postgresql_where", "")
        task_run_foreign_keys = await task_connection.run_sync(
            lambda conn: inspect(conn).get_foreign_keys("task_runs", schema="taskpilot")
        )
        assert len(task_run_foreign_keys) == 1
        assert task_run_foreign_keys[0]["constrained_columns"] == ["task_id"]
        assert task_run_foreign_keys[0]["referred_schema"] == "taskpilot"
        assert task_run_foreign_keys[0]["referred_table"] == "tasks"
        assert task_run_foreign_keys[0]["options"].get("ondelete") == "RESTRICT"
    else:
        assert task_runs_relation is None

    approvals_relation = await task_connection.scalar(
        text("SELECT to_regclass(:qualified_name)"),
        {"qualified_name": "taskpilot.approvals"},
    )
    if approvals_expected:
        assert approvals_relation == "taskpilot.approvals"
        approval_columns = {
            column["name"]: column
            for column in await task_connection.run_sync(
                lambda conn: inspect(conn).get_columns("approvals", schema="taskpilot")
            )
        }
        assert set(approval_columns) == {
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
        assert str(approval_columns["id"]["type"]) == "UUID"
        assert str(approval_columns["task_run_id"]["type"]) == "UUID"
        assert approval_columns["proposed_action"]["nullable"] is False
        assert approval_columns["outcome"]["nullable"] is True
        for column_name in ("decided_at", "created_at", "updated_at", "action_finished_at"):
            assert approval_columns[column_name]["type"].timezone is True
        approval_checks = await task_connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("approvals", schema="taskpilot")
        )
        assert {constraint["name"] for constraint in approval_checks} == {
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
        }
        approval_unique_constraints = await task_connection.run_sync(
            lambda conn: inspect(conn).get_unique_constraints("approvals", schema="taskpilot")
        )
        assert {
            (constraint["name"], tuple(constraint["column_names"]))
            for constraint in approval_unique_constraints
        } == {
            (
                "uq_approvals_run_replan_step",
                ("task_run_id", "replan_count", "step_position"),
            )
        }
        approval_indexes = await task_connection.run_sync(
            lambda conn: inspect(conn).get_indexes("approvals", schema="taskpilot")
        )
        assert {(index["name"], tuple(index["column_names"])) for index in approval_indexes} == {
            ("ix_approvals_task_run_id_status", ("task_run_id", "status")),
            (
                "uq_approvals_run_replan_step",
                ("task_run_id", "replan_count", "step_position"),
            ),
        }
        approval_foreign_keys = await task_connection.run_sync(
            lambda conn: inspect(conn).get_foreign_keys("approvals", schema="taskpilot")
        )
        foreign_keys_by_column = {
            tuple(foreign_key["constrained_columns"]): foreign_key
            for foreign_key in approval_foreign_keys
        }
        assert set(foreign_keys_by_column) == {
            ("task_run_id",),
            ("requester_membership_id",),
            ("decider_membership_id",),
        }
        assert foreign_keys_by_column[("task_run_id",)]["referred_table"] == "task_runs"
        assert foreign_keys_by_column[("requester_membership_id",)]["referred_table"] == (
            "memberships"
        )
        assert foreign_keys_by_column[("decider_membership_id",)]["referred_table"] == (
            "memberships"
        )
        assert all(
            foreign_key["options"].get("ondelete") == "RESTRICT"
            for foreign_key in foreign_keys_by_column.values()
        )
    else:
        assert approvals_relation is None
    await task_connection.close()


@pytest.mark.asyncio
async def test_scenario_a_langgraph_then_taskpilot_and_downgrade() -> None:
    async with disposable_database(_configured_test_url()) as database_url:
        config = _alembic_config(database_url)
        engine = create_async_engine(database_url)
        try:
            await _langgraph_roundtrip(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            await _langgraph_read(database_url, "scenario-a")
            # T034 -> T033 must remove only observability persistence.
            await _run_alembic(config, "downgrade", T033_REVISION)
            await _assert_taskpilot_schema(engine, expected_revision=T033_REVISION)
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            # T033 -> T032 must remove only Approval persistence.
            await _run_alembic(config, "downgrade", T032_REVISION)
            await _assert_taskpilot_schema(engine, expected_revision=T032_REVISION)
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            # T032 -> T031 must remove only the TaskRun table and active-run index.
            await _run_alembic(config, "downgrade", T031_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T031_REVISION,
                expected_tables={
                    "alembic_version",
                    "organizations",
                    "users",
                    "memberships",
                    "auth_sessions",
                    "tasks",
                },
            )
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            # T023 -> T022A must remove only the session table.
            await _run_alembic(config, "downgrade", T022A_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T022A_REVISION,
                sessions_expected=False,
                expected_tables={"alembic_version", "organizations", "users", "memberships"},
            )
            await _langgraph_read(database_url, "scenario-a")
            # T022A -> T022 must remove only the membership table.
            await _run_alembic(config, "downgrade", T022_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T022_REVISION,
                memberships_expected=False,
                sessions_expected=False,
                expected_tables={"alembic_version", "organizations", "users"},
            )
            await _langgraph_read(database_url, "scenario-a")
            # T022 -> T021 must remove only the users table.
            await _run_alembic(config, "downgrade", T021_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T021_REVISION,
                users_expected=False,
                memberships_expected=False,
                sessions_expected=False,
                expected_tables={"alembic_version", "organizations"},
            )
            await _langgraph_read(database_url, "scenario-a")
            # Re-upgrade must restore the whole TaskPilot schema.
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            await _langgraph_read(database_url, "scenario-a")
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def migrated_engine() -> AsyncIterator[AsyncEngine]:
    async with disposable_database(_configured_test_url()) as database_url:
        config = _alembic_config(database_url)
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                schemas = set(
                    await connection.run_sync(lambda conn: inspect(conn).get_schema_names())
                )
                assert "taskpilot" not in schemas
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.organizations"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.users"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.memberships"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.auth_sessions"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.alembic_version"},
                    )
                    is None
                )
            await _run_alembic(config, "upgrade", "head")
            yield engine
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_b_taskpilot_then_langgraph(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Scenario B")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(organization.id) is not None
    await _langgraph_roundtrip(
        migrated_engine.url.render_as_string(hide_password=False), "scenario-b"
    )
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(organization.id) is not None


@pytest.mark.asyncio
async def test_transactions_sessions_and_name_constraints(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    committed = Organization(name="Committed")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(committed)
    async with get_business_session(session_factory) as session:
        loaded = await OrganizationRepository(session).get(committed.id)
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert loaded.updated_at.utcoffset().total_seconds() == 0

    rolled_back_id = uuid4()
    with pytest.raises(RuntimeError, match="force rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await OrganizationRepository(session).add(
                    Organization(id=rolled_back_id, name="Rolled back")
                )
                raise RuntimeError("force rollback")
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(rolled_back_id) is None

    uncommitted_id = uuid4()
    async with get_business_session(session_factory) as session:
        await OrganizationRepository(session).add(
            Organization(id=uncommitted_id, name="Not committed")
        )
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(uncommitted_id) is None

    session_one = session_factory()
    session_two = session_factory()
    try:
        await session_one.begin()
        await OrganizationRepository(session_one).add(Organization(name="Isolated"))
        result = await session_two.scalars(
            select(Organization).where(Organization.name == "Isolated")
        )
        assert result.one_or_none() is None
    finally:
        await session_one.rollback()
        await session_one.close()
        await session_two.close()

    for invalid_name in ("   ", "\t\n"):
        with pytest.raises(Exception):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await OrganizationRepository(session).add(Organization(name=invalid_name))


@pytest.mark.asyncio
async def test_task_persistence_defaults_and_database_constraints(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Task Org")
    user = User(email="task-owner@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            task = Task(
                organization_id=organization.id,
                created_by_user_id=user.id,
                title="Persist a task",
            )
            session.add(task)
            await session.flush()
            assert task.status is TaskStatus.DRAFT
            assert task.created_at.tzinfo is not None
            assert task.updated_at.tzinfo is not None

    async with get_business_session(session_factory) as session:
        loaded = await session.scalar(select(Task).where(Task.id == task.id))
        assert loaded is not None
        assert loaded.status is TaskStatus.DRAFT
        assert loaded.organization_id == organization.id
        assert loaded.created_by_user_id == user.id

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO taskpilot.tasks "
                        "(id, organization_id, created_by_user_id, title, status) "
                        "VALUES (:id, :organization_id, :created_by_user_id, :title, :status)"
                    ),
                    {
                        "id": uuid4(),
                        "organization_id": organization.id,
                        "created_by_user_id": user.id,
                        "title": "Invalid status",
                        "status": "unknown",
                    },
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    delete(Organization).where(Organization.id == organization.id)
                )


@pytest.mark.asyncio
async def test_task_run_persistence_and_active_run_constraints(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="TaskRun Org")
    user = User(email="task-run-owner@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            task = Task(
                organization_id=organization.id,
                created_by_user_id=user.id,
                title="Run a task",
            )
            session.add(task)
            await session.flush()
            pending_run = TaskRun(task_id=task.id, run_number=1)
            succeeded_run = TaskRun(
                task_id=task.id,
                run_number=2,
                status=TaskRunStatus.SUCCEEDED,
            )
            failed_run = TaskRun(
                task_id=task.id,
                run_number=3,
                status=TaskRunStatus.FAILED,
            )
            session.add_all([pending_run, succeeded_run, failed_run])
            await session.flush()
            assert pending_run.status is TaskRunStatus.PENDING
            assert pending_run.created_at.tzinfo is not None
            assert pending_run.updated_at.tzinfo is not None

    async with get_business_session(session_factory) as session:
        stored_runs = list(
            await session.scalars(
                select(TaskRun).where(TaskRun.task_id == task.id).order_by(TaskRun.run_number)
            )
        )
        assert [run.run_number for run in stored_runs] == [1, 2, 3]
        assert [run.status for run in stored_runs] == [
            TaskRunStatus.PENDING,
            TaskRunStatus.SUCCEEDED,
            TaskRunStatus.FAILED,
        ]

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(TaskRun(task_id=task.id, run_number=2))
                await session.flush()

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(TaskRun(task_id=task.id, run_number=0))
                await session.flush()

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(TaskRun(task_id=task.id, run_number=4))
                await session.flush()

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO taskpilot.task_runs "
                        "(id, task_id, run_number, status) "
                        "VALUES (:id, :task_id, :run_number, :status)"
                    ),
                    {
                        "id": uuid4(),
                        "task_id": task.id,
                        "run_number": 5,
                        "status": "unknown",
                    },
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(delete(Task).where(Task.id == task.id))


@pytest.mark.asyncio
async def test_approval_tenant_integrity_constraints_and_rollback(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization_a = Organization(name="Approval Tenant A")
    organization_b = Organization(name="Approval Tenant B")
    user_a = User(email="approval-a@example.com", password_hash="opaque-test-hash")
    user_b = User(email="approval-b@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization_a)
            await OrganizationRepository(session).add(organization_b)
            await UserRepository(session).add(user_a)
            await UserRepository(session).add(user_b)
            member_a = Membership(
                user_id=user_a.id,
                organization_id=organization_a.id,
                role=Role.OWNER,
            )
            member_b = Membership(
                user_id=user_b.id,
                organization_id=organization_b.id,
                role=Role.OWNER,
            )
            await MembershipRepository(session).add(member_a)
            await MembershipRepository(session).add(member_b)
            task_a = Task(
                organization_id=organization_a.id,
                created_by_user_id=user_a.id,
                title="Approval tenant A task",
            )
            task_b = Task(
                organization_id=organization_b.id,
                created_by_user_id=user_b.id,
                title="Approval tenant B task",
            )
            await TaskRepository(session).add(task_a)
            await TaskRepository(session).add(task_b)
            run_a = TaskRun(task_id=task_a.id, run_number=1)
            run_b = TaskRun(task_id=task_b.id, run_number=1)
            await TaskRunRepository(session).add(run_a)
            await TaskRunRepository(session).add(run_b)
            own_approval = Approval(
                task_run_id=run_a.id,
                replan_count=0,
                step_position=0,
                action_name="send_report",
                action_version="1",
                proposed_action={"recipient": "a@example.com"},
                requester_membership_id=member_a.id,
            )
            foreign_approval = Approval(
                task_run_id=run_b.id,
                replan_count=0,
                step_position=0,
                action_name="send_report",
                action_version="1",
                proposed_action={"recipient": "b@example.com"},
                requester_membership_id=member_b.id,
            )
            await ApprovalRepository(session).add(own_approval)
            await ApprovalRepository(session).add(foreign_approval)

    async with get_business_session(session_factory) as session:
        repository = ApprovalRepository(session)
        assert (
            await repository.get_for_task_run_in_principal_tenant(
                task_a.id, run_a.id, own_approval.id, organization_a.id
            )
        ) is not None
        assert (
            await repository.get_for_task_run_in_principal_tenant(
                task_b.id, run_b.id, foreign_approval.id, organization_a.id
            )
            is None
        )
        assert (
            await repository.get_for_task_run_in_principal_tenant(
                task_a.id, run_b.id, foreign_approval.id, organization_a.id
            )
            is None
        )
        assert (
            await repository.get_for_action_identity_in_principal_tenant(
                task_a.id, run_a.id, 0, 0, organization_a.id
            )
        ) is not None
        assert [
            approval.id
            for approval in await repository.list_for_task_run_in_principal_tenant(
                task_a.id, run_a.id, organization_a.id
            )
        ] == [own_approval.id]

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(
                    Approval(
                        task_run_id=uuid4(),
                        replan_count=0,
                        step_position=1,
                        action_name="send_report",
                        action_version="1",
                        proposed_action={},
                        requester_membership_id=member_a.id,
                    )
                )
                await session.flush()
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(
                    Approval(
                        task_run_id=run_a.id,
                        replan_count=0,
                        step_position=1,
                        action_name="send_report",
                        action_version="1",
                        proposed_action={},
                        requester_membership_id=uuid4(),
                    )
                )
                await session.flush()
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await ApprovalRepository(session).add(
                    Approval(
                        task_run_id=run_a.id,
                        replan_count=0,
                        step_position=0,
                        action_name="send_report",
                        action_version="1",
                        proposed_action={},
                        requester_membership_id=member_a.id,
                    )
                )
    for invalid_replan_count, invalid_step_position in ((2, 1), (1, -1)):
        with pytest.raises(IntegrityError):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    session.add(
                        Approval(
                            task_run_id=run_a.id,
                            replan_count=invalid_replan_count,
                            step_position=invalid_step_position,
                            action_name="send_report",
                            action_version="1",
                            proposed_action={},
                            requester_membership_id=member_a.id,
                        )
                    )
                    await session.flush()
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(
                    Approval(
                        task_run_id=run_a.id,
                        replan_count=1,
                        step_position=1,
                        action_name="send_report",
                        action_version="1",
                        proposed_action={},
                        requester_membership_id=member_a.id,
                        status=ApprovalStatus.APPROVED,
                    )
                )
                await session.flush()

    invalid_enum_insert = text(
        "INSERT INTO taskpilot.approvals "
        "(id, task_run_id, replan_count, step_position, action_name, action_version, "
        "proposed_action, risk_level, requester_membership_id, status, action_state) "
        "VALUES (:id, :task_run_id, 1, 2, 'send_report', '1', CAST('{}' AS jsonb), "
        ":risk_level, :requester_membership_id, :status, :action_state)"
    )
    for invalid_risk, status, action_state in (
        ("L1", "pending", "available"),
        ("L3", "pending", "available"),
        ("L2", "decided", "available"),
        ("L2", "pending", "claimed"),
    ):
        with pytest.raises(IntegrityError):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await session.execute(
                        invalid_enum_insert,
                        {
                            "id": uuid4(),
                            "task_run_id": run_a.id,
                            "risk_level": invalid_risk,
                            "requester_membership_id": member_a.id,
                            "status": status,
                            "action_state": action_state,
                        },
                    )
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO taskpilot.approvals "
                        "(id, task_run_id, replan_count, step_position, action_name, "
                        "action_version, proposed_action, risk_level, requester_membership_id) "
                        "VALUES (:id, :task_run_id, 1, 3, 'send_report', '1', "
                        "CAST('[]' AS jsonb), 'L2', :requester_membership_id)"
                    ),
                    {
                        "id": uuid4(),
                        "task_run_id": run_a.id,
                        "requester_membership_id": member_a.id,
                    },
                )

    valid_finished = Approval(
        task_run_id=run_a.id,
        replan_count=1,
        step_position=4,
        action_name="send_report",
        action_version="1",
        proposed_action={"recipient": "a@example.com"},
        requester_membership_id=member_a.id,
        status=ApprovalStatus.APPROVED,
        decider_membership_id=member_a.id,
        decided_at=datetime.now(UTC),
        action_state=ApprovalActionState.COMPLETED,
        outcome={"step_position": 5, "success": True, "output": "done"},
        action_finished_at=datetime.now(UTC),
    )
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await ApprovalRepository(session).add(valid_finished)

    valid_failure = Approval(
        task_run_id=run_a.id,
        replan_count=1,
        step_position=7,
        action_name="send_report",
        action_version="1",
        proposed_action={"recipient": "a@example.com"},
        requester_membership_id=member_a.id,
        status=ApprovalStatus.APPROVED,
        decider_membership_id=member_a.id,
        decided_at=datetime.now(UTC),
        action_state=ApprovalActionState.FAILED,
        outcome={"step_position": 8, "success": False, "error_code": "failed"},
        action_finished_at=datetime.now(UTC),
    )
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await ApprovalRepository(session).add(valid_failure)

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                session.add(
                    Approval(
                        task_run_id=run_a.id,
                        replan_count=1,
                        step_position=5,
                        action_name="send_report",
                        action_version="1",
                        proposed_action={},
                        requester_membership_id=member_a.id,
                        status=ApprovalStatus.APPROVED,
                        decider_membership_id=member_a.id,
                        decided_at=datetime.now(UTC),
                        action_state=ApprovalActionState.COMPLETED,
                        outcome={"step_position": 7, "success": True, "output": "done"},
                        action_finished_at=datetime.now(UTC),
                    )
                )
                await session.flush()

    for statement in (
        delete(TaskRun).where(TaskRun.id == run_a.id),
        delete(Membership).where(Membership.id == member_a.id),
    ):
        with pytest.raises(IntegrityError):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await session.execute(statement)

    rollback_id = uuid4()
    async with get_business_session(session_factory) as session:
        uncommitted = Approval(
            id=rollback_id,
            task_run_id=run_a.id,
            replan_count=1,
            step_position=6,
            action_name="send_report",
            action_version="1",
            proposed_action={"recipient": "a@example.com"},
            requester_membership_id=member_a.id,
        )
        await ApprovalRepository(session).add(uncommitted)
    async with get_business_session(session_factory) as session:
        assert await session.get(Approval, rollback_id) is None


@pytest.mark.asyncio
async def test_task_repositories_hide_foreign_resources_and_preserve_run_history(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization_a = Organization(name="Repository Tenant A")
    organization_b = Organization(name="Repository Tenant B")
    user_a = User(email="repository-a@example.com", password_hash="opaque-test-hash")
    user_b = User(email="repository-b@example.com", password_hash="opaque-test-hash")

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization_a)
            await OrganizationRepository(session).add(organization_b)
            await UserRepository(session).add(user_a)
            await UserRepository(session).add(user_b)
            task_a = Task(
                organization_id=organization_a.id,
                created_by_user_id=user_a.id,
                title="Tenant A task",
            )
            task_b = Task(
                organization_id=organization_b.id,
                created_by_user_id=user_b.id,
                title="Tenant B task",
            )
            await TaskRepository(session).add(task_a)
            await TaskRepository(session).add(task_b)
            run_a_two = TaskRun(task_id=task_a.id, run_number=2, status=TaskRunStatus.FAILED)
            await TaskRunRepository(session).add(run_a_two)
            run_a_one = TaskRun(
                task_id=task_a.id,
                run_number=1,
                status=TaskRunStatus.SUCCEEDED,
            )
            await TaskRunRepository(session).add(run_a_one)
            foreign_run = TaskRun(
                task_id=task_b.id,
                run_number=1,
                status=TaskRunStatus.CANCELLED,
            )
            await TaskRunRepository(session).add(foreign_run)

    async with get_business_session(session_factory) as session:
        task_repository = TaskRepository(session)
        assert (
            await task_repository.get_in_principal_tenant(task_a.id, organization_a.id) is not None
        )
        assert await task_repository.get_in_principal_tenant(task_b.id, organization_a.id) is None
        assert await task_repository.get_in_principal_tenant(uuid4(), organization_a.id) is None
        assert [
            task.id for task in await task_repository.list_for_organization(organization_a.id)
        ] == [task_a.id]

        task_run_repository = TaskRunRepository(session)
        own_run = await task_run_repository.get_in_principal_tenant(run_a_two.id, organization_a.id)
        assert own_run is not None
        assert own_run.task_id == task_a.id
        assert (
            await task_run_repository.get_in_principal_tenant(foreign_run.id, organization_a.id)
            is None
        )
        assert await task_run_repository.get_in_principal_tenant(uuid4(), organization_a.id) is None
        history = await task_run_repository.list_for_task_in_organization(
            task_a.id, organization_a.id
        )
        assert [run.run_number for run in history] == [1, 2]
        assert (
            await task_run_repository.list_for_task_in_organization(task_a.id, organization_b.id)
            == []
        )


@pytest.mark.asyncio
async def test_user_identity_constraints_and_persistence(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    password_hash = "$argon2id$v=19$test-only-opaque-value"
    user = User(
        email="  Straße@Example.COM  ",
        password_hash=password_hash,
    )

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(user)

    assert user.id is not None
    assert user.id.version == 4
    assert user.email == "Straße@Example.COM"
    assert user.normalized_email == "strasse@example.com"
    assert user.is_active is True
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset().total_seconds() == 0
    assert user.updated_at.tzinfo is not None
    assert user.updated_at.utcoffset().total_seconds() == 0

    async with get_business_session(session_factory) as session:
        async with session.begin():
            repository = UserRepository(session)
            loaded = await repository.get(user.id)
            assert loaded is not None
            assert loaded.password_hash == password_hash
            assert password_hash not in repr(loaded)
            assert await repository.get_by_email("  STRASSE@EXAMPLE.COM ") is loaded
            original_created_at = loaded.created_at
            original_updated_at = loaded.updated_at
            await asyncio.sleep(0.01)
            loaded.is_active = False
            # ``updated_at`` is an ORM onupdate value, so it is only refreshed
            # once the change is flushed to PostgreSQL.
            await session.flush()
            assert loaded.is_active is False
            assert loaded.updated_at > original_updated_at

    async with get_business_session(session_factory) as session:
        loaded = await UserRepository(session).get(user.id)
        assert loaded is not None
        assert loaded.is_active is False
        # The onupdate timestamp must survive the commit, not only the session.
        assert loaded.created_at == original_created_at
        assert loaded.updated_at > original_updated_at
        assert loaded.updated_at.utcoffset().total_seconds() == 0

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await UserRepository(session).add(
                    User(email=" strasse@EXAMPLE.com ", password_hash="another-opaque-hash")
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    insert(User).values(
                        id=uuid4(),
                        email="null-normalized@example.com",
                        normalized_email=None,
                        password_hash="opaque-test-hash",
                    )
                )

    rolled_back_id = uuid4()
    with pytest.raises(RuntimeError, match="force user rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await UserRepository(session).add(
                    User(
                        id=rolled_back_id,
                        email="rollback@example.com",
                        password_hash="opaque-test-hash",
                    )
                )
                raise RuntimeError("force user rollback")

    async with get_business_session(session_factory) as session:
        assert await UserRepository(session).get(rolled_back_id) is None


@pytest.mark.asyncio
async def test_membership_persistence_roles_and_cross_tenant_scope(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    alpha = Organization(name="Tenant Alpha")
    beta = Organization(name="Tenant Beta")
    user = User(email="owner@example.com", password_hash="opaque-test-hash")
    other_user = User(email="other@example.com", password_hash="opaque-test-hash")

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(alpha)
            await OrganizationRepository(session).add(beta)
            await UserRepository(session).add(user)
            await UserRepository(session).add(other_user)

    membership = Membership(user_id=user.id, organization_id=alpha.id, role=Role.OWNER)
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await MembershipRepository(session).add(membership)

    assert membership.id is not None
    assert membership.id.version == 4
    assert membership.is_active is True
    assert membership.created_at.tzinfo is not None
    assert membership.created_at.utcoffset().total_seconds() == 0
    assert membership.updated_at.utcoffset().total_seconds() == 0

    async with get_business_session(session_factory) as session:
        async with session.begin():
            repository = MembershipRepository(session)
            loaded = await repository.get(membership.id)
            assert loaded is not None
            assert loaded.user_id == user.id
            assert loaded.organization_id == alpha.id
            assert loaded.role is Role.OWNER

            # The same user may join a second organization.
            await repository.add(
                Membership(user_id=user.id, organization_id=beta.id, role=Role.MEMBER)
            )
            # Scope is answered per organization and never leaks a sibling tenant.
            alpha_membership = await repository.get_for_user_in_organization(user.id, alpha.id)
            beta_membership = await repository.get_for_user_in_organization(user.id, beta.id)
            assert alpha_membership is not None and alpha_membership.role is Role.OWNER
            assert beta_membership is not None and beta_membership.role is Role.MEMBER
            assert alpha_membership.organization_id != beta_membership.organization_id

            original_updated_at = loaded.updated_at
            await asyncio.sleep(0.01)
            loaded.is_active = False
            # ``updated_at`` is an ORM onupdate value, refreshed on flush only.
            await session.flush()
            assert loaded.is_active is False
            assert loaded.updated_at > original_updated_at

    async with get_business_session(session_factory) as session:
        repository = MembershipRepository(session)
        alpha_memberships = await repository.list_for_organization(alpha.id)
        assert [item.organization_id for item in alpha_memberships] == [alpha.id]
        assert alpha_memberships[0].is_active is False
        reloaded = await repository.get(membership.id)
        assert reloaded is not None
        # Inactive memberships are retained, not deleted.
        assert reloaded.is_active is False
        assert reloaded.updated_at.utcoffset().total_seconds() == 0

        # Organization listings are ordered so callers see a deterministic page.
        second_member = User(email="second@example.com", password_hash="opaque-test-hash")
        await UserRepository(session).add(second_member)
        second_membership = await repository.add(
            Membership(user_id=second_member.id, organization_id=beta.id, role=Role.OWNER)
        )
        beta_listing = await repository.list_for_organization(beta.id)
        assert len(beta_listing) == 2
        assert {item.id for item in beta_listing} == {beta_membership.id, second_membership.id}

        # Every frozen role round-trips through VARCHAR + check constraint.
        for role in Role:
            member = User(email=f"role-{role.value}@example.com", password_hash="opaque-test-hash")
            await UserRepository(session).add(member)
            await repository.add(
                Membership(
                    user_id=member.id,
                    organization_id=alpha.id,
                    role=role,
                )
            )
            stored = await repository.get_for_user_in_organization(member.id, alpha.id)
            assert stored is not None
            assert stored.role is role


@pytest.mark.asyncio
async def test_membership_constraints_and_rollback(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Constraint Org")
    user = User(email="constraint@example.com", password_hash="opaque-test-hash")

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            await MembershipRepository(session).add(
                Membership(user_id=user.id, organization_id=organization.id, role=Role.ADMIN)
            )

    # A duplicate (user_id, organization_id) pair is rejected by PostgreSQL,
    # even after the existing membership was deactivated.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            existing = await MembershipRepository(session).get_for_user_in_organization(
                user.id, organization.id
            )
            assert existing is not None
            existing.is_active = False
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await MembershipRepository(session).add(
                    Membership(user_id=user.id, organization_id=organization.id, role=Role.MEMBER)
                )

    # The database rejects roles outside the frozen V1 set.
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    insert(Membership).values(
                        id=uuid4(),
                        user_id=user.id,
                        organization_id=organization.id,
                        role="superadmin",
                    )
                )

    # Foreign keys are enforced for both sides of the pair.
    for unknown_user_id, unknown_organization_id in (
        (uuid4(), organization.id),
        (user.id, uuid4()),
    ):
        with pytest.raises(IntegrityError):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await MembershipRepository(session).add(
                        Membership(
                            user_id=unknown_user_id,
                            organization_id=unknown_organization_id,
                            role=Role.MEMBER,
                        )
                    )

    doomed_user = User(email="doomed@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(doomed_user)
            await MembershipRepository(session).add(
                Membership(
                    user_id=doomed_user.id, organization_id=organization.id, role=Role.MEMBER
                )
            )

    # ON DELETE RESTRICT prevents silent cascade removal of authorization rows.
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(delete(User).where(User.id == doomed_user.id))
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    delete(Organization).where(Organization.id == organization.id)
                )

    async with get_business_session(session_factory) as session:
        assert (
            await MembershipRepository(session).get_for_user_in_organization(
                doomed_user.id, organization.id
            )
            is not None
        )

    rolled_back_id = uuid4()
    rolled_back_user = User(email="rollback-member@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(rolled_back_user)
    with pytest.raises(RuntimeError, match="force membership rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await MembershipRepository(session).add(
                    Membership(
                        id=rolled_back_id,
                        user_id=rolled_back_user.id,
                        organization_id=organization.id,
                        role=Role.MEMBER,
                    )
                )
                raise RuntimeError("force membership rollback")

    async with get_business_session(session_factory) as session:
        assert await MembershipRepository(session).get(rolled_back_id) is None


PASSWORD = "correct horse battery staple"


async def _seed_identity(
    session_factory,
    *,
    email: str = "session-owner@example.com",
    password: str = PASSWORD,
    organization: Organization | None = None,
    user_is_active: bool = True,
    membership_is_active: bool = True,
) -> tuple[Organization, User, Membership]:
    """Create one organization, user, and owner membership for auth tests."""

    organization = organization or Organization(name=f"Auth Org {uuid4().hex[:8]}")
    user = User(email=email, password_hash=hash_password(password))
    user.is_active = user_is_active
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            membership = Membership(
                user_id=user.id,
                organization_id=organization.id,
                role=Role.OWNER,
                is_active=membership_is_active,
            )
            await MembershipRepository(session).add(membership)
    return organization, user, membership


@pytest.mark.asyncio
async def test_auth_session_issuance_persists_only_the_token_digest(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization, user, membership = await _seed_identity(session_factory)

    async with get_business_session(session_factory) as session:
        async with session.begin():
            issued = await AuthService(session).login("  SESSION-OWNER@example.com ", PASSWORD)
            assert issued.user_id == user.id
            assert issued.membership_id == membership.id
            assert issued.organization_id == organization.id
            assert issued.expires_at.tzinfo is not None
            assert issued.expires_at.utcoffset().total_seconds() == 0

    raw_token = issued.token
    assert raw_token
    assert issued.session_id.version == 4

    async with get_business_session(session_factory) as session:
        stored = await AuthSessionRepository(session).get(issued.session_id)
        assert stored is not None
        assert stored.user_id == user.id
        assert stored.membership_id == membership.id
        assert stored.token_hash == hash_token(raw_token)
        assert stored.token_hash != raw_token
        assert stored.revoked_at is None
        assert raw_token not in repr(stored)
        assert stored.token_hash not in repr(stored)
        assert stored.expires_at - stored.created_at == SESSION_TTL

        # A raw-token column must not exist anywhere on the session table.
        connection = await session.connection()
        column_names = {
            column["name"]
            for column in await connection.run_sync(
                lambda conn: inspect(conn).get_columns("auth_sessions", schema="taskpilot")
            )
        }
        assert "raw_token" not in column_names
        assert "token" not in column_names

    async with get_business_session(session_factory) as session:
        service = AuthService(session)
        found = await service.get_valid_session(raw_token)
        assert found is not None and found.id == issued.session_id
        assert await service.get_valid_session(raw_token + "x") is None
        assert await service.get_valid_session(hash_token(raw_token)) is None

    async with get_business_session(session_factory) as session:
        async with session.begin():
            assert await AuthService(session).revoke_token(raw_token) is True
    async with get_business_session(session_factory) as session:
        assert await AuthService(session).get_valid_session(raw_token) is None
        stored = await AuthSessionRepository(session).get(issued.session_id)
        assert stored is not None
        # Revocation retains the row for audit instead of deleting it.
        assert stored.revoked_at is not None


@pytest.mark.asyncio
async def test_expired_sessions_are_rejected_and_cleaned_up(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    await _seed_identity(session_factory)

    async with get_business_session(session_factory) as session:
        async with session.begin():
            issued = await AuthService(session).login("session-owner@example.com", PASSWORD)

    async with get_business_session(session_factory) as session:
        async with session.begin():
            stored = await AuthSessionRepository(session).get(issued.session_id)
            assert stored is not None
            stored.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    async with get_business_session(session_factory) as session:
        service = AuthService(session)
        assert await service.get_valid_session(issued.token) is None
    async with get_business_session(session_factory) as session:
        async with session.begin():
            assert await AuthService(session).cleanup_expired_sessions() == 1
    async with get_business_session(session_factory) as session:
        assert await AuthSessionRepository(session).get(issued.session_id) is None


@pytest.mark.asyncio
async def test_login_rejects_inactive_user_membership_and_unknown_identity(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    await _seed_identity(session_factory, email="inactive-user@example.com", user_is_active=False)
    await _seed_identity(
        session_factory, email="inactive-member@example.com", membership_is_active=False
    )

    for email in (
        "inactive-user@example.com",
        "inactive-member@example.com",
        "unknown@example.com",
    ):
        async with get_business_session(session_factory) as session:
            with pytest.raises(LoginError) as raised:
                await AuthService(session).login(email, PASSWORD)
            # Unknown, inactive, and wrong-credential paths share one message.
            assert str(raised.value) == "Invalid credentials"

    async with get_business_session(session_factory) as session:
        with pytest.raises(LoginError):
            await AuthService(session).login("inactive-user@example.com", "wrong password")

    async with get_business_session(session_factory) as session:
        result = await session.execute(select(AuthSession))
        assert list(result.scalars()) == []


@pytest.mark.asyncio
async def test_login_rejects_an_inactive_organization(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization, _, _ = await _seed_identity(session_factory, email="inactive-org@example.com")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            stored = await OrganizationRepository(session).get(organization.id)
            assert stored is not None
            stored.is_active = False

    async with get_business_session(session_factory) as session:
        with pytest.raises(LoginError) as raised:
            await AuthService(session).login("inactive-org@example.com", PASSWORD)
        assert str(raised.value) == "Invalid credentials"

    async with get_business_session(session_factory) as session:
        assert list((await session.execute(select(AuthSession))).scalars()) == []


async def _seed_multi_org_user(
    session_factory,
    *,
    email: str,
    organizations: list[tuple[Organization, str]],
    password: str = PASSWORD,
) -> User:
    """Create one user plus a (organization, role) membership for each entry."""

    user = User(email=email, password_hash=hash_password(password))
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(user)
            for organization, role in organizations:
                await OrganizationRepository(session).add(organization)
                await MembershipRepository(session).add(
                    Membership(
                        user_id=user.id,
                        organization_id=organization.id,
                        role=Role(role),
                    )
                )
    return user


@pytest.mark.asyncio
async def test_multi_org_login_selection_and_switching(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization_a = Organization(name="Selection Alpha")
    organization_b = Organization(name="Selection Beta")
    user = await _seed_multi_org_user(
        session_factory,
        email="multi-org@example.com",
        organizations=[(organization_a, "owner"), (organization_b, "member")],
    )
    expected_ids = tuple(sorted({organization_a.id, organization_b.id}, key=str))

    # No selector with two eligible memberships: a typed selection result, and
    # no session or token is created.
    async with get_business_session(session_factory) as session:
        result = await AuthService(session).login("multi-org@example.com", PASSWORD)
    assert isinstance(result, OrganizationSelectionRequired)
    assert result.code == ORGANIZATION_SELECTION_REQUIRED
    assert result.organization_ids == expected_ids
    async with get_business_session(session_factory) as session:
        assert list((await session.execute(select(AuthSession))).scalars()) == []

    # Explicit selector A binds the session to A's membership only.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            session_a = await AuthService(session).login(
                "multi-org@example.com", PASSWORD, organization_id=organization_a.id
            )
    assert isinstance(session_a, AuthenticatedSession)
    assert session_a.organization_id == organization_a.id
    async with get_business_session(session_factory) as session:
        stored_membership_a = await MembershipRepository(session).get_for_user_in_organization(
            user.id, organization_a.id
        )
        stored_membership_b = await MembershipRepository(session).get_for_user_in_organization(
            user.id, organization_b.id
        )
        assert stored_membership_a is not None and stored_membership_b is not None
        assert session_a.membership_id == stored_membership_a.id
        assert session_a.membership_id != stored_membership_b.id

    # Switching uses a fresh login with the other selector and issues a distinct
    # session without revoking the first.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            session_b = await AuthService(session).login(
                "multi-org@example.com", PASSWORD, organization_id=organization_b.id
            )
    assert isinstance(session_b, AuthenticatedSession)
    assert session_b.organization_id == organization_b.id
    assert session_b.membership_id == stored_membership_b.id
    assert session_b.session_id != session_a.session_id

    async with get_business_session(session_factory) as session:
        service = AuthService(session)
        first = await service.get_valid_session(session_a.token)
        second = await service.get_valid_session(session_b.token)
        assert first is not None and first.membership_id == stored_membership_a.id
        assert first.revoked_at is None
        assert second is not None and second.membership_id == stored_membership_b.id


@pytest.mark.asyncio
async def test_eligibility_is_evaluated_in_postgresql(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    active_organization = Organization(name="Eligible Org")
    inactive_organization = Organization(name="Inactive Org")
    user = User(email="eligibility@example.com", password_hash=hash_password(PASSWORD))
    stranger = User(email="stranger-eligibility@example.com", password_hash=hash_password(PASSWORD))

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(active_organization)
            await OrganizationRepository(session).add(inactive_organization)
            await UserRepository(session).add(user)
            await UserRepository(session).add(stranger)
            await MembershipRepository(session).add(
                Membership(
                    user_id=user.id,
                    organization_id=active_organization.id,
                    role=Role.OWNER,
                )
            )
            await MembershipRepository(session).add(
                Membership(
                    user_id=user.id,
                    organization_id=inactive_organization.id,
                    role=Role.MEMBER,
                    is_active=False,
                )
            )
            await MembershipRepository(session).add(
                Membership(
                    user_id=stranger.id,
                    organization_id=active_organization.id,
                    role=Role.ADMIN,
                )
            )
            stored_inactive_org = await OrganizationRepository(session).get(
                inactive_organization.id
            )
            assert stored_inactive_org is not None
            stored_inactive_org.is_active = False

    async with get_business_session(session_factory) as session:
        # The SQL join returns only the active membership in the active
        # organization, and never another user's row.
        eligible = await MembershipRepository(session).list_eligible_for_user(user.id)
        assert [item.organization_id for item in eligible] == [active_organization.id]

    # A single eligible membership is issued automatically, without a selector.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            result = await AuthService(session).login("eligibility@example.com", PASSWORD)
    assert isinstance(result, AuthenticatedSession)
    assert result.organization_id == active_organization.id

    # Selecting the non-eligible (inactive) membership fails generically.
    async with get_business_session(session_factory) as session:
        with pytest.raises(LoginError) as raised:
            await AuthService(session).login(
                "eligibility@example.com",
                PASSWORD,
                organization_id=inactive_organization.id,
            )
        assert str(raised.value) == "Invalid credentials"


@pytest.mark.asyncio
async def test_service_ownership_invariant_fails_closed_with_real_rows(
    migrated_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repository that leaks a foreign row must not produce a session."""

    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Ownership Org")
    owner = User(email="ownership-owner@example.com", password_hash=hash_password(PASSWORD))
    stranger = User(email="ownership-stranger@example.com", password_hash=hash_password(PASSWORD))
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(owner)
            await UserRepository(session).add(stranger)
            await MembershipRepository(session).add(
                Membership(user_id=owner.id, organization_id=organization.id, role=Role.OWNER)
            )
            await MembershipRepository(session).add(
                Membership(user_id=stranger.id, organization_id=organization.id, role=Role.MEMBER)
            )

    async def leaking_scope(self, user_id: UUID) -> list[Membership]:  # noqa: ANN001, ARG001
        # Simulate a regression that drops the user predicate and returns the
        # stranger's row for the authenticated owner.
        statement = select(Membership).where(Membership.user_id == stranger.id)
        result = await self.session.scalars(statement)
        return list(result)

    monkeypatch.setattr(MembershipRepository, "list_eligible_for_user", leaking_scope)

    async with get_business_session(session_factory) as session:
        async with session.begin():
            with pytest.raises(LoginError) as raised:
                await AuthService(session).login("ownership-owner@example.com", PASSWORD)
            assert str(raised.value) == "Invalid credentials"

    async with get_business_session(session_factory) as session:
        # The foreign membership is never turned into a session.
        assert list((await session.execute(select(AuthSession))).scalars()) == []


@pytest.mark.asyncio
async def test_auth_session_constraints_and_restrict_deletes(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    _, user, membership = await _seed_identity(session_factory)
    duplicate_hash = hash_token(generate_token())

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await AuthSessionRepository(session).add(
                    AuthSession(
                        user_id=uuid4(),
                        membership_id=membership.id,
                        token_hash=duplicate_hash,
                        expires_at=datetime.now(UTC) + SESSION_TTL,
                    )
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await AuthSessionRepository(session).add(
                    AuthSession(
                        user_id=user.id,
                        membership_id=uuid4(),
                        token_hash=duplicate_hash,
                        expires_at=datetime.now(UTC) + SESSION_TTL,
                    )
                )

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await AuthSessionRepository(session).add(
                AuthSession(
                    user_id=user.id,
                    membership_id=membership.id,
                    token_hash=duplicate_hash,
                    expires_at=datetime.now(UTC) + SESSION_TTL,
                )
            )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await AuthSessionRepository(session).add(
                    AuthSession(
                        user_id=user.id,
                        membership_id=membership.id,
                        token_hash=duplicate_hash,
                        expires_at=datetime.now(UTC) + SESSION_TTL,
                    )
                )

    # Session rows are audit-relevant, so user/membership deletion is restricted.
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(delete(User).where(User.id == user.id))
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(delete(Membership).where(Membership.id == membership.id))

    uncommitted_id = uuid4()
    async with get_business_session(session_factory) as session:
        await AuthSessionRepository(session).add(
            AuthSession(
                id=uncommitted_id,
                user_id=user.id,
                membership_id=membership.id,
                token_hash=hash_token(generate_token()),
                expires_at=datetime.now(UTC) + SESSION_TTL,
            )
        )
    async with get_business_session(session_factory) as session:
        assert await AuthSessionRepository(session).get(uncommitted_id) is None


@pytest.mark.asyncio
async def test_bootstrap_creates_owner_then_is_an_idempotent_no_op(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization_name = "Bootstrap Org"
    email = "bootstrap-owner@example.com"

    async with get_business_session(session_factory) as session:
        created = await bootstrap_owner(
            session,
            organization_name=organization_name,
            email=email,
            password=PASSWORD,
        )
    assert created.outcome is BootstrapOutcome.CREATED

    async with get_business_session(session_factory) as session:
        user = await UserRepository(session).get_by_email(email)
        assert user is not None
        first_hash = user.password_hash
        assert first_hash.startswith("$argon2id$")
        assert PASSWORD not in first_hash
        memberships = await MembershipRepository(session).list_for_user(user.id)
        assert len(memberships) == 1
        assert memberships[0].role is Role.OWNER
        assert memberships[0].is_active is True

    async with get_business_session(session_factory) as session:
        repeated = await bootstrap_owner(
            session,
            organization_name=organization_name,
            email=email,
            password="a-completely-different-password",
        )
    assert repeated.outcome is BootstrapOutcome.ALREADY_INITIALIZED
    assert repeated.organization_id == created.organization_id
    assert repeated.membership_id == created.membership_id

    async with get_business_session(session_factory) as session:
        # A repeated run must neither duplicate rows nor reset the password.
        user = await UserRepository(session).get_by_email(email)
        assert user is not None
        assert user.password_hash == first_hash
        assert verify_password(PASSWORD, user.password_hash) is True
        assert verify_password("a-completely-different-password", user.password_hash) is False
        assert len(await MembershipRepository(session).list_for_user(user.id)) == 1
        organizations = await session.scalars(
            select(Organization).where(Organization.name == organization_name)
        )
        assert len(list(organizations)) == 1


@pytest.mark.asyncio
async def test_bootstrap_never_logs_the_password_or_the_stored_hash(
    migrated_engine: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """T026 secret row: a real bootstrap leaks neither plaintext nor hash to logs."""

    session_factory = create_session_factory(migrated_engine)
    configure_logging()

    with caplog.at_level(logging.DEBUG):
        async with get_business_session(session_factory) as session:
            result = await bootstrap_owner(
                session,
                organization_name="Logging Org",
                email="logging-owner@example.com",
                password=PASSWORD,
            )
        assert result.outcome is BootstrapOutcome.CREATED
        async with get_business_session(session_factory) as session:
            user = await UserRepository(session).get_by_email("logging-owner@example.com")
        assert user is not None
        # Exactly what a leaky debug log of the persisted row would emit.
        logging.getLogger("tests.t026.bootstrap").debug("user=%r", user)

    assert PASSWORD not in caplog.text
    assert user.password_hash.startswith("$argon2id$")
    assert user.password_hash not in caplog.text
    assert "$argon2id$" not in caplog.text
    assert "password_hash" not in caplog.text


@pytest.mark.asyncio
async def test_bootstrap_fails_closed_on_conflicting_state(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization, user, membership = await _seed_identity(
        session_factory, email="conflict-owner@example.com"
    )

    # Same user, different organization name.
    async with get_business_session(session_factory) as session:
        with pytest.raises(BootstrapError):
            await bootstrap_owner(
                session,
                organization_name="A Different Organization",
                email="conflict-owner@example.com",
                password=PASSWORD,
            )

    # Same organization, unknown user.
    async with get_business_session(session_factory) as session:
        with pytest.raises(BootstrapError):
            await bootstrap_owner(
                session,
                organization_name=organization.name,
                email="someone-else@example.com",
                password=PASSWORD,
            )

    # Matching organization and user but a non-owner role.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            stored = await MembershipRepository(session).get(membership.id)
            assert stored is not None
            stored.role = Role.ADMIN
    async with get_business_session(session_factory) as session:
        with pytest.raises(BootstrapError, match="refusing"):
            await bootstrap_owner(
                session,
                organization_name=organization.name,
                email="conflict-owner@example.com",
                password=PASSWORD,
            )

    # Conflicting state must never be repaired or elevated.
    async with get_business_session(session_factory) as session:
        stored = await MembershipRepository(session).get(membership.id)
        assert stored is not None
        assert stored.role is Role.ADMIN
        assert await MembershipRepository(session).list_for_user(user.id) != []


@pytest.mark.asyncio
async def test_bootstrap_rolls_back_every_write_on_failure(
    migrated_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization_name = "Rollback Org"
    email = "rollback-owner@example.com"

    async def failing_add(self, membership):  # noqa: ANN001, ANN202 - test double
        raise RuntimeError("forced membership failure")

    monkeypatch.setattr(MembershipRepository, "add", failing_add)
    async with get_business_session(session_factory) as session:
        with pytest.raises(RuntimeError, match="forced membership failure"):
            await bootstrap_owner(
                session,
                organization_name=organization_name,
                email=email,
                password=PASSWORD,
            )

    async with get_business_session(session_factory) as session:
        assert await UserRepository(session).get_by_email(email) is None
        organizations = await session.scalars(
            select(Organization).where(Organization.name == organization_name)
        )
        assert list(organizations) == []
        assert list((await session.execute(select(AuthSession))).scalars()) == []


@pytest.mark.asyncio
async def test_real_database_failure_rolls_back_and_never_echoes_the_hash(
    migrated_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_factory = create_session_factory(migrated_engine)
    database_url = migrated_engine.url.render_as_string(hide_password=False)

    # An existing account forces the users UNIQUE(normalized_email) violation
    # mid-bootstrap, which is the realistic database failure path.
    password_hash = hash_password(PASSWORD)
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(
                User(email="taken@example.com", password_hash=password_hash)
            )

    # Force the failure to surface from inside the access layer after the
    # organization row has already been written, so the CLI conversion and the
    # transaction rollback are both exercised for real.
    from sqlalchemy.exc import IntegrityError as SqlIntegrityError

    async def failing_run(self, **kwargs: object) -> None:  # noqa: ANN001
        del self, kwargs
        raise SqlIntegrityError(
            "UPDATE taskpilot.users SET password_hash=%(password_hash)s",
            {"password_hash": password_hash},
            Exception(f"duplicate key value violates unique constraint {password_hash}"),
        )

    monkeypatch.setattr("service.bootstrap.BootstrapService.run", failing_run)
    monkeypatch.setattr(bootstrap_cli.settings, "TASKPILOT_DATABASE_URL", database_url)
    exit_code = await bootstrap_cli._run(
        organization_name="Duplicate Org",
        email="  TAKEN@Example.COM  ",
        password=PASSWORD,
    )

    captured = capsys.readouterr()
    assert exit_code == bootstrap_cli.EXIT_FAILED
    assert captured.err.strip() == bootstrap_cli.DATABASE_ERROR_MESSAGE

    # Neither the plaintext password nor the stored hash may reach the operator,
    # and the driver's SQL/bind-parameter text must not be rendered either.
    for leaked in (PASSWORD, password_hash, "password_hash", "INSERT INTO", "$argon2id$"):
        assert leaked not in captured.out
        assert leaked not in captured.err

    async with get_business_session(session_factory) as session:
        # Full rollback: the organization attempted before the failure is gone.
        organizations = await session.scalars(
            select(Organization).where(Organization.name == "Duplicate Org")
        )
        assert list(organizations) == []
        users = await session.scalars(
            select(User).where(User.normalized_email == "taken@example.com")
        )
        assert len(list(users)) == 1


@pytest.mark.asyncio
async def test_observability_persistence_is_bounded_tenant_scoped_and_restrictive(
    migrated_engine: AsyncEngine,
) -> None:
    """T091/T092 live PostgreSQL evidence for ownership, idempotency, and rollback."""

    session_factory = create_session_factory(migrated_engine)
    async with migrated_engine.connect() as connection:
        for table_name, expected_unique in (
            ("agent_runs", {"uq_agent_runs_invocation"}),
            ("tool_calls", {"uq_tool_calls_agent_run_index"}),
        ):
            columns = await connection.run_sync(
                lambda conn, name=table_name: inspect(conn).get_columns(name, schema="taskpilot")
            )
            column_by_name = {column["name"]: column for column in columns}
            timestamp_columns = {"started_at", "finished_at", "created_at", "updated_at"}
            assert all(column_by_name[name]["type"].timezone for name in timestamp_columns)
            assert not {"organization_id", "task_id"}.intersection(column_by_name)
            unique = await connection.run_sync(
                lambda conn, name=table_name: inspect(conn).get_unique_constraints(
                    name, schema="taskpilot"
                )
            )
            assert expected_unique <= {constraint["name"] for constraint in unique}
            foreign_keys = await connection.run_sync(
                lambda conn, name=table_name: inspect(conn).get_foreign_keys(
                    name, schema="taskpilot"
                )
            )
            assert len(foreign_keys) == 1
            assert foreign_keys[0]["options"].get("ondelete") == "RESTRICT"

    organization_a = Organization(name="Trace Tenant A")
    organization_b = Organization(name="Trace Tenant B")
    user_a = User(email="trace-a@example.com", password_hash="opaque-a")
    user_b = User(email="trace-b@example.com", password_hash="opaque-b")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization_a)
            await OrganizationRepository(session).add(organization_b)
            await UserRepository(session).add(user_a)
            await UserRepository(session).add(user_b)
            task_a = Task(
                organization_id=organization_a.id,
                created_by_user_id=user_a.id,
                title="Trace task A",
            )
            task_b = Task(
                organization_id=organization_b.id,
                created_by_user_id=user_b.id,
                title="Trace task B",
            )
            await TaskRepository(session).add(task_a)
            await TaskRepository(session).add(task_b)
            run_a = TaskRun(task_id=task_a.id, run_number=1)
            run_b = TaskRun(task_id=task_b.id, run_number=1)
            await TaskRunRepository(session).add(run_a)
            await TaskRunRepository(session).add(run_b)

    started_at = datetime.now(UTC)
    agent_a = AgentRun(
        task_run_id=run_a.id,
        replan_count=0,
        step_position=0,
        retry_count=0,
        agent_name="planner",
        status=AgentRunStatus.SUCCEEDED,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=3),
    )
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await AgentRunRepository(session).add(agent_a, organization_a.id)
            call_a = ToolCall(
                agent_run_id=agent_a.id,
                call_index=0,
                tool_name="inspect",
                status=ToolCallStatus.SUCCEEDED,
                started_at=started_at,
                arguments={"api_key": "secret-must-not-persist"},
            )
            await ToolCallRepository(session).add(call_a, organization_a.id)

    async with get_business_session(session_factory) as session:
        assert (
            await AgentRunRepository(session).get_in_principal_tenant(agent_a.id, organization_a.id)
            is not None
        )
        assert (
            await AgentRunRepository(session).get_in_principal_tenant(agent_a.id, organization_b.id)
            is None
        )
        loaded_call = await ToolCallRepository(session).get_in_principal_tenant(
            call_a.id, organization_a.id
        )
        assert loaded_call is not None
        assert loaded_call.arguments == {"api_key": "[REDACTED]"}
        assert (
            await ToolCallRepository(session).get_in_principal_tenant(call_a.id, organization_b.id)
            is None
        )

    duplicate_agent = AgentRun(
        task_run_id=run_a.id,
        replan_count=0,
        step_position=0,
        retry_count=0,
        agent_name="planner",
        status=AgentRunStatus.RUNNING,
        started_at=started_at,
    )
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await AgentRunRepository(session).add(duplicate_agent, organization_a.id)

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                duplicate_call = ToolCall(
                    agent_run_id=agent_a.id,
                    call_index=0,
                    tool_name="inspect",
                    started_at=started_at,
                    arguments={},
                )
                await ToolCallRepository(session).add(duplicate_call, organization_a.id)

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text("DELETE FROM taskpilot.agent_runs WHERE id = :id"),
                    {"id": agent_a.id},
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text("DELETE FROM taskpilot.task_runs WHERE id = :id"),
                    {"id": run_a.id},
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO taskpilot.tool_calls "
                        "(id, agent_run_id, call_index, tool_name, status, started_at, arguments) "
                        "VALUES (:id, :agent_run_id, 1000, 'inspect', 'running', :started_at, '{}'::jsonb)"
                    ),
                    {"id": uuid4(), "agent_run_id": agent_a.id, "started_at": started_at},
                )

    foreign_agent = AgentRun(
        task_run_id=run_a.id,
        replan_count=0,
        step_position=1,
        retry_count=0,
        agent_name="foreign-attempt",
        started_at=started_at,
    )
    with pytest.raises(ValueError, match="not visible"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await AgentRunRepository(session).add(foreign_agent, organization_b.id)

    rolled_back_id = uuid4()
    with pytest.raises(RuntimeError, match="trace rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                rolled_back = AgentRun(
                    id=rolled_back_id,
                    task_run_id=run_a.id,
                    replan_count=1,
                    step_position=0,
                    retry_count=0,
                    agent_name="rollback",
                    started_at=started_at,
                )
                await AgentRunRepository(session).add(rolled_back, organization_a.id)
                raise RuntimeError("trace rollback")
    async with get_business_session(session_factory) as session:
        assert (
            await AgentRunRepository(session).get_in_principal_tenant(
                rolled_back_id, organization_a.id
            )
            is None
        )


@pytest.mark.asyncio
async def test_observability_postgres_checks_and_persisted_redaction(
    migrated_engine: AsyncEngine,
) -> None:
    """Exercise DB checks directly and verify sanitized values after reload."""

    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Constraint Tenant")
    user = User(email="constraint@example.com", password_hash="opaque")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            task = Task(
                organization_id=organization.id,
                created_by_user_id=user.id,
                title="Constraint task",
            )
            await TaskRepository(session).add(task)
            task_run = TaskRun(task_id=task.id, run_number=1)
            await TaskRunRepository(session).add(task_run)

    started_at = datetime.now(UTC)
    agent = AgentRun(
        id=uuid4(),
        task_run_id=task_run.id,
        replan_count=0,
        step_position=0,
        retry_count=0,
        agent_name="constraint-agent",
        status=AgentRunStatus.SUCCEEDED,
        started_at=started_at,
        finished_at=started_at + timedelta(milliseconds=4),
        usage={"status": "unavailable", "reason": "not_returned"},
        provider_metadata={
            "provider": "fixture",
            "model": "fixture-model",
            "response_id": "api_key=agent-provider-secret",
        },
    )
    call = ToolCall(
        agent_run_id=agent.id,
        call_index=0,
        tool_name="constraint-tool",
        status=ToolCallStatus.SUCCEEDED,
        started_at=started_at,
        arguments={
            "api_key": "argument-secret",
            "nested": {"password": "nested-secret"},
            "authorization": "Bearer argument-token",
        },
        result={
            "access_token": "result-secret",
            "nested": {"dsn": "postgresql://user:password@example.test/db"},
        },
        usage={"status": "unavailable", "reason": "unsupported"},
    )
    nullable_agent = AgentRun(
        id=uuid4(),
        task_run_id=task_run.id,
        replan_count=1,
        step_position=0,
        retry_count=0,
        agent_name="nullable-agent",
        status=AgentRunStatus.RUNNING,
        started_at=started_at,
    )
    failed_agent = AgentRun(
        id=uuid4(),
        task_run_id=task_run.id,
        replan_count=1,
        step_position=1,
        retry_count=0,
        agent_name="failed-agent",
        status=AgentRunStatus.FAILED,
        error_class="TERMINAL",
        error_code="provider_error",
        error_message="provider failed: api_key=agent-error-secret",
        started_at=started_at,
    )
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await AgentRunRepository(session).add(agent, organization.id)
            await ToolCallRepository(session).add(call, organization.id)
            await AgentRunRepository(session).add(nullable_agent, organization.id)
            await AgentRunRepository(session).add(failed_agent, organization.id)

    async with get_business_session(session_factory) as session:
        stored_agent = (
            (
                await session.execute(
                    text(
                        "SELECT provider_metadata, usage FROM taskpilot.agent_runs WHERE id = :id"
                    ),
                    {"id": agent.id},
                )
            )
            .mappings()
            .one()
        )
        stored_call = (
            (
                await session.execute(
                    text(
                        "SELECT arguments, result, usage FROM taskpilot.tool_calls WHERE id = :id"
                    ),
                    {"id": call.id},
                )
            )
            .mappings()
            .one()
        )
        stored_nullable_agent = (
            (
                await session.execute(
                    text(
                        "SELECT provider_metadata, usage FROM taskpilot.agent_runs WHERE id = :id"
                    ),
                    {"id": nullable_agent.id},
                )
            )
            .mappings()
            .one()
        )
        stored_failed_agent = (
            (
                await session.execute(
                    text("SELECT error_message FROM taskpilot.agent_runs WHERE id = :id"),
                    {"id": failed_agent.id},
                )
            )
            .mappings()
            .one()
        )

    assert stored_agent["provider_metadata"] == {
        "provider": "fixture",
        "model": "fixture-model",
        "response_id": "api_key=[REDACTED]",
    }
    assert stored_agent["usage"] == {"status": "unavailable", "reason": "not_returned"}
    assert stored_nullable_agent["provider_metadata"] is None
    assert stored_nullable_agent["usage"] is None
    assert stored_failed_agent["error_message"] == "provider failed: api_key=[REDACTED]"
    assert "agent-error-secret" not in stored_failed_agent["error_message"]
    assert stored_call["arguments"] == {
        "api_key": "[REDACTED]",
        "nested": {"password": "[REDACTED]"},
        "authorization": "[REDACTED]",
    }
    assert stored_call["result"] == {
        "access_token": "[REDACTED]",
        "nested": {"dsn": "[REDACTED]"},
    }
    assert stored_call["usage"] == {"status": "unavailable", "reason": "unsupported"}

    async def assert_rejected(statement: str, parameters: dict[str, object]) -> None:
        with pytest.raises((DataError, IntegrityError)):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await session.execute(text(statement), parameters)

    # These inserts intentionally bypass ORM validation: they prove the
    # database constraints independently of the application sanitization path.
    agent_insert = (
        "INSERT INTO taskpilot.agent_runs "
        "(id, task_run_id, replan_count, step_position, retry_count, agent_name, "
        "status, error_message, started_at, finished_at, duration_ms, usage, provider_metadata) "
        "VALUES (:id, :task_run_id, :replan_count, :step_position, :retry_count, :agent_name, "
        ":status, :error_message, :started_at, :finished_at, :duration_ms, "
        "CAST(:usage_json AS jsonb), CAST(:metadata_json AS jsonb))"
    )
    tool_insert = (
        "INSERT INTO taskpilot.tool_calls "
        "(id, agent_run_id, call_index, tool_name, tool_version, status, error_message, "
        "started_at, finished_at, duration_ms, arguments, result, usage) "
        "VALUES (:id, :agent_run_id, :call_index, :tool_name, :tool_version, :status, "
        ":error_message, :started_at, :finished_at, :duration_ms, "
        "CAST(:arguments_json AS jsonb), CAST(:result_json AS jsonb), "
        "CAST(:usage_json AS jsonb))"
    )
    base_agent_parameters = {
        "task_run_id": task_run.id,
        "replan_count": 0,
        "step_position": 0,
        "retry_count": 0,
        "agent_name": "db-check",
        "status": "running",
        "error_message": None,
        "started_at": started_at,
        "finished_at": None,
        "duration_ms": None,
        "usage_json": None,
        "metadata_json": None,
    }
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "task_run_id": uuid4(),
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "step_position": 8,
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "replan_count": 2,
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "retry_count": 2,
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "agent_name": "   ",
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "finished_at": started_at - timedelta(milliseconds=1),
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "finished_at": started_at + timedelta(milliseconds=4),
            "duration_ms": 3,
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "finished_at": started_at + timedelta(days=1, milliseconds=1),
            "duration_ms": 86_400_001,
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "usage_json": json.dumps(
                {
                    "status": "known",
                    "input_tokens": 1_000_000_000_001,
                    "output_tokens": 0,
                    "total_tokens": 1_000_000_000_001,
                }
            ),
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "metadata_json": json.dumps({"provider": "x" * 2100}),
        },
    )
    await assert_rejected(
        agent_insert,
        {
            **base_agent_parameters,
            "id": uuid4(),
            "status": "succeeded",
            "error_message": "unexpected error",
        },
    )

    base_tool_parameters = {
        "agent_run_id": agent.id,
        "call_index": 10,
        "tool_name": "db-check",
        "tool_version": None,
        "status": "running",
        "error_message": None,
        "started_at": started_at,
        "finished_at": None,
        "duration_ms": None,
        "arguments_json": "{}",
        "result_json": None,
        "usage_json": None,
    }
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "agent_run_id": uuid4(),
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "tool_version": "v" * 65,
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "finished_at": started_at + timedelta(milliseconds=4),
            "duration_ms": 3,
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "arguments_json": "[]",
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "arguments_json": json.dumps({"value": "x" * 9000}),
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "result_json": "[]",
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "result_json": json.dumps({"value": "x" * 9000}),
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "usage_json": json.dumps(
                {
                    "status": "known",
                    "input_tokens": 1,
                    "output_tokens": 1_000_000_000_001,
                    "total_tokens": 1_000_000_000_002,
                }
            ),
        },
    )
    await assert_rejected(
        tool_insert,
        {
            **base_tool_parameters,
            "id": uuid4(),
            "status": "succeeded",
            "error_message": "unexpected error",
        },
    )
