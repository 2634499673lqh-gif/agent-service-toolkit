"""Bounded process-local bridge from Product run creation to the runtime."""

import asyncio
import logging
from uuid import UUID

from persistence.engine import get_business_session_factory
from persistence.models import TaskRunStatus
from persistence.repositories import TaskRunRepository
from service.task_lifecycle import TaskLifecycleService
from service.task_runtime import TaskRuntimeService

logger = logging.getLogger(__name__)


class RuntimeDispatchService:
    """Own background tasks while the FastAPI process is alive."""

    def __init__(self, checkpointer: object) -> None:
        self.checkpointer = checkpointer
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_runs: set[UUID] = set()

    def dispatch(self, *, organization_id: UUID, task_id: UUID, task_run_id: UUID) -> None:
        if task_run_id in self._active_runs:
            return
        self._active_runs.add(task_run_id)
        task = asyncio.create_task(
            self._execute(organization_id=organization_id, task_id=task_id, task_run_id=task_run_id)
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda _task: self._active_runs.discard(task_run_id))

    async def _execute(self, *, organization_id: UUID, task_id: UUID, task_run_id: UUID) -> None:
        try:
            factory = get_business_session_factory()
            async with factory() as session:
                await TaskRuntimeService(self.checkpointer).execute_run(
                    session,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("runtime.dispatch.failed", extra={"task_run_id": str(task_run_id)})
            # Fail closed if an unexpected bridge/runtime error escaped before
            # TaskRuntimeService could apply its normal terminal transition.
            try:
                async with get_business_session_factory()() as recovery_session:
                    pair = await TaskRunRepository(
                        recovery_session
                    ).get_task_and_run_in_principal_tenant(task_id, task_run_id, organization_id)
                    if pair is not None and pair[1].status is TaskRunStatus.RUNNING:
                        await TaskLifecycleService(recovery_session).fail_run(
                            task_id, organization_id, task_run_id
                        )
            except Exception:
                logger.exception(
                    "runtime.dispatch.recovery_failed",
                    extra={"task_run_id": str(task_run_id)},
                )

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


__all__ = ["RuntimeDispatchService"]
