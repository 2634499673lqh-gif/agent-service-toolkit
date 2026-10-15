"""Resume reconstruction only trusts validated checkpoint and server binder facts."""

from types import SimpleNamespace
from uuid import uuid4

import geochange.artifacts as artifacts
from geochange.artifacts import write_dynamic_evidence
from runtime.executor import TrustedDynamicExecutionResult
from runtime.failure import RuntimeFailure
from schema.planner import Plan, PlanStep
from schema.verifier import VerificationResult
from service.task_runtime import _progress_completed_from_checkpoint


def _state(task_id, run_id, *, evidence, result=None, verification=None, failure=None):
    plan = Plan(steps=[PlanStep(position=1, instruction="summarize_change")])
    return SimpleNamespace(
        plan=plan,
        plan_position=0,
        execution_result=result,
        trusted_dynamic_evidence=evidence,
        verification=verification,
        failure=failure,
    )


def _dynamic_result(evidence, *, step_position=1):
    return TrustedDynamicExecutionResult(
        step_position=step_position,
        success=True,
        output="{}",
        trusted_dynamic=True,
        canonical_evidence=evidence,
    )


def test_resume_reconstructs_only_checkpoint_work_bound_to_server_dynamic_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", tmp_path)
    task_id, run_id = uuid4(), uuid4()
    evidence = {"result_digest": "server-created"}
    write_dynamic_evidence(str(task_id), str(run_id), evidence)
    verification = VerificationResult(verdict="PASS", reason="valid", evidence=[])
    state = _state(
        task_id,
        run_id,
        evidence=evidence,
        result=_dynamic_result(evidence),
        verification=verification,
    )

    assert _progress_completed_from_checkpoint(state, task_id=task_id, task_run_id=run_id) == (
        "planning",
        "preparation",
        "computation",
        "validation",
    )

    no_verifier = _state(task_id, run_id, evidence=evidence, result=_dynamic_result(evidence))
    assert _progress_completed_from_checkpoint(
        no_verifier, task_id=task_id, task_run_id=run_id
    ) == ("planning", "preparation", "computation")

    forged = _state(
        task_id,
        run_id,
        evidence={"result_digest": "forged"},
        result=_dynamic_result({"result_digest": "forged"}),
        verification=verification,
    )
    assert _progress_completed_from_checkpoint(forged, task_id=task_id, task_run_id=run_id) == (
        "planning",
    )

    wrong_step = _state(
        task_id,
        run_id,
        evidence=evidence,
        result=_dynamic_result(evidence, step_position=2),
        verification=verification,
    )
    assert _progress_completed_from_checkpoint(wrong_step, task_id=task_id, task_run_id=run_id) == (
        "planning",
    )


def test_resume_does_not_mark_uncheckpointed_or_failed_operations_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", tmp_path)
    task_id, run_id = uuid4(), uuid4()
    evidence = {"result_digest": "server-created"}
    write_dynamic_evidence(str(task_id), str(run_id), evidence)
    verification = VerificationResult(verdict="PASS", reason="valid", evidence=[])

    missing_binder = _state(
        task_id,
        run_id,
        evidence=evidence,
        result=_dynamic_result(evidence),
        verification=verification,
    )
    (tmp_path / str(task_id) / str(run_id) / ".dynamic_evidence.json").unlink()
    assert _progress_completed_from_checkpoint(
        missing_binder, task_id=task_id, task_run_id=run_id
    ) == ("planning",)

    failed = _state(
        task_id,
        run_id,
        evidence=evidence,
        result=_dynamic_result(evidence),
        verification=verification,
        failure=RuntimeFailure(
            classification="TERMINAL", code="test_failure", sanitized_message="failed"
        ),
    )
    assert _progress_completed_from_checkpoint(failed, task_id=task_id, task_run_id=run_id) == (
        "planning",
    )

    empty = SimpleNamespace(plan=None)
    assert _progress_completed_from_checkpoint(empty, task_id=task_id, task_run_id=run_id) == ()
