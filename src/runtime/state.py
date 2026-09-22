"""Checkpoint-safe state for the bounded TaskPilot runtime."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from schema.planner import Plan

from .context import ContextEnvelope
from .executor import ExecutionResult
from .failure import RuntimeFailure
from .planner import PlannerTaskInput
from .verifier import VerificationResult

TerminalOutcome = Literal["SUCCEEDED", "FAILED"]


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
    plan: Plan | None = None
    plan_position: int = Field(default=0, ge=0)
    capability_context: ContextEnvelope | None = None
    execution_result: ExecutionResult | None = None
    verification: VerificationResult | None = None
    failure: RuntimeFailure | None = None
    retry_count: int = Field(default=0, ge=0)
    replan_count: int = Field(default=0, ge=0)
    terminal_outcome: TerminalOutcome | None = None

    @field_validator("task_id", "task_run_id", mode="before")
    @classmethod
    def canonical_uuid_string(cls, value: object) -> str:
        """Store identifiers as canonical UUID strings, never UUID objects."""

        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ValueError("runtime identifiers must be canonical UUID strings") from None

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


__all__ = ["AgentState", "TerminalOutcome"]
