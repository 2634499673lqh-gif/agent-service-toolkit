"""Explicit Task/TaskRun lifecycle orchestration for Phase 3."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Task, TaskRun, TaskRunStatus, TaskStatus
from persistence.repositories import TaskRepository, TaskRunRepository


class TaskLifecycleError(Exception):
    """Base class for service-level lifecycle failures."""


class TaskNotFoundError(TaskLifecycleError):
    """The resource is not visible in the supplied tenant scope."""


class TaskLifecycleConflictError(TaskLifecycleError):
    """The requested operation is not legal for the current state."""


class TaskLifecycleInconsistentStateError(TaskLifecycleError):
    """Persisted Task and TaskRun state violates a frozen invariant."""


class TaskLifecycleService:
    """Coordinate legal Task and TaskRun transitions in service transactions.

    Methods lock the tenant-scoped Task before making a lifecycle decision.
    Successful operations commit atomically; failures roll back and propagate.
    The service never closes or replaces the supplied session.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        before_task_lock: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.session = session
        self.before_task_lock = before_task_lock
        self.tasks = TaskRepository(session)
        self.runs = TaskRunRepository(session)

    async def start_task(self, task_id: UUID, organization_id: UUID) -> TaskRun:
        try:
            run = await self._start_task(task_id, organization_id)
            await self.session.commit()
            return run
        except BaseException:
            await self.session.rollback()
            raise

    async def _start_task(self, task_id: UUID, organization_id: UUID) -> TaskRun:
        task = await self._locked_task(task_id, organization_id)
        if task.status not in (TaskStatus.DRAFT, TaskStatus.FAILED):
            raise TaskLifecycleConflictError(f"cannot start task in {task.status.value} state")
        run = TaskRun(
            task_id=task.id,
            run_number=await self.tasks.next_run_number(task.id),
            status=TaskRunStatus.PENDING,
        )
        task.status = TaskStatus.QUEUED
        await self.runs.add(run)
        await self.session.flush()
        return run

    async def begin_run(self, task_id: UUID, organization_id: UUID) -> TaskRun:
        try:
            run = await self._begin_run(task_id, organization_id)
            await self.session.commit()
            return run
        except BaseException:
            await self.session.rollback()
            raise

    async def _begin_run(self, task_id: UUID, organization_id: UUID) -> TaskRun:
        task = await self._locked_task(task_id, organization_id)
        if task.status is not TaskStatus.QUEUED:
            raise TaskLifecycleConflictError(
                f"cannot begin run for task in {task.status.value} state"
            )
        run = await self.runs.get_active_for_update(task.id, TaskRunStatus.PENDING)
        if run is None:
            raise TaskLifecycleInconsistentStateError("queued task has no pending active run")
        run.status = TaskRunStatus.RUNNING
        task.status = TaskStatus.RUNNING
        await self.session.flush()
        return run

    async def succeed_run(self, task_id: UUID, organization_id: UUID) -> Task:
        return await self._finish_public(
            task_id, organization_id, TaskRunStatus.SUCCEEDED, TaskStatus.SUCCEEDED
        )

    async def fail_run(self, task_id: UUID, organization_id: UUID) -> Task:
        return await self._finish_public(
            task_id, organization_id, TaskRunStatus.FAILED, TaskStatus.FAILED
        )

    async def _finish_public(
        self,
        task_id: UUID,
        organization_id: UUID,
        run_status: TaskRunStatus,
        task_status: TaskStatus,
    ) -> Task:
        try:
            task = await self._finish_run(task_id, organization_id, run_status, task_status)
            await self.session.commit()
            return task
        except BaseException:
            await self.session.rollback()
            raise

    async def _finish_run(
        self,
        task_id: UUID,
        organization_id: UUID,
        run_status: TaskRunStatus,
        task_status: TaskStatus,
    ) -> Task:
        task = await self._locked_task(task_id, organization_id)
        if task.status is not TaskStatus.RUNNING:
            raise TaskLifecycleConflictError(f"cannot finish task in {task.status.value} state")
        run = await self.runs.get_active_for_update(task.id, TaskRunStatus.RUNNING)
        if run is None:
            raise TaskLifecycleInconsistentStateError("running task has no running active run")
        run.status = run_status
        task.status = task_status
        await self.session.flush()
        return task

    async def cancel_task(self, task_id: UUID, organization_id: UUID) -> Task:
        try:
            task = await self._cancel_task(task_id, organization_id)
            await self.session.commit()
            return task
        except BaseException:
            await self.session.rollback()
            raise

    async def _cancel_task(self, task_id: UUID, organization_id: UUID) -> Task:
        task = await self._locked_task(task_id, organization_id)
        if task.status is TaskStatus.CANCELLED:
            return task
        if task.status in (TaskStatus.SUCCEEDED, TaskStatus.FAILED):
            raise TaskLifecycleConflictError(f"cannot cancel task in {task.status.value} state")
        if task.status is TaskStatus.DRAFT:
            task.status = TaskStatus.CANCELLED
        elif task.status is TaskStatus.QUEUED:
            run = await self.runs.get_active_for_update(task.id, TaskRunStatus.PENDING)
            if run is None:
                raise TaskLifecycleInconsistentStateError("queued task has no pending active run")
            run.status = TaskRunStatus.CANCELLED
            task.status = TaskStatus.CANCELLED
        elif task.status is TaskStatus.RUNNING:
            run = await self.runs.get_active_for_update(task.id, TaskRunStatus.RUNNING)
            if run is None:
                raise TaskLifecycleInconsistentStateError("running task has no running active run")
            run.status = TaskRunStatus.CANCELLED
            task.status = TaskStatus.CANCELLED
        else:  # pragma: no cover - enum exhaustiveness guard
            raise TaskLifecycleConflictError("unsupported task state")
        await self.session.flush()
        return task

    async def _locked_task(self, task_id: UUID, organization_id: UUID) -> Task:
        if self.before_task_lock is not None:
            await self.before_task_lock()
        task = await self.tasks.get_for_update_in_principal_tenant(task_id, organization_id)
        if task is None:
            raise TaskNotFoundError("task is not visible in the organization")
        return task


__all__ = [
    "TaskLifecycleConflictError",
    "TaskLifecycleError",
    "TaskLifecycleInconsistentStateError",
    "TaskLifecycleService",
    "TaskNotFoundError",
]
