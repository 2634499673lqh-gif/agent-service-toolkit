"""Live PostgreSQL evidence for T097 trace visibility and reconstruction."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import (
    AgentRun,
    AgentRunStatus,
    Membership,
    ObservabilityErrorClass,
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
from persistence.passwords import hash_password
from service.auth_dependency import get_session_factory
from service.service import app
from service.session import AuthService

pytestmark = pytest.mark.postgres
PASSWORD = "T097-test-password"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
CAP_OBSERVATION_COUNT = 501


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_t097_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    isolated_url = base.set(database=name).render_as_string(hide_password=False)
    async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
        config.attributes["database_url"] = isolated_url
        await asyncio.to_thread(command.upgrade, config, "head")
        yield isolated_url
    finally:
        async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
            await connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


async def _issue_token(factory: async_sessionmaker[AsyncSession], email: str) -> str:
    async with factory() as session:
        async with session.begin():
            result = await AuthService(session).login(email, PASSWORD)
        assert hasattr(result, "token"), result
        return result.token  # type: ignore[union-attr]


@pytest_asyncio.fixture
async def trace_api_context() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], dict[str, str], dict[str, UUID]]
]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        organization = Organization(id=uuid4(), name="T097 own")
        foreign_organization = Organization(id=uuid4(), name="T097 foreign")
        member = User(
            id=uuid4(), email="t097-member@example.com", password_hash=hash_password(PASSWORD)
        )
        foreign_member = User(
            id=uuid4(),
            email="t097-foreign@example.com",
            password_hash=hash_password(PASSWORD),
        )
        task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=member.id,
            title="Trace task",
            status=TaskStatus.SUCCEEDED,
        )
        empty_task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=member.id,
            title="Empty trace task",
            status=TaskStatus.SUCCEEDED,
        )
        foreign_task = Task(
            id=uuid4(),
            organization_id=foreign_organization.id,
            created_by_user_id=foreign_member.id,
            title="Foreign trace task",
            status=TaskStatus.SUCCEEDED,
        )
        visible_run = TaskRun(
            id=uuid4(), task_id=task.id, run_number=1, status=TaskRunStatus.SUCCEEDED
        )
        empty_run = TaskRun(
            id=uuid4(), task_id=empty_task.id, run_number=1, status=TaskRunStatus.SUCCEEDED
        )
        foreign_run = TaskRun(
            id=uuid4(), task_id=foreign_task.id, run_number=1, status=TaskRunStatus.FAILED
        )
        retry_agent = AgentRun(
            id=UUID("11111111-1111-4111-8111-111111111111"),
            task_run_id=visible_run.id,
            request_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
            replan_count=0,
            step_position=0,
            retry_count=0,
            agent_name="executor",
            status=AgentRunStatus.FAILED,
            error_class=ObservabilityErrorClass.RETRY,
            error_code="fixture_retry",
            error_message="safe retry",
            started_at=NOW,
            finished_at=NOW + timedelta(milliseconds=1),
            usage={"status": "unavailable", "reason": "not_returned"},
            provider_metadata={"provider": "fixture", "model": "fixture", "version": "v1"},
        )
        retry_attempt = AgentRun(
            id=UUID("22222222-2222-4222-8222-222222222222"),
            task_run_id=visible_run.id,
            replan_count=0,
            step_position=0,
            retry_count=1,
            agent_name="executor",
            status=AgentRunStatus.SUCCEEDED,
            started_at=NOW,
            finished_at=NOW + timedelta(milliseconds=2),
            usage={"status": "known", "input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            provider_metadata={
                "provider": "fixture",
                "model": "fixture",
                "version": "v1",
                "response_id": "Bearer secret-value",
            },
        )
        replan_agent = AgentRun(
            id=UUID("33333333-3333-4333-8333-333333333333"),
            task_run_id=visible_run.id,
            replan_count=1,
            step_position=0,
            retry_count=0,
            agent_name="planner",
            status=AgentRunStatus.FAILED,
            error_class=ObservabilityErrorClass.REPLAN,
            error_code="fixture_replan",
            error_message="safe replan",
            started_at=NOW,
            finished_at=NOW + timedelta(milliseconds=3),
        )
        tool_call = ToolCall(
            id=UUID("44444444-4444-4444-8444-444444444444"),
            agent_run_id=retry_agent.id,
            call_index=0,
            tool_name="fixture_tool",
            tool_version="1",
            status=ToolCallStatus.FAILED,
            started_at=NOW,
            finished_at=NOW + timedelta(milliseconds=1),
            arguments={"api_key": "secret-value", "safe": "value"},
            result={"authorization": "Bearer secret-value"},
            error_class=ObservabilityErrorClass.RETRY,
            error_code="tool_retry",
            error_message="tool failed safely",
        )
        cap_agents = [
            AgentRun(
                id=UUID(f"50000000-0000-4000-8000-{index + 1:012x}"),
                task_run_id=visible_run.id,
                replan_count=0,
                step_position=0,
                retry_count=0,
                agent_name=f"cap-agent-{index:04d}",
                status=AgentRunStatus.SUCCEEDED,
                started_at=NOW + timedelta(seconds=10 + index),
                finished_at=NOW + timedelta(seconds=10 + index, milliseconds=1),
            )
            for index in range(CAP_OBSERVATION_COUNT)
        ]
        async with factory() as session:
            async with session.begin():
                session.add_all([organization, foreign_organization, member, foreign_member])
                await session.flush()
                session.add_all(
                    [
                        task,
                        empty_task,
                        foreign_task,
                        visible_run,
                        empty_run,
                        foreign_run,
                        Membership(
                            user_id=member.id, organization_id=organization.id, role=Role.MEMBER
                        ),
                        Membership(
                            user_id=foreign_member.id,
                            organization_id=foreign_organization.id,
                            role=Role.MEMBER,
                        ),
                    ]
                )
                await session.flush()
                session.add_all([retry_agent, retry_attempt, replan_agent, tool_call, *cap_agents])

        tokens = {
            "member": await _issue_token(factory, member.email),
            "foreign": await _issue_token(factory, foreign_member.email),
        }
        ids = {
            "task": task.id,
            "run": visible_run.id,
            "empty_task": empty_task.id,
            "empty_run": empty_run.id,
            "foreign_task": foreign_task.id,
            "foreign_run": foreign_run.id,
        }
        app.dependency_overrides[get_session_factory] = lambda: factory
        try:
            yield factory, tokens, ids
        finally:
            app.dependency_overrides.pop(get_session_factory, None)
            await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_trace_is_tenant_safe_bounded_ordered_and_reconstructable(trace_api_context) -> None:
    _, tokens, ids = trace_api_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        unauthenticated = await client.get(f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"

        response = await client.get(
            f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace",
            headers=_headers(tokens["member"]),
        )
        repeated = await client.get(
            f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace?limit=500",
            headers=_headers(tokens["member"]),
        )
        assert response.status_code == repeated.status_code == 200
        assert len(response.json()) == 100
        capped = await client.get(
            f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace?limit=501",
            headers=_headers(tokens["member"]),
        )
        assert capped.status_code == 200
        assert capped.json() == repeated.json()
        body = capped.json()
        assert len(body) == 500
        assert len([item for item in body if item["agent_name"].startswith("cap-agent-")]) == 496
        assert any(item["agent_name"] == "cap-agent-0495" for item in body)
        assert not any(item["agent_name"] == "cap-agent-0496" for item in body)
        assert {item["task_id"] for item in body} == {str(ids["task"])}
        assert {item["task_run_id"] for item in body} == {str(ids["run"])}

        def ordering_key(item: dict[str, object]) -> tuple[object, ...]:
            return (
                datetime.fromisoformat(str(item["started_at"])),
                0 if item["event_kind"] == "agent_run" else 1,
                UUID(str(item["agent_run_id"])),
                -1 if item["call_index"] is None else int(item["call_index"]),
                UUID(str(item["event_id"])),
            )

        assert [ordering_key(item) for item in body] == sorted(map(ordering_key, body))
        assert [item["event_kind"] for item in body] == [
            "agent_run",
            "agent_run",
            "agent_run",
            "tool_call",
        ] + ["agent_run"] * 496
        assert {(item["replan_count"], item["retry_count"]) for item in body} >= {
            (0, 0),
            (0, 1),
            (1, 0),
        }
        assert {item["error_class"] for item in body if item["error_class"]} >= {
            "RETRY",
            "REPLAN",
        }
        assert all("arguments" not in item and "result" not in item for item in body)
        assert all("secret-value" not in str(item) for item in body)
        assert body[1]["metadata"]["response_id"] == "Bearer [REDACTED]"
        assert body[0]["usage"] == {"status": "unavailable", "reason": "not_returned"}
        assert body[0]["estimate"] == {"status": "unknown", "reason": "usage_unavailable"}
        assert body[1]["estimate"] == {"status": "unknown", "reason": "unsupported_model"}

        limited = await client.get(
            f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace?limit=1",
            headers=_headers(tokens["member"]),
        )
        assert limited.status_code == 200
        assert len(limited.json()) == 1
        assert (
            await client.get(
                f"/api/v1/tasks/{ids['task']}/runs/{ids['run']}/trace?limit=0",
                headers=_headers(tokens["member"]),
            )
        ).status_code == 422
        empty = await client.get(
            f"/api/v1/tasks/{ids['empty_task']}/runs/{ids['empty_run']}/trace",
            headers=_headers(tokens["member"]),
        )
        foreign = await client.get(
            f"/api/v1/tasks/{ids['foreign_task']}/runs/{ids['foreign_run']}/trace",
            headers=_headers(tokens["member"]),
        )
        assert empty.status_code == 200 and empty.json() == []
        assert foreign.status_code == 404 and foreign.json() == {"detail": "Not Found"}
