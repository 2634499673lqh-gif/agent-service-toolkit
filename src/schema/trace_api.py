"""Pydantic contract for the tenant-scoped execution timeline."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TraceEventResponse(BaseModel):
    """Allowlisted observational fields for one AgentRun or ToolCall event."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_kind: Literal["agent_run", "tool_call"]
    task_id: UUID
    task_run_id: UUID
    request_id: UUID | None
    replan_count: int
    step_position: int
    retry_count: int
    approval_id: UUID | None
    agent_run_id: UUID
    agent_name: str
    tool_call_id: UUID | None
    call_index: int | None
    tool_name: str | None
    tool_version: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    error_class: str | None
    error_code: str | None
    error_message: str | None
    usage: dict[str, Any] | None
    estimate: dict[str, str] | None
    metadata: dict[str, Any] | None


__all__ = ["TraceEventResponse"]
