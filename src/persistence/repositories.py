"""Small repositories for TaskPilot business models."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.identity import canonicalize_email
from persistence.models import Organization, User


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
