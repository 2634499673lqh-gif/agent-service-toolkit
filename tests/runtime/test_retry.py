import json

import pytest
from pydantic import ValidationError

from runtime import (
    RETRY_BUDGET,
    DeterministicExecutor,
    FailureClassifier,
    RetryDecision,
    consume_retry,
)
from runtime.planner import PlannerTaskInput
from schema import PlanStep

STEP = PlanStep(position=1, instruction="Inspect the supplied task")
TASK_INPUT = PlannerTaskInput(title="Prepare the report", description="Use the task details.")


def test_first_retry_transitions_from_zero_to_one_before_executor_reentry() -> None:
    failure = FailureClassifier().classify("deterministic_execution_failed")

    decision = consume_retry(failure, retry_count=0)

    assert RETRY_BUDGET == 1
    assert decision.classification == "RETRY"
    assert decision.retry_allowed is True
    assert decision.retry_count == 1


def test_second_retry_is_denied_and_becomes_terminal_without_increment() -> None:
    failure = FailureClassifier().classify("deterministic_execution_failed")

    decision = consume_retry(failure, retry_count=1)

    assert decision.classification == "TERMINAL"
    assert decision.retry_allowed is False
    assert decision.retry_count == 1


@pytest.mark.asyncio
async def test_fail_once_executor_allows_one_additional_same_step_attempt() -> None:
    executor = DeterministicExecutor(failure_mode="fail_once")
    first = await executor.execute(STEP, TASK_INPUT)
    failure = FailureClassifier().classify(first.error_code or "unknown", first.error_message)
    decision = consume_retry(failure, retry_count=0)
    assert decision.retry_allowed is True
    second = await executor.execute(STEP, TASK_INPUT)

    assert first.success is False
    assert second.success is True
    assert second.step_position == STEP.position


@pytest.mark.parametrize("classification", ["REPLAN", "TERMINAL"])
def test_non_retry_classifications_do_not_consume_budget(classification: str) -> None:
    failure = FailureClassifier().classify(
        "recoverable_plan_inadequacy" if classification == "REPLAN" else "unknown"
    )

    decision = consume_retry(failure, retry_count=0)

    assert decision.classification == classification
    assert decision.retry_allowed is False
    assert decision.retry_count == 0


def test_retry_count_is_monotonic_and_not_reset() -> None:
    failure = FailureClassifier().classify("deterministic_execution_failed")

    first = consume_retry(failure, retry_count=0)
    exhausted = consume_retry(failure, retry_count=first.retry_count)

    assert first.retry_count == 1
    assert exhausted.retry_count == first.retry_count
    assert exhausted.classification == "TERMINAL"


def test_retry_decision_is_json_checkpoint_safe() -> None:
    failure = FailureClassifier().classify("deterministic_execution_failed")
    decision = consume_retry(failure, retry_count=0)

    payload = decision.model_dump(mode="json")
    restored = RetryDecision.model_validate_json(json.dumps(payload))

    assert payload == {
        "classification": "RETRY",
        "retry_count": 1,
        "retry_allowed": True,
    }
    assert restored == decision


@pytest.mark.parametrize("retry_count", [-1, 2])
def test_retry_decision_rejects_invalid_counter_bounds(retry_count: int) -> None:
    failure = FailureClassifier().classify("deterministic_execution_failed")

    with pytest.raises(ValueError):
        consume_retry(failure, retry_count)

    with pytest.raises(ValidationError):
        RetryDecision(
            classification="TERMINAL",
            retry_count=retry_count,
            retry_allowed=False,
        )


def test_retry_decision_rejects_inconsistent_route() -> None:
    with pytest.raises(ValidationError):
        RetryDecision(classification="TERMINAL", retry_count=1, retry_allowed=True)
