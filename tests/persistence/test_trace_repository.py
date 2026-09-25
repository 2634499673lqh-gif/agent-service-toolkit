"""T097 repository and response-boundary contracts."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from persistence.repositories import TraceRepository
from schema.trace_api import TraceEventResponse
from service.session import CurrentPrincipal
from service.trace_service import TraceService

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
ORG = UUID("33333333-3333-4333-8333-333333333333")


@pytest.mark.asyncio
async def test_trace_query_scopes_both_union_branches_and_caps_limit() -> None:
    session = Mock()
    result = Mock()
    result.mappings.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)

    await TraceRepository(session).list_for_task_run_in_principal_tenant(
        uuid4(), uuid4(), ORG, limit=999
    )

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect())).casefold()
    assert sql.count("taskpilot.tasks.organization_id") == 2
    assert sql.count("taskpilot.task_runs.id") >= 4
    assert "join taskpilot.agent_runs" in sql
    assert "from taskpilot.tool_calls" in sql
    assert "union all" in sql
    assert "event_kind_rank" in sql
    assert "nulls first" in sql
    assert statement._limit_clause.value == 500


def test_trace_response_redacts_again_and_excludes_payload_authority() -> None:
    service = TraceService(Mock())
    event_id = uuid4()
    row = {
        "event_id": event_id,
        "event_kind": "tool_call",
        "task_id": uuid4(),
        "task_run_id": uuid4(),
        "request_id": uuid4(),
        "replan_count": 1,
        "step_position": 0,
        "retry_count": 1,
        "approval_id": None,
        "agent_run_id": uuid4(),
        "agent_name": "executor",
        "call_index": 0,
        "tool_name": "fixture",
        "tool_version": "1",
        "status": "failed",
        "started_at": NOW,
        "finished_at": NOW,
        "duration_ms": 0,
        "error_class": "RETRY",
        "error_code": "retryable",
        "error_message": "Authorization: Bearer secret-value",
        "usage": {"status": "known", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
        "metadata": {
            "provider": "fixture",
            "response_id": "Bearer secret-value",
            "token": "secret-value",
            "prompt": "raw provider data",
        },
    }

    response = service._to_response(row)

    assert isinstance(response, TraceEventResponse)
    body = response.model_dump(mode="json")
    assert body["metadata"] == {
        "provider": "fixture",
        "response_id": "Bearer [REDACTED]",
    }
    assert "secret-value" not in str(body)
    assert "arguments" not in body
    assert "result" not in body
    assert "checkpoint" not in body
    assert "raw provider data" not in str(body)
    assert body["event_kind"] == "tool_call"
    assert body["tool_call_id"] == str(event_id)
    assert body["estimate"] == {"status": "unknown", "reason": "unsupported_model"}


@pytest.mark.asyncio
async def test_trace_service_returns_none_before_trace_query_for_invisible_run() -> None:
    service = TraceService(Mock())
    service.runs = Mock()
    service.runs.get_for_task_in_principal_tenant = AsyncMock(return_value=None)
    service.traces = Mock()
    service.traces.list_for_task_run_in_principal_tenant = AsyncMock()
    principal = CurrentPrincipal(
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=ORG,
        role="member",
        session_id=uuid4(),
    )

    result = await service.get_trace(principal, uuid4(), uuid4())

    assert result is None
    service.traces.list_for_task_run_in_principal_tenant.assert_not_awaited()
