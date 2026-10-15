"""Pydantic contracts for the T038 TaskRun API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from persistence.models import TaskRunStatus

TaskRunProgressPhaseStatus = Literal["pending", "running", "succeeded", "failed", "cancelled"]
TaskRunProgressState = Literal["pending", "running", "succeeded", "failed", "cancelled"]


class TaskRunProgressPhase(BaseModel):
    """Public status for one actual server execution phase."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["planning", "preparation", "computation", "validation", "finalization"]
    status: TaskRunProgressPhaseStatus


class TaskRunProgressResponse(BaseModel):
    """Bounded observational projection; never used to decide TaskRun success."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt_id: UUID
    attempt_number: int
    retry_count: int
    replan_count: int
    reset_reason: Literal["start", "resume", "retry", "replan"]
    state: TaskRunProgressState
    current_phase: (
        Literal["planning", "preparation", "computation", "validation", "finalization"] | None
    )
    completed_phase_count: int
    total_phase_count: Literal[5] = 5
    phase_completion_pct: int
    phases: list[TaskRunProgressPhase]
    last_event: str
    last_event_at: datetime


class TaskRunResponse(BaseModel):
    """Public persisted TaskRun representation without runtime metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    run_number: int
    status: TaskRunStatus
    created_at: datetime
    updated_at: datetime
    result_metadata: dict[str, object] | None = None
    progress: TaskRunProgressResponse | None = None


__all__ = [
    "TaskRunProgressPhase",
    "TaskRunProgressPhaseStatus",
    "TaskRunProgressResponse",
    "TaskRunProgressState",
    "TaskRunResponse",
]
