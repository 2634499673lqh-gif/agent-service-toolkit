"""Application service for the T036 Task create/list/get surface."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Task
from persistence.repositories import TaskRepository
from service.session import CurrentPrincipal


class TaskService:
    """Own Task API use cases while keeping tenant scope in repository SQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)

    async def create_task(
        self,
        principal: CurrentPrincipal,
        *,
        title: str,
        description: str | None = None,
    ) -> Task:
        """Create a draft Task with ownership derived from the principal."""

        try:
            task = Task(
                organization_id=principal.organization_id,
                created_by_user_id=principal.user_id,
                title=title,
                description=description,
            )
            await self.tasks.add(task)
            await self.session.commit()
            return task
        except BaseException:
            await self.session.rollback()
            raise

    async def list_tasks(self, organization_id: UUID) -> list[Task]:
        """List only Tasks inside the trusted principal organization."""

        return await self.tasks.list_for_organization(organization_id)

    async def get_task(self, task_id: UUID, organization_id: UUID) -> Task | None:
        """Load one Task through the tenant-scoped repository boundary."""

        return await self.tasks.get_in_principal_tenant(task_id, organization_id)


__all__ = ["TaskService"]
