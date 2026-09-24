"""Unit contracts for the T091/T092 observation persistence boundary."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import UniqueConstraint

from persistence.models import (
    AgentRun,
    AgentRunStatus,
    ObservabilityErrorClass,
    ToolCall,
    ToolCallStatus,
)
from persistence.repositories import AgentRunRepository, ToolCallRepository

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
TASK_RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
AGENT_RUN_ID = UUID("22222222-2222-4222-8222-222222222222")
ORGANIZATION_ID = UUID("33333333-3333-4333-8333-333333333333")


def test_observability_metadata_declares_normalized_ownership_and_uniqueness() -> None:
    agent_table = AgentRun.__table__
    tool_table = ToolCall.__table__
    assert set(agent_table.c.keys()) == {
        "id",
        "task_run_id",
        "request_id",
        "replan_count",
        "step_position",
        "retry_count",
        "agent_name",
        "status",
        "error_class",
        "error_code",
        "error_message",
        "started_at",
        "finished_at",
        "duration_ms",
        "usage",
        "provider_metadata",
        "created_at",
        "updated_at",
    }
    assert set(tool_table.c.keys()) == {
        "id",
        "agent_run_id",
        "call_index",
        "tool_name",
        "tool_version",
        "status",
        "started_at",
        "finished_at",
        "duration_ms",
        "arguments",
        "result",
        "error_class",
        "error_code",
        "error_message",
        "usage",
        "created_at",
        "updated_at",
    }
    assert {
        tuple(c.columns.keys()) for c in agent_table.constraints if isinstance(c, UniqueConstraint)
    } == {("task_run_id", "replan_count", "step_position", "retry_count", "agent_name")}
    assert {
        tuple(c.columns.keys()) for c in tool_table.constraints if isinstance(c, UniqueConstraint)
    } == {("agent_run_id", "call_index")}
    agent_fk = next(iter(agent_table.foreign_keys))
    tool_fk = next(iter(tool_table.foreign_keys))
    assert agent_fk.target_fullname == "taskpilot.task_runs.id"
    assert tool_fk.target_fullname == "taskpilot.agent_runs.id"
    assert agent_fk.ondelete == tool_fk.ondelete == "RESTRICT"
    assert not {"organization_id", "task_id"}.intersection(agent_table.c.keys())
    assert not {"organization_id", "task_id", "task_run_id"}.intersection(tool_table.c.keys())


def test_observability_models_bound_timing_usage_and_status() -> None:
    agent = AgentRun(
        task_run_id=TASK_RUN_ID,
        replan_count=1,
        step_position=7,
        retry_count=1,
        agent_name="planner",
        status=AgentRunStatus.FAILED,
        error_class=ObservabilityErrorClass.RETRY,
        error_code="deterministic_execution_failed",
        error_message="safe failure",
        started_at=NOW,
        finished_at=NOW + timedelta(milliseconds=12),
        usage={"status": "unavailable", "reason": "unsupported"},
        provider_metadata={"provider": "test", "model": "fixture"},
    )
    assert agent.duration_ms == 12
    assert agent.finished_at == NOW + timedelta(milliseconds=12)

    call = ToolCall(
        agent_run_id=AGENT_RUN_ID,
        call_index=0,
        tool_name="inspect",
        status=ToolCallStatus.SUCCEEDED,
        started_at=NOW,
        arguments={"api_key": "secret", "nested": [{"password": "pw"}]},
        result={"authorization": "Bearer secret"},
    )
    assert call.arguments == {
        "api_key": "[REDACTED]",
        "nested": [{"password": "[REDACTED]"}],
    }
    assert call.result == {"authorization": "[REDACTED]"}
    assert "secret" not in repr(call)

    with pytest.raises(ValueError, match="finished_at"):
        AgentRun(
            task_run_id=TASK_RUN_ID,
            replan_count=0,
            step_position=0,
            retry_count=0,
            agent_name="planner",
            started_at=NOW,
            finished_at=NOW - timedelta(seconds=1),
        )
    with pytest.raises(ValueError, match="8192"):
        ToolCall(
            agent_run_id=AGENT_RUN_ID,
            call_index=0,
            tool_name="inspect",
            started_at=NOW,
            arguments={"value": "x" * 9000},
        )
    with pytest.raises(ValueError, match="usage"):
        AgentRun(
            task_run_id=TASK_RUN_ID,
            replan_count=0,
            step_position=0,
            retry_count=0,
            agent_name="planner",
            started_at=NOW,
            usage={"status": "known", "input_tokens": -1, "output_tokens": 1, "total_tokens": 0},
        )
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(ValueError, match="JSON object"):
        ToolCall(
            agent_run_id=AGENT_RUN_ID,
            call_index=1,
            tool_name="inspect",
            started_at=NOW,
            arguments={"cycle": cyclic},
        )


@pytest.mark.asyncio
async def test_observability_repositories_flush_without_commit_and_keep_sql_scope() -> None:
    session = Mock()
    session.scalar = AsyncMock(return_value=object())
    session.flush = AsyncMock()
    agent_repository = AgentRunRepository(session)
    agent = AgentRun(
        task_run_id=TASK_RUN_ID,
        replan_count=0,
        step_position=0,
        retry_count=0,
        agent_name="planner",
        started_at=NOW,
    )
    assert await agent_repository.add(agent, ORGANIZATION_ID) is agent
    session.add.assert_called_once_with(agent)
    session.flush.assert_awaited_once_with()
    session.commit.assert_not_called()
    parent_sql = str(session.scalar.await_args.args[0].compile()).casefold()
    assert "join taskpilot.tasks" in parent_sql
    assert "taskpilot.tasks.organization_id" in parent_sql

    read_session = Mock()
    read_session.scalar = AsyncMock(return_value=None)
    read_session.scalars = AsyncMock(return_value=[])
    tool_repository = ToolCallRepository(read_session)
    assert await tool_repository.get_in_principal_tenant(uuid4(), ORGANIZATION_ID) is None
    read_sql = str(read_session.scalar.await_args.args[0].compile()).casefold()
    assert "join taskpilot.agent_runs" in read_sql
    assert "join taskpilot.task_runs" in read_sql
    assert "join taskpilot.tasks" in read_sql
    assert "taskpilot.tasks.organization_id" in read_sql
