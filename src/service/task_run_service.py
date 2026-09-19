"""Application service for the T038 TaskRun start/inspect surface."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import TaskRun
from persistence.repositories import TaskRunRepository
from service.session import CurrentPrincipal
from service.task_lifecycle import TaskLifecycleService


class TaskRunService:
    """Coordinate TaskRun API use cases without owning lifecycle transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = TaskRunRepository(session)

    async def start_task(self, principal: CurrentPrincipal, task_id: UUID) -> TaskRun:
        """Start or explicitly retry a tenant-visible Task through T035."""

        return await TaskLifecycleService(self.session).start_task(
            task_id, principal.organization_id
        )

    async def get_run(
        self,
        principal: CurrentPrincipal,
        task_id: UUID,
        run_id: UUID,
    ) -> TaskRun | None:
        """Inspect one historical run through its tenant-scoped Task join."""

        return await self.runs.get_for_task_in_principal_tenant(
            task_id, run_id, principal.organization_id
        )


__all__ = ["TaskRunService"]
