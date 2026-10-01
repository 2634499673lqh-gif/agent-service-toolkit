from uuid import uuid4

from runtime.executor import ExecutionResult
from runtime.planner import PlannerTaskInput
from runtime.state import AgentState
from runtime.verifier import VerificationResult
from schema.planner import Plan
from service.task_runtime import _terminal_stage_status


def _state(**updates):
    state = AgentState(
        task_id=uuid4(),
        task_run_id=uuid4(),
        task_input=PlannerTaskInput(title="Task"),
        plan=Plan(steps=[{"position": 1, "instruction": "Inspect"}]),
    )
    return state.model_copy(update=updates)


def test_terminal_stage_status_distinguishes_planner_and_execution_failures():
    planner_failed = _state(plan=None)
    assert _terminal_stage_status(planner_failed) == {
        "planner": "failed",
        "execution": "not_run",
        "verifier": "not_run",
    }
    execution_failed = _state(
        execution_result=ExecutionResult(step_position=1, success=False, error_code="failed")
    )
    assert _terminal_stage_status(execution_failed) == {
        "planner": "passed",
        "execution": "failed",
        "verifier": "not_run",
    }


def test_terminal_stage_status_reports_verifier_rejection_and_success():
    rejected = _state(
        execution_result=ExecutionResult(step_position=1, success=True, output="ok"),
        verification=VerificationResult(verdict="FAIL", reason="insufficient", evidence=[]),
    )
    assert _terminal_stage_status(rejected) == {
        "planner": "passed",
        "execution": "passed",
        "verifier": "failed",
    }
    passed = rejected.model_copy(
        update={"verification": VerificationResult(verdict="PASS", reason="valid", evidence=[])}
    )
    assert _terminal_stage_status(passed) == {
        "planner": "passed",
        "execution": "passed",
        "verifier": "passed",
    }
