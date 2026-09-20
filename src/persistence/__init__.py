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
    AuthSession,
    Membership,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    User,
)
from persistence.repositories import (
    AuthSessionRepository,
    MembershipRepository,
    OrganizationRepository,
    TaskRepository,
    TaskRunRepository,
    UserRepository,
)

__all__ = [
    "AuthSession",
    "AuthSessionRepository",
    "Base",
    "Membership",
    "MembershipRepository",
    "Organization",
    "OrganizationRepository",
    "Role",
    "Task",
    "TaskRepository",
    "TaskRun",
    "TaskRunRepository",
    "TaskRunStatus",
    "TaskStatus",
    "User",
    "UserRepository",
    "canonicalize_email",
    "create_async_engine",
    "create_session_factory",
    "get_business_session",
    "normalize_business_database_url",
]
