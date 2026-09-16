"""Small repositories for TaskPilot business models."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.identity import canonicalize_email
from persistence.models import Membership, Organization, User


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
