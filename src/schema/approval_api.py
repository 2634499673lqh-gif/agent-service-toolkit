"""Pydantic contracts for the T082 Approval HTTP surface."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from persistence.models import ApprovalRiskLevel, ApprovalStatus


class ApprovalDecisionRequest(BaseModel):
    """Optional human reason; actor, tenant, role, and proposal are server-owned."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class ApprovalResponse(BaseModel):
    """Sanitized Approval fields needed to inspect and decide the proposal."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    task_run_id: UUID
    replan_count: int
    step_position: int
    action_name: str
    action_version: str
    proposed_action: dict[str, Any]
    risk_level: ApprovalRiskLevel
    status: ApprovalStatus
    requester_membership_id: UUID
    decider_membership_id: UUID | None
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime


__all__ = ["ApprovalDecisionRequest", "ApprovalResponse"]
