"""HTTP wire contracts for the minimal TaskPilot authentication API."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)
    organization_id: UUID | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class OrganizationSelectionResponse(BaseModel):
    code: str
    organization_ids: list[UUID]


class SessionResponse(BaseModel):
    user_id: UUID
    membership_id: UUID
    organization_id: UUID
    role: str


__all__ = [
    "LoginRequest",
    "LoginResponse",
    "OrganizationSelectionResponse",
    "SessionResponse",
]
