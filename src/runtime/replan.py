"""Bounded replacement-plan transition for Phase 4 T049."""

from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.planner import Plan
from schema.verifier import VerificationResult

from .executor import ExecutionResult
from .failure import FailureClassification, RuntimeFailure

REPLAN_BUDGET = 1
ReplanCount = Annotated[int, Field(ge=0, le=REPLAN_BUDGET)]


class ReplanDecision(BaseModel):
    """Checkpoint-safe result of consuming the single replan budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    classification: FailureClassification
    replan_count: ReplanCount
    replan_allowed: bool

    @model_validator(mode="after")
    def replan_allowed_matches_classification(self) -> "ReplanDecision":
        if self.replan_allowed != (self.classification == "REPLAN"):
            raise ValueError("replan_allowed must match the effective classification")
        return self


class ReplanState(BaseModel):
    """Serializable state slice changed by an accepted replacement Plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan: Plan | None = None
    plan_position: int = Field(default=0, ge=0)
    execution_result: ExecutionResult | None = None
    verification: VerificationResult | None = None
    failure: RuntimeFailure | None = None
    retry_count: int = Field(default=0, ge=0)
    replan_count: ReplanCount = 0


def consume_replan(failure: RuntimeFailure, replan_count: int) -> ReplanDecision:
    """Consume the one replan budget without reclassifying the failure."""

    if not 0 <= replan_count <= REPLAN_BUDGET:
        raise ValueError(f"replan_count must be between 0 and {REPLAN_BUDGET}")

    if failure.classification == "REPLAN" and replan_count < REPLAN_BUDGET:
        return ReplanDecision(
            classification="REPLAN",
            replan_count=replan_count + 1,
            replan_allowed=True,
        )

    if failure.classification == "REPLAN":
        return ReplanDecision(
            classification="TERMINAL",
            replan_count=replan_count,
            replan_allowed=False,
        )

    return ReplanDecision(
        classification=failure.classification,
        replan_count=replan_count,
        replan_allowed=False,
    )


def apply_replacement_plan(
    state: ReplanState,
    replacement_plan: Plan | Mapping[str, Any],
    decision: ReplanDecision,
) -> ReplanState:
    """Apply an already-authorized budget decision and validated replacement Plan."""

    if not decision.replan_allowed:
        raise ValueError("a replacement Plan requires an allowed REPLAN decision")
    if decision.replan_count != state.replan_count + 1:
        raise ValueError("replacement Plan must consume exactly one replan count")

    validated_plan = Plan.model_validate(replacement_plan)
    return ReplanState(
        plan=validated_plan,
        plan_position=0,
        execution_result=None,
        verification=None,
        failure=None,
        retry_count=state.retry_count,
        replan_count=decision.replan_count,
    )


__all__ = [
    "REPLAN_BUDGET",
    "ReplanDecision",
    "ReplanState",
    "apply_replacement_plan",
    "consume_replan",
]
