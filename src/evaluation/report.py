"""Canonical machine-readable reports for Phase 8 Evaluation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from .fixtures import SUITE_ID, SUITE_VERSION
from .metrics import MetricResult, compute_metrics
from .runner import CaseResult, EvaluationRun, RunnerStatus

REPORT_SCHEMA = "taskpilot.eval.report/v1"
MAX_REPORT_BYTES = 32 * 1024
_RATE_RE = re.compile(r"^\d+\.\d{4}$")


class ComparisonStatus(StrEnum):
    NOT_REQUESTED = "not_requested"
    COMPARABLE = "comparable"
    INCOMPARABLE = "incomparable"


class ComparisonReason(StrEnum):
    SUITE_ID_MISMATCH = "suite_id_mismatch"
    SUITE_VERSION_MISMATCH = "suite_version_mismatch"
    CASE_SET_MISMATCH = "case_set_mismatch"
    CASE_VERSION_MISMATCH = "case_version_mismatch"


class ComparisonResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: ComparisonStatus
    reason: ComparisonReason | None = None

    @model_validator(mode="after")
    def validate_status_reason(self) -> ComparisonResult:
        if self.status in {ComparisonStatus.NOT_REQUESTED, ComparisonStatus.COMPARABLE}:
            if self.reason is not None:
                raise ValueError(f"{self.status.value} comparison must not have a reason")
        elif self.reason is None:
            raise ValueError("incomparable comparison requires a frozen mismatch reason")
        return self


class MachineReport(BaseModel):
    """The complete bounded report object; no runtime payloads are retained."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    schema_: StrictStr = Field(REPORT_SCHEMA, alias="schema")
    suite_id: StrictStr = SUITE_ID
    suite_version: StrictInt = SUITE_VERSION
    runner_status: RunnerStatus
    cases: tuple[CaseResult, ...] = Field(max_length=5)
    metrics: dict[str, MetricResult]
    comparison: ComparisonResult

    @field_validator("metrics")
    @classmethod
    def exact_metrics(cls, value: dict[str, MetricResult]) -> dict[str, MetricResult]:
        expected = {"pass_rate", "recovery_success", "approval_compliance", "evidence_completeness"}
        if set(value) != expected:
            raise ValueError("report must contain exactly the four frozen metrics")
        for metric in value.values():
            if metric.rate is not None and not _RATE_RE.fullmatch(metric.rate):
                raise ValueError("metric rates must use four decimal places")
        return value

    @field_validator("cases")
    @classmethod
    def canonical_cases(cls, value: tuple[CaseResult, ...]) -> tuple[CaseResult, ...]:
        ordered = tuple(sorted(value, key=lambda c: (c.case_id, c.case_version)))
        if value != ordered or len({(c.case_id, c.case_version) for c in value}) != len(value):
            raise ValueError("cases must be unique and canonically ordered")
        return value

    @classmethod
    def from_run(
        cls, run: EvaluationRun, *, comparison: ComparisonResult | None = None
    ) -> MachineReport:
        if run.suite_id != SUITE_ID or run.suite_version != SUITE_VERSION:
            # The V1 report is still bounded, but preserves the run identity for comparison.
            return cls.model_validate(
                {
                    "schema": REPORT_SCHEMA,
                    "suite_id": run.suite_id,
                    "suite_version": run.suite_version,
                    "runner_status": run.runner_status,
                    "cases": run.cases,
                    "metrics": compute_metrics(run.cases),
                    "comparison": comparison
                    or ComparisonResult(status=ComparisonStatus.NOT_REQUESTED),
                }
            )
        return cls(
            runner_status=run.runner_status,
            cases=run.cases,
            metrics=compute_metrics(run.cases),
            comparison=comparison or ComparisonResult(status=ComparisonStatus.NOT_REQUESTED),
        )


class ReportSizeExceeded(ValueError):
    """The canonical artifact exceeded the frozen 32 KiB bound."""

    code = "report_size_exceeded"


def _as_report(value: MachineReport | Mapping[str, Any]) -> MachineReport:
    return value if isinstance(value, MachineReport) else MachineReport.model_validate(value)


def compare_reports(
    current: MachineReport | Mapping[str, Any],
    previous: MachineReport | Mapping[str, Any],
) -> ComparisonResult:
    """Compare only frozen suite/case identity; no cross-run metric deltas are emitted."""
    current_report = _as_report(current)
    previous_report = _as_report(previous)
    if current_report.suite_id != previous_report.suite_id:
        reason = ComparisonReason.SUITE_ID_MISMATCH
    elif current_report.suite_version != previous_report.suite_version:
        reason = ComparisonReason.SUITE_VERSION_MISMATCH
    else:
        current_ids = {case.case_id for case in current_report.cases}
        previous_ids = {case.case_id for case in previous_report.cases}
        if current_ids != previous_ids:
            reason = ComparisonReason.CASE_SET_MISMATCH
        else:
            current_versions = {case.case_id: case.case_version for case in current_report.cases}
            previous_versions = {case.case_id: case.case_version for case in previous_report.cases}
            if current_versions != previous_versions:
                reason = ComparisonReason.CASE_VERSION_MISMATCH
            else:
                return ComparisonResult(status=ComparisonStatus.COMPARABLE)
    return ComparisonResult(status=ComparisonStatus.INCOMPARABLE, reason=reason)


def build_machine_report(
    run: EvaluationRun, *, compare_to: MachineReport | Mapping[str, Any] | None = None
) -> MachineReport:
    comparison = (
        ComparisonResult(status=ComparisonStatus.NOT_REQUESTED)
        if compare_to is None
        else compare_reports(MachineReport.from_run(run), compare_to)
    )
    return MachineReport.from_run(run, comparison=comparison)


def serialize_report(report: MachineReport | Mapping[str, Any]) -> bytes:
    """Return canonical UTF-8 JSON bytes with exactly one trailing newline."""
    value = _as_report(report).model_dump(mode="json", by_alias=True)
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_REPORT_BYTES:
        raise ReportSizeExceeded("report_size_exceeded")
    return encoded


# Friendly aliases for callers at the CLI/test boundary.
render_machine_report = serialize_report
report_bytes = serialize_report

__all__ = [
    "REPORT_SCHEMA",
    "MAX_REPORT_BYTES",
    "ComparisonReason",
    "ComparisonResult",
    "ComparisonStatus",
    "MachineReport",
    "ReportSizeExceeded",
    "build_machine_report",
    "compare_reports",
    "report_bytes",
    "render_machine_report",
    "serialize_report",
]
