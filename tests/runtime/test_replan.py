import json

import pytest
from pydantic import ValidationError

from runtime import (
    REPLAN_BUDGET,
    ReplanDecision,
    ReplanState,
    apply_replacement_plan,
    consume_replan,
)
from runtime.executor import ExecutionResult
from runtime.failure import FailureClassifier, RuntimeFailure
from schema import Plan, PlanStep, VerificationResult

OLD_PLAN = Plan(steps=[PlanStep(position=1, instruction="Inspect the original plan")])
REPLACEMENT_PLAN = {"steps": [{"position": 1, "instruction": "Use the replacement plan"}]}


def _replan_failure() -> RuntimeFailure:
    return FailureClassifier().classify("recoverable_plan_inadequacy")


def test_first_replan_consumes_budget_before_replacement_transition() -> None:
    decision = consume_replan(_replan_failure(), replan_count=0)

    assert REPLAN_BUDGET == 1
    assert decision.classification == "REPLAN"
    assert decision.replan_allowed is True
    assert decision.replan_count == 1


def test_second_replan_is_denied_terminal_without_increment() -> None:
    decision = consume_replan(_replan_failure(), replan_count=1)

    assert decision.classification == "TERMINAL"
    assert decision.replan_allowed is False
    assert decision.replan_count == 1


@pytest.mark.parametrize("classification", ["RETRY", "TERMINAL"])
def test_non_replan_classifications_do_not_consume_budget(classification: str) -> None:
    code = "deterministic_execution_failed" if classification == "RETRY" else "unknown"
    decision = consume_replan(FailureClassifier().classify(code), replan_count=0)

    assert decision.classification == classification
    assert decision.replan_allowed is False
    assert decision.replan_count == 0


def test_replacement_plan_resets_per_plan_state_and_preserves_counters() -> None:
    failure = _replan_failure()
    state = ReplanState(
        plan=OLD_PLAN,
        plan_position=1,
        execution_result=ExecutionResult(
            step_position=1,
            success=False,
            error_code="deterministic_execution_failed",
        ),
        verification=VerificationResult(
            verdict="FAIL",
            reason="The original plan was insufficient.",
            evidence=["Original evidence"],
        ),
        failure=failure,
        retry_count=1,
        replan_count=0,
    )
    decision = consume_replan(failure, state.replan_count)

    replanned = apply_replacement_plan(state, REPLACEMENT_PLAN, decision)

    assert replanned.plan == Plan.model_validate(REPLACEMENT_PLAN)
    assert replanned.plan_position == 0
    assert replanned.execution_result is None
    assert replanned.verification is None
    assert replanned.failure is None
    assert replanned.retry_count == 1
    assert replanned.replan_count == 1


def test_malformed_replacement_plan_is_rejected_before_state_application() -> None:
    state = ReplanState(plan=OLD_PLAN, retry_count=1)
    decision = consume_replan(_replan_failure(), state.replan_count)

    with pytest.raises(ValidationError):
        apply_replacement_plan(
            state,
            {"steps": [{"position": 2, "instruction": "Invalid ordering"}]},
            decision,
        )

    assert state.plan == OLD_PLAN
    assert state.replan_count == 0


def test_exhausted_decision_cannot_apply_a_replacement_plan() -> None:
    state = ReplanState(plan=OLD_PLAN, retry_count=1, replan_count=1)
    decision = consume_replan(_replan_failure(), state.replan_count)

    with pytest.raises(ValueError, match="allowed REPLAN"):
        apply_replacement_plan(state, REPLACEMENT_PLAN, decision)

    assert decision.classification == "TERMINAL"
    assert decision.replan_count == 1
    assert state.plan == OLD_PLAN


def test_replan_state_and_decision_are_json_checkpoint_safe() -> None:
    state = ReplanState(plan=OLD_PLAN, retry_count=1, replan_count=1)
    decision = consume_replan(_replan_failure(), replan_count=0)

    state_restored = ReplanState.model_validate_json(json.dumps(state.model_dump(mode="json")))
    decision_restored = ReplanDecision.model_validate_json(
        json.dumps(decision.model_dump(mode="json"))
    )

    assert state_restored == state
    assert decision_restored == decision


@pytest.mark.parametrize("replan_count", [-1, 2])
def test_replan_counter_is_bounded(replan_count: int) -> None:
    with pytest.raises(ValueError):
        consume_replan(_replan_failure(), replan_count)

    with pytest.raises(ValidationError):
        ReplanDecision(
            classification="TERMINAL",
            replan_count=replan_count,
            replan_allowed=False,
        )


def test_replan_decision_rejects_inconsistent_route() -> None:
    with pytest.raises(ValidationError):
        ReplanDecision(classification="TERMINAL", replan_count=1, replan_allowed=True)
