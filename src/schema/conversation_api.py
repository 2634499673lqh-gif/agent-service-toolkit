"""Contracts for the product-facing conversational TaskPilot entry point."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from geochange.models import Period


class TaskProposal(BaseModel):
    """Server-readable proposal; it is not an execution command."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    analysis_area: str | None = Field(default=None, max_length=255)
    analysis_type: str = Field(default="vegetation_change", max_length=64)
    indicator: str = Field(default="NDVI", max_length=64)
    period_a: Period | None = None
    period_b: Period | None = None
    required_parameters: dict[str, str] = Field(default_factory=dict)

    @field_validator("title", "description", "analysis_type", "indicator")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proposal text must not be blank")
        return value.strip()


class ConversationRequest(BaseModel):
    # Task descriptions are bounded to the same supported size.  Keeping the
    # boundary here prevents a valid conversation request from becoming an
    # unhandled persistence error later in the flow.
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class ConfirmTaskRequest(BaseModel):
    proposal: TaskProposal


class ConversationTaskSummary(BaseModel):
    task_id: UUID
    title: str
    analysis_type: str | None = None
    status: str
    created_at: datetime
    runs: list[dict[str, object]] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    kind: str
    message: str
    proposal: TaskProposal | None = None
    tasks: list[ConversationTaskSummary] = Field(default_factory=list)
    result: dict[str, object] | None = None
    missing_fields: list[str] = Field(default_factory=list)


__all__ = [
    "ConfirmTaskRequest",
    "ConversationRequest",
    "ConversationResponse",
    "ConversationTaskSummary",
    "TaskProposal",
]
