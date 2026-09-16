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
from persistence.models import Membership, Organization, Role, User
from persistence.repositories import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)

__all__ = [
    "Base",
    "Membership",
    "MembershipRepository",
    "Organization",
    "OrganizationRepository",
    "Role",
    "User",
    "UserRepository",
    "canonicalize_email",
    "create_async_engine",
    "create_session_factory",
    "get_business_session",
    "normalize_business_database_url",
]
