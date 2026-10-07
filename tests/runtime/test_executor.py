import json

import pytest
from pydantic import ValidationError

from runtime import ExecutionResult, Executor, PlannerTaskInput
from runtime.executor import TrustedDynamicExecutionResult
from runtime.graph import _observation_result
from runtime.state import AgentState
from schema import PlanStep


def test_accepts_valid_success_result() -> None:
    result = ExecutionResult(step_position=1, success=True, output="completed")

    assert result.output == "completed"
    assert result.error_code is None
    assert result.error_message is None


def test_accepts_valid_failure_result() -> None:
    result = ExecutionResult(
        step_position=1,
        success=False,
        error_code="execution_failed",
        error_message="The deterministic operation failed.",
    )

    assert result.output is None
    assert result.error_code == "execution_failed"


@pytest.mark.parametrize("step_position", [0, -1])
def test_rejects_non_positive_step_position(step_position: int) -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(step_position=step_position, success=True, output="completed")


def test_rejects_success_without_output() -> None:
    with pytest.raises(ValidationError, match="requires output"):
        ExecutionResult(step_position=1, success=True)


def test_rejects_success_with_error_code() -> None:
    with pytest.raises(ValidationError, match="must not contain error_code"):
        ExecutionResult(
            step_position=1,
            success=True,
            output="completed",
            error_code="unexpected_error",
        )


def test_rejects_success_with_error_message() -> None:
    with pytest.raises(ValidationError, match="must not contain error_message"):
        ExecutionResult(
            step_position=1,
            success=True,
            output="completed",
            error_message="unexpected message",
        )


def test_rejects_failure_without_error_code() -> None:
    with pytest.raises(ValidationError, match="requires error_code"):
        ExecutionResult(step_position=1, success=False)


def test_rejects_failure_with_output() -> None:
    with pytest.raises(ValidationError, match="must not contain output"):
        ExecutionResult(
            step_position=1,
            success=False,
            output="not allowed",
            error_code="execution_failed",
        )


def test_rejects_blank_error_code() -> None:
    with pytest.raises(ValidationError, match="error_code must not be blank"):
        ExecutionResult(step_position=1, success=False, error_code="   ")


def test_rejects_overlong_output() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(step_position=1, success=True, output="x" * 2001)


def test_trusted_dynamic_result_has_explicit_larger_bound() -> None:
    result = TrustedDynamicExecutionResult(
        step_position=1,
        success=True,
        output="x" * 8000,
        trusted_dynamic=True,
    )
    assert len(result.output or "") == 8000
    with pytest.raises(ValidationError):
        TrustedDynamicExecutionResult(
            step_position=1,
            success=True,
            output="x" * 8193,
            trusted_dynamic=True,
        )


def test_dynamic_marker_is_required_when_rehydrating() -> None:
    with pytest.raises(ValidationError):
        TrustedDynamicExecutionResult(
            step_position=1,
            success=True,
            output="x" * 2001,
        )


def test_dynamic_checkpoint_requires_the_server_binder() -> None:
    result = TrustedDynamicExecutionResult(
        step_position=1,
        success=True,
        output="{}",
        trusted_dynamic=True,
        canonical_evidence={"metrics": {"common": 1}},
    )
    with pytest.raises(ValidationError, match="binder"):
        AgentState(
            task_id="11111111-1111-4111-8111-111111111111",
            task_run_id="22222222-2222-4222-8222-222222222222",
            task_input=PlannerTaskInput(title="dynamic", description=None),
            execution_result=result,
        )


def test_dynamic_observation_does_not_duplicate_terminal_binder() -> None:
    result = TrustedDynamicExecutionResult(
        step_position=4,
        success=True,
        output="x" * 8000,
        trusted_dynamic=True,
        canonical_evidence={"provenance": "x" * 8000},
    )
    observation = _observation_result(result)
    assert observation == {
        "step_position": 4,
        "success": True,
        "output": "[server-owned dynamic terminal result]",
    }


def test_rejects_overlong_error_code() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(step_position=1, success=False, error_code="x" * 65)


def test_rejects_overlong_error_message() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            step_position=1, success=False, error_code="failed", error_message="x" * 501
        )


def test_rejects_unexpected_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ExecutionResult(
            step_position=1,
            success=True,
            output="completed",
            organization_id="org-1",
        )


def test_execution_result_json_round_trip() -> None:
    result = ExecutionResult(
        step_position=1,
        success=False,
        error_code="execution_failed",
        error_message="The operation failed.",
    )

    payload = result.model_dump(mode="json")
    restored = ExecutionResult.model_validate_json(json.dumps(payload))

    assert payload == {
        "step_position": 1,
        "success": False,
        "output": None,
        "error_code": "execution_failed",
        "error_message": "The operation failed.",
    }
    assert restored == result


@pytest.mark.asyncio
async def test_minimal_fake_satisfies_executor_interface_with_approved_inputs() -> None:
    class FakeExecutor:
        async def execute(self, step: PlanStep, task_input: PlannerTaskInput) -> ExecutionResult:
            self.step = step
            self.task_input = task_input
            return ExecutionResult(step_position=step.position, success=True, output="fake")

    executor: Executor = FakeExecutor()
    step = PlanStep(position=1, instruction="Inspect the task")
    task_input = PlannerTaskInput(title="Prepare the report", description=None)

    result = await executor.execute(step, task_input)

    assert result.step_position == step.position
    assert result.success is True
    assert step.model_dump() == {"position": 1, "instruction": "Inspect the task"}
    assert task_input.model_dump() == {"title": "Prepare the report", "description": None}
