"""Pydantic contracts for the T038 TaskRun API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from persistence.models import TaskRunStatus


class TaskRunResponse(BaseModel):
    """Public persisted TaskRun representation without runtime metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    task_id: UUID
    run_number: int
    status: TaskRunStatus
    created_at: datetime
    updated_at: datetime


__all__ = ["TaskRunResponse"]
