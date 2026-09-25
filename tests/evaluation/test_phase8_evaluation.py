from math import inf, nan

import pytest
from pydantic import ValidationError

from evaluation import (
    CASE_IDS,
    CaseResult,
    EvaluationRunner,
    MetricResult,
    approval_compliance,
    compute_metrics,
    evidence_completeness,
    load_fixtures,
    pass_rate,
    recovery_success,
)
from evaluation import runner as runner_module


def test_v1_fixture_set_is_exactly_five_data_only_cases() -> None:
    suite = load_fixtures()

    assert tuple(case.case_id for case in suite.cases) == CASE_IDS
    assert [case.case_version for case in suite.cases] == [1] * 5
    assert all(case.model_dump(mode="json") for case in suite.cases)
    assert suite.cases[2].expected_output == "deterministic-read-only-fixture:v1"
    assert suite.cases[2].capability_name == "deterministic_fixture"
    assert suite.cases[2].expected_step == {
        "position": 1,
        "instruction": "Return the deterministic fixture.",
    }
    assert suite.cases[2].required_evidence == (
        "capability_selected",
        "execution_succeeded",
        "output_exact",
        "verification_passed",
    )


def test_fixture_rejects_runtime_objects_and_secret_shaped_values() -> None:
    with pytest.raises(ValidationError):
        load_fixtures().cases[2].__class__(
            case_id="unsafe",
            case_version=1,
            input={"session": object()},
            required_evidence=(),
            expected_step={"position": 1, "instruction": "x"},
            tag="baseline",
        )


@pytest.mark.parametrize("non_finite", [nan, inf, -inf])
def test_fixture_rejects_nested_non_finite_json_values(non_finite: float) -> None:
    with pytest.raises(ValidationError):
        load_fixtures().cases[2].__class__(
            case_id="non-finite",
            case_version=1,
            input={"nested": [{"value": non_finite}]},
            required_evidence=(),
            expected_step={"position": 1, "instruction": "x"},
            tag="baseline",
        )


@pytest.mark.asyncio
async def test_runner_executes_all_cases_in_canonical_order_and_repeats() -> None:
    first = await EvaluationRunner().run()
    second = await EvaluationRunner().run()

    assert first == second
    assert first.runner_status == "completed"
    assert [case.case_id for case in first.cases] == list(CASE_IDS)
    assert all(case.status == "pass" for case in first.cases)
    assert [case.failure_code for case in first.cases] == [None] * 5
    metrics = compute_metrics(first.cases)
    assert {name: metric.rate for name, metric in metrics.items()} == {
        "pass_rate": "1.0000",
        "recovery_success": "1.0000",
        "approval_compliance": "1.0000",
        "evidence_completeness": "1.0000",
    }
    assert first.cases[4].evidence_codes == ("recovery_pass", "retry_observed")
    assert first.cases[3].evidence_codes == ("recovery_pass", "replan_observed")


@pytest.mark.asyncio
async def test_case_error_is_isolated_and_marks_run_partial(monkeypatch) -> None:
    runner = EvaluationRunner()
    original = runner._run_case

    async def fail_one_case(fixture):
        if fixture.case_id == "deterministic.fixture_baseline":
            raise RuntimeError("untrusted provider-shaped exception text")
        return await original(fixture)

    monkeypatch.setattr(runner, "_run_case", fail_one_case)

    result = await runner.run()

    assert result.runner_status == "partial"
    assert len(result.cases) == 5
    failed = result.cases[2]
    assert failed.case_id == "deterministic.fixture_baseline"
    assert failed.status == "error"
    assert failed.failure_code == "case_error"
    assert failed.evidence_codes == ()
    assert all(case.status == "pass" for case in result.cases if case is not failed)


@pytest.mark.asyncio
async def test_empty_or_invalid_suite_produces_runner_error() -> None:
    invalid_suite = load_fixtures().model_copy(update={"cases": ()})

    result = await EvaluationRunner(invalid_suite).run()

    assert result.runner_status == "error"
    assert result.cases == ()


