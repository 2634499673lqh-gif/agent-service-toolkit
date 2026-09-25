import asyncio

import pytest
from pydantic import ValidationError

from evaluation import (
    ComparisonStatus,
    build_machine_report,
    compare_reports,
    render_human_report,
    run_evaluation,
    serialize_report,
)
from evaluation.report import ComparisonReason, ComparisonResult, ReportSizeExceeded
from evaluation.smoke import SMOKE_CASE_IDS, run_smoke, smoke_exit_code


def test_machine_report_is_canonical_and_versioned() -> None:
    report = build_machine_report(asyncio.run(run_evaluation()))
    artifact = serialize_report(report)
    assert report.model_dump(mode="json", by_alias=True)["schema"] == "taskpilot.eval.report/v1"
    assert artifact == serialize_report(report)
    assert artifact.endswith(b"\n") and not artifact.endswith(b"\n\n")
    assert list(report.metrics) == [
        "pass_rate",
        "recovery_success",
        "approval_compliance",
        "evidence_completeness",
    ]


def test_incomparable_comparison_has_reason_and_no_deltas() -> None:
    report = build_machine_report(asyncio.run(run_evaluation()))
    changed = report.model_copy(update={"suite_id": "other.suite"})
    comparison = compare_reports(report, changed)
    assert comparison.status is ComparisonStatus.INCOMPARABLE
    assert comparison.reason is ComparisonReason.SUITE_ID_MISMATCH
    assert "delta" not in report.model_dump(mode="json", by_alias=True)


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ComparisonStatus.NOT_REQUESTED, None),
        (ComparisonStatus.COMPARABLE, None),
        (ComparisonStatus.INCOMPARABLE, ComparisonReason.SUITE_ID_MISMATCH),
        (ComparisonStatus.INCOMPARABLE, ComparisonReason.SUITE_VERSION_MISMATCH),
        (ComparisonStatus.INCOMPARABLE, ComparisonReason.CASE_SET_MISMATCH),
        (ComparisonStatus.INCOMPARABLE, ComparisonReason.CASE_VERSION_MISMATCH),
    ],
)
def test_comparison_state_reason_pairs_are_frozen(status, reason) -> None:
    assert ComparisonResult(status=status, reason=reason).reason == reason


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (ComparisonStatus.COMPARABLE, ComparisonReason.SUITE_ID_MISMATCH),
        (ComparisonStatus.NOT_REQUESTED, ComparisonReason.CASE_SET_MISMATCH),
        (ComparisonStatus.INCOMPARABLE, None),
        (ComparisonStatus.INCOMPARABLE, "unsupported_reason"),
    ],
)
def test_comparison_state_reason_mismatches_are_rejected(status, reason) -> None:
    with pytest.raises(ValidationError):
        ComparisonResult(status=status, reason=reason)


def test_each_frozen_comparison_mismatch_is_detected() -> None:
    report = build_machine_report(asyncio.run(run_evaluation()))
    assert (
        compare_reports(report, report.model_copy(update={"suite_id": "other"})).reason
        is ComparisonReason.SUITE_ID_MISMATCH
    )
    assert (
        compare_reports(report, report.model_copy(update={"suite_version": 2})).reason
        is ComparisonReason.SUITE_VERSION_MISMATCH
    )
    reduced = report.model_copy(update={"cases": report.cases[:-1]})
    assert compare_reports(report, reduced).reason is ComparisonReason.CASE_SET_MISMATCH
    changed_case = report.cases[0].model_copy(update={"case_version": 2})
    versioned = report.model_copy(update={"cases": (changed_case, *report.cases[1:])})
    assert compare_reports(report, versioned).reason is ComparisonReason.CASE_VERSION_MISMATCH


def test_report_size_bound_returns_frozen_terminal_code(monkeypatch) -> None:
    import evaluation.report as report_module

    report = build_machine_report(asyncio.run(run_evaluation()))
    monkeypatch.setattr(report_module, "MAX_REPORT_BYTES", 1)
    try:
        serialize_report(report)
    except ReportSizeExceeded as error:
        assert str(error) == "report_size_exceeded"
    else:
        raise AssertionError("expected bounded report-size failure")


def test_human_report_is_a_projection_of_machine_report() -> None:
    report = build_machine_report(asyncio.run(run_evaluation()))
    human = render_human_report(report)
    assert "**PASS**" in human and "not_requested" in human
    assert "1.0000" in human
    assert human == render_human_report(report.model_dump(mode="json", by_alias=True))


def test_ci_smoke_is_exact_three_case_subset_and_passes() -> None:
    run, report = asyncio.run(run_smoke())
    assert tuple(case.case_id for case in run.cases) == tuple(sorted(SMOKE_CASE_IDS))
    assert smoke_exit_code(run, report) == 0
    assert report.comparison.status is ComparisonStatus.NOT_REQUESTED


@pytest.mark.parametrize("runner_status", ["error", "partial"])
def test_smoke_failure_status_returns_non_zero(runner_status) -> None:
    run, report = asyncio.run(run_smoke())
    failed_run = run.model_copy(update={"runner_status": runner_status})
    assert smoke_exit_code(failed_run, report) != 0


def test_smoke_non_pass_case_returns_non_zero() -> None:
    run, report = asyncio.run(run_smoke())
    failed_case = run.cases[0].model_copy(
        update={"status": "fail", "failure_code": "assertion_failed"}
    )
    failed_run = run.model_copy(update={"cases": (failed_case, *run.cases[1:])})
    assert smoke_exit_code(failed_run, report) != 0


def test_ci_invokes_the_real_smoke_entry_point() -> None:
    workflow = (
        __import__("pathlib").Path(__file__).parents[2] / ".github" / "workflows" / "test.yml"
    ).read_text(encoding="utf-8")
    assert "uv run python scripts/evaluation_smoke.py > evaluation-smoke.json" in workflow
