"""Deterministic, in-memory Evaluation primitives for Phase 8."""

from .fixtures import (
    CASE_IDS,
    SUITE_ID,
    SUITE_VERSION,
    EvaluationFixture,
    FixtureSuite,
    load_fixtures,
)
from .metrics import (
    MetricResult,
    approval_compliance,
    compute_metrics,
    evidence_completeness,
    pass_rate,
    recovery_success,
)
from .runner import (
    CaseResult,
    EvaluationRun,
    EvaluationRunner,
    FixtureApprovalSignal,
    RunnerStatus,
    run_evaluation,
)

__all__ = [
    "CASE_IDS",
    "SUITE_ID",
    "SUITE_VERSION",
    "EvaluationFixture",
    "FixtureSuite",
    "load_fixtures",
    "CaseResult",
    "EvaluationRun",
    "EvaluationRunner",
    "FixtureApprovalSignal",
    "RunnerStatus",
    "run_evaluation",
    "MetricResult",
    "pass_rate",
    "recovery_success",
    "approval_compliance",
    "evidence_completeness",
    "compute_metrics",
    "REPORT_SCHEMA",
    "MAX_REPORT_BYTES",
    "ComparisonReason",
    "ComparisonResult",
    "ComparisonStatus",
    "MachineReport",
    "ReportSizeExceeded",
    "build_machine_report",
    "compare_reports",
    "serialize_report",
    "render_machine_report",
    "report_bytes",
    "render_human_report",
    "render_report",
    "SMOKE_CASE_IDS",
    "run_smoke",
    "smoke_exit_code",
    "smoke_suite",
]

from .human import render_human_report, render_report
from .report import (
    MAX_REPORT_BYTES,
    REPORT_SCHEMA,
    ComparisonReason,
    ComparisonResult,
    ComparisonStatus,
    MachineReport,
    ReportSizeExceeded,
    build_machine_report,
    compare_reports,
    render_machine_report,
    report_bytes,
    serialize_report,
)
from .smoke import SMOKE_CASE_IDS, run_smoke, smoke_exit_code, smoke_suite
