"""Deterministic T094/T095 observability normalization coverage."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from persistence.models import AgentRun, AgentRunStatus, TaskRunStatus, ToolCall, ToolCallStatus
from runtime import (
    ExecutionResult,
    RuntimeObservation,
    normalize_provider_usage,
    unavailable_usage,
)

TASK_RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
AGENT_RUN_ID = UUID("44444444-4444-4444-8444-444444444444")
START = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def _observation(**overrides: object) -> RuntimeObservation:
    values: dict[str, object] = {
        "task_run_id": str(TASK_RUN_ID),
        "replan_count": 0,
        "step_position": 0,
        "retry_count": 0,
        "agent_name": "executor",
        "agent_status": "succeeded",
        "tool_name": "fixture",
        "call_index": 0,
        "tool_status": "succeeded",
        "started_at": START,
        "finished_at": START + timedelta(milliseconds=12),
    }
    values.update(overrides)
    return RuntimeObservation.model_validate(values)


def test_t094_normalizes_utc_and_derives_bounded_duration() -> None:
    observation = _observation(
        started_at=datetime(2026, 9, 24, 18, 0, tzinfo=timezone(timedelta(hours=8))),
        finished_at=datetime(2026, 9, 24, 18, 0, 0, 5000, tzinfo=timezone(timedelta(hours=8))),
    )
    assert observation.started_at.tzinfo is UTC
    assert observation.finished_at is not None and observation.finished_at.tzinfo is UTC
    assert observation.duration_ms == 5


@pytest.mark.parametrize(
    "overrides",
    [
        {"started_at": datetime(2026, 9, 24, 10, 0)},
        {"finished_at": START - timedelta(seconds=1)},
        {"finished_at": START + timedelta(days=1, milliseconds=1)},
        {"error_message": "x" * 501},
        {"error_message": "traceback (most recent call last): secret"},
    ],
)
def test_t094_rejects_malformed_timing_and_unsanitized_errors(overrides: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        _observation(**overrides)


def test_t094_missing_finish_keeps_duration_unknown() -> None:
    observation = _observation(finished_at=None)
    assert observation.duration_ms is None


def test_t094_retry_and_replan_coordinates_are_observational_only() -> None:
    retry = _observation(retry_count=1, agent_status="failed", tool_status="failed")
    replan = _observation(replan_count=1, retry_count=0, step_position=0)
    assert (retry.replan_count, retry.step_position, retry.retry_count) == (0, 0, 1)
    assert (replan.replan_count, replan.step_position, replan.retry_count) == (1, 0, 0)
    # The runtime observation never carries a TaskRun lifecycle value.
    assert not hasattr(retry, "task_run_status")
    assert TaskRunStatus.SUCCEEDED.value not in retry.model_dump().values()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            {
                "input_tokens": 123,
                "output_tokens": 45,
                "total_tokens": 168,
                "cached_input_tokens": 0,
            },
            {
                "status": "known",
                "input_tokens": 123,
                "output_tokens": 45,
                "total_tokens": 168,
                "cached_input_tokens": 0,
            },
        ),
        (
            {"prompt_tokens": 123, "completion_tokens": 45},
            {"status": "known", "input_tokens": 123, "output_tokens": 45, "total_tokens": 168},
        ),
        (None, {"status": "unavailable", "reason": "not_returned"}),
        ({"input_tokens": 1}, {"status": "unavailable", "reason": "malformed"}),
        (
            {"input_tokens": "1", "output_tokens": 2},
            {"status": "unavailable", "reason": "malformed"},
        ),
        (
            {"input_tokens": -1, "output_tokens": 2},
            {"status": "unavailable", "reason": "malformed"},
        ),
        (
            {"input_tokens": 1_000_000_000_001, "output_tokens": 0},
            {"status": "unavailable", "reason": "malformed"},
        ),
    ],
)
def test_t095_provider_usage_is_truthful(raw: object, expected: dict[str, object]) -> None:
    assert normalize_provider_usage(raw) == expected


def test_t095_unsupported_provider_is_explicit_and_never_zero() -> None:
    value = normalize_provider_usage({"input_tokens": 0, "output_tokens": 0}, supported=False)
    assert value == unavailable_usage("unsupported")
    assert "input_tokens" not in value and "output_tokens" not in value


@pytest.mark.parametrize(
    "credential_text",
    [
        "Authorization: Bearer provider-secret",
        "api_key=provider-secret",
        "token=provider-secret",
    ],
)
def test_t095_rejects_credential_bearing_provider_metadata_before_checkpoint(
    credential_text: str,
) -> None:
    with pytest.raises(ValidationError, match="credential-bearing"):
        ExecutionResult(
            step_position=1,
            success=True,
            output="safe-output",
            provider_metadata={"response_id": credential_text},
        )
    with pytest.raises(ValidationError, match="credential-bearing"):
        _observation(provider_metadata={"response_id": credential_text})


def test_t095_safe_provider_metadata_is_unchanged_and_missing_usage_is_explicit() -> None:
    result = ExecutionResult(
        step_position=1,
        success=True,
        output="safe-output",
        provider_metadata={"provider": "fixture", "model": "fixture-v1"},
    )
    assert result.provider_metadata == {"provider": "fixture", "model": "fixture-v1"}
    assert result.usage == {"status": "unavailable", "reason": "not_returned"}
    assert result.model_dump(mode="json")["usage"] == {
        "status": "unavailable",
        "reason": "not_returned",
    }


def test_t095_persisted_models_accept_known_and_unavailable_shapes() -> None:
    agent = AgentRun(
        task_run_id=TASK_RUN_ID,
        replan_count=0,
        step_position=0,
        retry_count=0,
        agent_name="executor",
        status=AgentRunStatus.SUCCEEDED,
        started_at=START,
        usage=normalize_provider_usage({"input_tokens": 1, "output_tokens": 2}),
    )
    call = ToolCall(
        agent_run_id=AGENT_RUN_ID,
        call_index=0,
        tool_name="fixture",
        status=ToolCallStatus.SUCCEEDED,
        started_at=START,
        arguments={},
        usage=normalize_provider_usage(None),
    )
    assert agent.usage == {
        "status": "known",
        "input_tokens": 1,
        "output_tokens": 2,
        "total_tokens": 3,
    }
    assert call.usage == {"status": "unavailable", "reason": "not_returned"}
