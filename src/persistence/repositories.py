"""Small repositories for TaskPilot business models."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.identity import canonicalize_email
from persistence.models import AuthSession, Membership, Organization, User


class OrganizationRepository:
    """Persistence operations for organizations; transaction ownership stays above."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, organization: Organization) -> Organization:
        """Stage and flush an organization without committing its transaction."""

        self.session.add(organization)
        await self.session.flush()
        return organization

    async def get(self, organization_id: UUID) -> Organization | None:
        """Load one organization by its tenant identifier."""

        return await self.session.get(Organization, organization_id)

    async def get_by_name(self, name: str) -> Organization | None:
        """Load one organization by its display name."""

        statement = select(Organization).where(Organization.name == name)
        return await self.session.scalar(statement)


class UserRepository:
    """Persistence operations for users; transaction ownership stays above."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, user: User) -> User:
        """Stage and flush a user without committing its transaction."""

        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: UUID) -> User | None:
        """Load one user by its identity identifier."""

        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Load one user using the shared application email identity contract."""

        _, normalized_email = canonicalize_email(email)
        return await self.get_by_normalized_email(normalized_email)

    async def get_by_normalized_email(self, normalized_email: str) -> User | None:
        """Load one user by an already canonicalized email identity."""

        statement = select(User).where(User.normalized_email == normalized_email)
        return await self.session.scalar(statement)


class MembershipRepository:
    """Tenant-scoped membership persistence; transaction ownership stays above.

    Every lookup answers a question that already names the organization scope,
    which keeps the future principal chain (user -> membership -> organization
    -> role) expressible without introducing a global membership listing.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, membership: Membership) -> Membership:
        """Stage and flush a membership without committing its transaction."""

        self.session.add(membership)
        await self.session.flush()
        return membership

    async def get(self, membership_id: UUID) -> Membership | None:
        """Load one membership by its identity identifier."""

        return await self.session.get(Membership, membership_id)

    async def get_for_user_in_organization(
        self, user_id: UUID, organization_id: UUID
    ) -> Membership | None:
        """Load the single membership for one user inside one organization."""

        statement = select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
        )
        return await self.session.scalar(statement)

    async def list_for_organization(self, organization_id: UUID) -> list[Membership]:
        """List the memberships owned by one organization scope."""

        statement = select(Membership).where(Membership.organization_id == organization_id)
        statement = statement.order_by(Membership.created_at, Membership.id)
        result = await self.session.scalars(statement)
        return list(result)

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        """List one user's memberships.

        This is deliberately the only user-scoped membership query.  Login must
        resolve the caller's own membership before any organization scope
        exists, and it fails closed when the result is not a single active row.
        """

        statement = select(Membership).where(Membership.user_id == user_id)
        statement = statement.order_by(Membership.created_at, Membership.id)
        result = await self.session.scalars(statement)
        return list(result)

    async def list_eligible_for_user(self, user_id: UUID) -> list[Membership]:
        """List one user's memberships whose organization also exists and is active.

        Eligibility is evaluated in PostgreSQL - user scope, active membership,
        and an existing active organization - so login never filters tenants in
        Python and never counts an inactive or dangling row as selectable.
        """

        statement = (
            select(Membership)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
                Organization.is_active.is_(True),
            )
            .order_by(Membership.created_at, Membership.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class AuthSessionRepository:
    """Opaque session persistence; transaction ownership stays above.

    Lookups use the token digest only.  The raw token is never a parameter name,
    never stored, and never returned from this boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, auth_session: AuthSession) -> AuthSession:
        """Stage and flush a session without committing its transaction."""

        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def get(self, session_id: UUID) -> AuthSession | None:
        """Load one session by its identity identifier."""

        return await self.session.get(AuthSession, session_id)

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Load the single session owning ``token_hash`` (never a raw token)."""

        statement = select(AuthSession).where(AuthSession.token_hash == token_hash)
        return await self.session.scalar(statement)

    async def revoke(self, auth_session: AuthSession, revoked_at: datetime) -> AuthSession:
        """Mark one session revoked; the row is retained for audit."""

        auth_session.revoked_at = revoked_at
        await self.session.flush()
        return auth_session

    async def delete_expired(self, now: datetime) -> int:
        """Delete only rows that already expired; explicit revocation is never deleted."""

        statement = delete(AuthSession).where(AuthSession.expires_at < now)
        result = cast(CursorResult, await self.session.execute(statement))
        return int(result.rowcount or 0)
