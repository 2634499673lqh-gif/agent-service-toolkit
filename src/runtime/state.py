"""Checkpoint-safe state for the bounded TaskPilot runtime."""

import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from geochange.models import GeoChangeTask
from schema.planner import Plan

from .context import ContextEnvelope
from .executor import ExecutionResult, TrustedDynamicExecutionResult
from .failure import RuntimeFailure
from .planner import PlannerTaskInput
from .verifier import VerificationResult

TerminalOutcome = Literal["SUCCEEDED", "FAILED"]


class PendingApprovalReference(BaseModel):
    """Bounded checkpoint lookup data; it carries no decision authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: UUID
    replan_count: StrictInt = Field(ge=0, le=1)
    step_position: StrictInt = Field(ge=0)


class AgentState(BaseModel):
    """The complete JSON-safe state carried by one TaskRun checkpoint.

    This model intentionally contains runtime data only.  Tenant authority,
    sessions, ORM objects, repositories, principals, providers, and secrets
    stay outside the graph and are never checkpointed.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str
    task_run_id: str
    task_input: PlannerTaskInput
    geochange_task: GeoChangeTask | None = None
    geochange_aoi_evidence: dict[str, str] = Field(default_factory=dict, max_length=16)
    geochange_evidence: dict[str, str] = Field(default_factory=dict, max_length=32)
    plan: Plan | None = None
    plan_position: int = Field(default=0, ge=0)
    capability_context: ContextEnvelope | None = None
    execution_result: TrustedDynamicExecutionResult | ExecutionResult | None = None
    trusted_dynamic_evidence: dict[str, Any] | None = None
    verification: VerificationResult | None = None
    failure: RuntimeFailure | None = None
    retry_count: int = Field(default=0, ge=0)
    replan_count: int = Field(default=0, ge=0)
    pending_approval: PendingApprovalReference | None = None
    terminal_outcome: TerminalOutcome | None = None

    @field_validator("task_id", "task_run_id", mode="before")
    @classmethod
    def canonical_uuid_string(cls, value: object) -> str:
        """Store identifiers as canonical UUID strings, never UUID objects."""

        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ValueError("runtime identifiers must be canonical UUID strings") from None

    @field_validator("trusted_dynamic_evidence")
    @classmethod
    def bound_dynamic_evidence(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        try:
            serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("trusted dynamic evidence must be JSON-safe") from error
        if len(serialized.encode("utf-8")) > 16_384:
            raise ValueError("trusted dynamic evidence exceeds the bounded limit")
        return value

    @model_validator(mode="after")
    def dynamic_result_requires_binder(self) -> "AgentState":
        if isinstance(self.execution_result, TrustedDynamicExecutionResult):
            if self.trusted_dynamic_evidence != self.execution_result.canonical_evidence:
                raise ValueError("trusted dynamic checkpoint binder is missing or mismatched")
        return self

    @classmethod
    def initial(
        cls,
        *,
        task_id: UUID | str,
        task_run_id: UUID | str,
        title: str,
        description: str | None,
    ) -> "AgentState":
        """Create the only state shape allowed for a new graph execution."""

        return cls(
            task_id=task_id,
            task_run_id=task_run_id,
            task_input=PlannerTaskInput(title=title, description=description),
        )

    def checkpoint_data(self) -> dict[str, object]:
        """Return plain JSON values for LangGraph checkpoint writes."""

        return self.model_dump(mode="json")


__all__ = ["AgentState", "PendingApprovalReference", "TerminalOutcome"]
