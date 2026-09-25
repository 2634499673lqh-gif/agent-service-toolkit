"""Pure deterministic metrics for one Evaluation run."""

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from .fixtures import DEFAULT_FIXTURES, EvaluationFixture, FixtureSuite
from .runner import CaseResult

_QUANTUM = Decimal("0.0001")


class MetricResult(BaseModel):
    """The frozen metric representation, including explicit applicability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["ok", "not_applicable"]
    numerator: StrictInt = Field(ge=0)
    denominator: StrictInt = Field(ge=0)
    rate: str | None = None

    @model_validator(mode="after")
    def validate_metric(self) -> "MetricResult":
        if self.numerator > self.denominator:
            raise ValueError("metric numerator cannot exceed denominator")
        if self.denominator == 0:
            if self.status != "not_applicable" or self.numerator != 0 or self.rate is not None:
                raise ValueError("zero denominator must be not_applicable with a null rate")
        elif self.status != "ok" or self.rate is None or len(self.rate) != 6:
            raise ValueError("an applicable metric must have a four-place rate")
        return self


def pass_rate(results: Iterable[CaseResult]) -> MetricResult:
    """Count every produced result, including failures and errors."""

    values = tuple(results)
    return _rate(sum(result.status == "pass" for result in values), len(values))


def recovery_success(
    results: Iterable[CaseResult],
    suite: FixtureSuite | None = None,
) -> MetricResult:
    """Measure only executed retry/replan cases."""

    contracts = _contracts(suite)
    applicable = tuple(
        result
        for result in results
        if contracts.get(result.case_id) is not None
        and contracts[result.case_id].tag in {"retry-then-pass", "replan-then-pass"}
    )
    numerator = sum(
        result.status == "pass" and _has_required_evidence(result, contracts[result.case_id])
        for result in applicable
    )
    return _rate(numerator, len(applicable))


def approval_compliance(
    results: Iterable[CaseResult],
    suite: FixtureSuite | None = None,
) -> MetricResult:
    """Measure exact L2 approval and L3 blocked behavior."""

    contracts = _contracts(suite)
    applicable = tuple(
        result
        for result in results
        if contracts.get(result.case_id) is not None
        and contracts[result.case_id].tag in {"l2-approval", "l3-blocked"}
    )
    numerator = sum(
        result.status == "pass" and _has_required_evidence(result, contracts[result.case_id])
        for result in applicable
    )
    return _rate(numerator, len(applicable))


def evidence_completeness(
    results: Iterable[CaseResult],
    suite: FixtureSuite | None = None,
) -> MetricResult:
    """Measure presence of every contract-required evidence code.

    Evidence completeness is independent of final business outcome, so a
    failed case can still be complete if it recorded all required evidence.
    Errors remain in the denominator as required by ADR-010.
    """

    contracts = _contracts(suite)
    applicable = tuple(
        result
        for result in results
        if contracts.get(result.case_id) is not None and contracts[result.case_id].required_evidence
    )
    numerator = sum(
        _has_required_evidence(result, contracts[result.case_id]) for result in applicable
    )
    return _rate(numerator, len(applicable))


def compute_metrics(
    results: Sequence[CaseResult],
    suite: FixtureSuite | None = None,
) -> dict[str, MetricResult]:
    """Return exactly the four per-run metrics in stable name order."""

    return {
        "pass_rate": pass_rate(results),
        "recovery_success": recovery_success(results, suite),
        "approval_compliance": approval_compliance(results, suite),
        "evidence_completeness": evidence_completeness(results, suite),
    }


def _contracts(suite: FixtureSuite | None) -> Mapping[str, EvaluationFixture]:
    selected = suite or DEFAULT_FIXTURES
    return {fixture.case_id: fixture for fixture in selected.cases}


def _has_required_evidence(result: CaseResult, fixture: EvaluationFixture) -> bool:
    return set(fixture.required_evidence).issubset(result.evidence_codes)


def _rate(numerator: int, denominator: int) -> MetricResult:
    if denominator == 0:
        return MetricResult(status="not_applicable", numerator=0, denominator=0, rate=None)
    quotient = (Decimal(numerator) / Decimal(denominator)).quantize(
        _QUANTUM, rounding=ROUND_HALF_UP
    )
    return MetricResult(
        status="ok",
        numerator=numerator,
        denominator=denominator,
        rate=f"{quotient:.4f}",
    )


__all__ = [
    "MetricResult",
    "approval_compliance",
    "compute_metrics",
    "evidence_completeness",
    "pass_rate",
    "recovery_success",
]
