"""Pydantic contracts for the T036 Task API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from persistence.models import TaskStatus


class TaskCreateRequest(BaseModel):
    """Client-controlled Task fields; tenant and creator are not accepted."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


class TaskResponse(BaseModel):
    """Public persisted Task representation; TaskRun data is intentionally absent."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    created_by_user_id: UUID
    title: str
    description: str | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime


__all__ = ["TaskCreateRequest", "TaskResponse"]
