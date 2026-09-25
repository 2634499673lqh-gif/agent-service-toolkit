"""TaskPilot business persistence boundary.

This package owns only the PostgreSQL ``taskpilot`` schema.  LangGraph's
checkpoint and Store adapters remain in :mod:`memory` and are deliberately
not imported into this metadata or migration boundary.
"""

from persistence.base import Base
from persistence.engine import (
    create_async_engine,
    create_session_factory,
    get_business_session,
    normalize_business_database_url,
)
from persistence.identity import canonicalize_email
from persistence.models import (
    AgentRun,
    AgentRunErrorClass,
    AgentRunStatus,
    AuthSession,
    Membership,
    ObservabilityErrorClass,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    ToolCall,
    ToolCallErrorClass,
    ToolCallStatus,
    User,
)
from persistence.repositories import (
    AgentRunRepository,
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    TaskRepository,
    TaskRunRepository,
    ToolCallRepository,
    UserRepository,
)

__all__ = [
    "AuthSession",
    "AuthSessionRepository",
    "AgentRun",
    "AgentRunErrorClass",
    "AgentRunRepository",
    "AgentRunStatus",
    "Base",
    "Membership",
    "MembershipRepository",
    "ObservabilityErrorClass",
    "Organization",
    "OrganizationRepository",
    "Role",
    "Task",
    "TaskRepository",
    "TaskRun",
    "TaskRunRepository",
    "TaskRunStatus",
    "TaskStatus",
    "ToolCall",
    "ToolCallErrorClass",
    "ToolCallRepository",
    "ToolCallStatus",
    "User",
    "UserRepository",
    "canonicalize_email",
    "create_async_engine",
    "create_session_factory",
    "get_business_session",
    "normalize_business_database_url",
]
