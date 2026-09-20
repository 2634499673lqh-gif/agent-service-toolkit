from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from persistence.models import Task, TaskRun, TaskRunStatus, TaskStatus
from service.task_lifecycle import (
    TaskLifecycleConflictError,
    TaskLifecycleInconsistentStateError,
    TaskLifecycleService,
)

ORG = UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")


def make_task(status: TaskStatus) -> Task:
    task = Task(organization_id=ORG, created_by_user_id=USER_ID, title="lifecycle")
    task.id = TASK_ID
    task.status = status
    return task


def make_service(task: Task) -> TaskLifecycleService:
    service = TaskLifecycleService(Mock())
    service.tasks.get_for_update_in_principal_tenant = AsyncMock(return_value=task)
    service.tasks.next_run_number = AsyncMock(return_value=1)
    service.runs.add = AsyncMock(side_effect=lambda run: run)
    service.session.flush = AsyncMock()
    service.session.commit = AsyncMock()
    service.session.rollback = AsyncMock()
    service.session.close = AsyncMock()
    return service


@pytest.mark.asyncio
async def test_start_draft_creates_pending_run_and_queues_task() -> None:
    service = make_service(make_task(TaskStatus.DRAFT))

    run = await service.start_task(TASK_ID, ORG)

    assert run.status is TaskRunStatus.PENDING
    assert run.run_number == 1
    assert service.tasks.get_for_update_in_principal_tenant.await_args.args == (TASK_ID, ORG)
    service.session.commit.assert_awaited_once_with()
    service.session.rollback.assert_not_awaited()
    service.session.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_non_restartable_states() -> None:
    for status in (
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
        TaskStatus.CANCELLED,
    ):
        service = make_service(make_task(status))
        with pytest.raises(TaskLifecycleConflictError):
            await service.start_task(TASK_ID, ORG)
        service.runs.add.assert_not_awaited()
        service.session.rollback.assert_awaited_once_with()
        service.session.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_begin_and_finish_keep_task_and_run_in_sync() -> None:
    task = make_task(TaskStatus.QUEUED)
    run = TaskRun(task_id=TASK_ID, run_number=1)
    service = make_service(task)
    service.runs.get_active_for_update = AsyncMock(return_value=run)

    await service.begin_run(TASK_ID, ORG)
    assert task.status is TaskStatus.RUNNING
    assert run.status is TaskRunStatus.RUNNING

    await service.succeed_run(TASK_ID, ORG)
    assert task.status is TaskStatus.SUCCEEDED
    assert run.status is TaskRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_cancel_is_persistence_only_and_repeated_cancel_is_stable() -> None:
    task = make_task(TaskStatus.QUEUED)
    run = TaskRun(task_id=TASK_ID, run_number=1)
    service = make_service(task)
    service.runs.get_active_for_update = AsyncMock(return_value=run)

    assert await service.cancel_task(TASK_ID, ORG) is task
    assert task.status is TaskStatus.CANCELLED
    assert run.status is TaskRunStatus.CANCELLED
    service.runs.get_active_for_update.reset_mock()
    assert await service.cancel_task(TASK_ID, ORG) is task
    service.runs.get_active_for_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_active_run_fails_closed() -> None:
    service = make_service(make_task(TaskStatus.RUNNING))
    service.runs.get_active_for_update = AsyncMock(return_value=None)

    with pytest.raises(TaskLifecycleInconsistentStateError):
        await service.fail_run(TASK_ID, ORG)
    assert service.session.flush.await_count == 0
