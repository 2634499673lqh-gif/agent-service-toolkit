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
]