@pytest.mark.asyncio
async def test_real_runner_fail_path_is_assertion_failed() -> None:
    suite = load_fixtures()
    baseline = suite.cases[2].model_copy(update={"expected_output": "wrong-output"})
    mutated = suite.model_copy(update={"cases": (*suite.cases[:2], baseline, *suite.cases[3:])})

    result = await EvaluationRunner(mutated).run()
    failed = result.cases[2]

    assert result.runner_status == "completed"
    assert failed.status == "fail"
    assert failed.failure_code == "assertion_failed"


@pytest.mark.asyncio
async def test_l2_pending_boundary_requires_explicit_signal(monkeypatch) -> None:
    runner = EvaluationRunner()
    l2 = load_fixtures().cases[0]
    seen_counts: list[int] = []

    def no_signal(fixture, state, capability):
        seen_counts.append(capability.executions)
        return None

    monkeypatch.setattr(runner, "_post_approval_signal", no_signal)

    result = await runner._approval(l2, "L2")

    assert seen_counts == [0]
    assert result.status == "fail"
    assert result.evidence_codes == ("approval_required",)
    assert "approval_satisfied" not in result.evidence_codes


@pytest.mark.asyncio
async def test_l2_signal_executes_once_and_validates_exact_output(monkeypatch) -> None:
    counts: list[int] = []
    outputs: list[str | None] = []

    class RecordingCapability(runner_module._RiskFixtureCapability):
        async def execute(self, step, context):
            counts.append(self.executions)
            result = await super().execute(step, context)
            outputs.append(result.output)
            counts.append(self.executions)
            return result

    monkeypatch.setattr(runner_module, "_RiskFixtureCapability", RecordingCapability)
    runner = EvaluationRunner()
    l2 = load_fixtures().cases[0]

    result = await runner._approval(l2, "L2")

    assert result.status == "pass"
    assert result.evidence_codes == (
        "approval_required",
        "approval_satisfied",
        "execution_succeeded",
    )
    assert counts == [0, 1]
    assert outputs == [l2.expected_output]


@pytest.mark.asyncio
async def test_l3_is_blocked_before_capability_execution() -> None:
    result = await EvaluationRunner()._approval(load_fixtures().cases[1], "L3")

    assert result.status == "pass"
    assert result.evidence_codes == ("blocked_before_execution",)


def test_metrics_cover_rounding_and_zero_applicability() -> None:
    results = (
        CaseResult(
            case_id="deterministic.fixture_baseline",
            case_version=1,
            status="pass",
            evidence_codes=(
                "capability_selected",
                "execution_succeeded",
                "output_exact",
                "verification_passed",
            ),
        ),
        CaseResult(
            case_id="recovery.retry_then_pass",
            case_version=1,
            status="fail",
            evidence_codes=("retry_observed",),
            failure_code="assertion_failed",
        ),
        CaseResult(
            case_id="recovery.replan_then_pass",
            case_version=1,
            status="error",
            evidence_codes=(),
            failure_code="case_error",
        ),
    )

    assert pass_rate(results).model_dump() == {
        "status": "ok",
        "numerator": 1,
        "denominator": 3,
        "rate": "0.3333",
    }
    assert recovery_success(results).model_dump() == {
        "status": "ok",
        "numerator": 0,
        "denominator": 2,
        "rate": "0.0000",
    }
    assert approval_compliance(results) == MetricResult(
        status="not_applicable", numerator=0, denominator=0, rate=None
    )
    assert evidence_completeness(results).model_dump() == {
        "status": "ok",
        "numerator": 1,
        "denominator": 3,
        "rate": "0.3333",
    }
    assert compute_metrics(())["pass_rate"].status == "not_applicable"


def test_case_result_rejects_invalid_failure_code_pair() -> None:
    with pytest.raises(ValueError):
        CaseResult(
            case_id="x",
            case_version=1,
            status="pass",
            evidence_codes=(),
            failure_code="assertion_failed",
        )
