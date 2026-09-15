"""Small repositories for TaskPilot business models."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Organization


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
