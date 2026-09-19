"""Protected `/api/v1/tasks` routes for T036."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from schema.task_api import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from service.auth_dependency import PrincipalDependency, get_session_factory
from service.authorization import RESOURCE_NOT_FOUND_DETAIL, AuthorizationError
from service.task_lifecycle import (
    TaskLifecycleConflictError,
    TaskLifecycleInconsistentStateError,
    TaskNotFoundError,
)
from service.task_service import TaskService

TASK_LIFECYCLE_CONFLICT_DETAIL = "Task lifecycle conflict"


async def get_task_session(
    session_factory: Annotated[async_sessionmaker[AsyncSession], Depends(get_session_factory)],
) -> AsyncIterator[AsyncSession]:
    """Provide the Task API session; authentication uses and closes its own session."""

    async with session_factory() as session:
        yield session


TaskSessionDependency = Annotated[AsyncSession, Depends(get_task_session)]

task_router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@task_router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: TaskCreateRequest,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> TaskResponse:
    task = await TaskService(session).create_task(
        principal,
        title=payload.title,
        description=payload.description,
    )
    return TaskResponse.model_validate(task)


@task_router.get("", response_model=list[TaskResponse])
async def list_tasks(
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> list[TaskResponse]:
    tasks = await TaskService(session).list_tasks(principal.organization_id)
    return [TaskResponse.model_validate(task) for task in tasks]


@task_router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> TaskResponse:
    task = await TaskService(session).get_task(task_id, principal.organization_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        )
    return TaskResponse.model_validate(task)


@task_router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    payload: TaskUpdateRequest,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> TaskResponse:
    try:
        task = await TaskService(session).update_task(
            principal,
            task_id,
            title=payload.title,
            description=payload.description,
            update_title="title" in payload.model_fields_set,
            update_description="description" in payload.model_fields_set,
        )
    except TaskNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        ) from None
    except AuthorizationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    return TaskResponse.model_validate(task)


@task_router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> TaskResponse:
    try:
        task = await TaskService(session).cancel_task(principal, task_id)
    except TaskNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        ) from None
    except AuthorizationError as error:
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    except (TaskLifecycleConflictError, TaskLifecycleInconsistentStateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=TASK_LIFECYCLE_CONFLICT_DETAIL,
        ) from None
    return TaskResponse.model_validate(task)


__all__ = ["TASK_LIFECYCLE_CONFLICT_DETAIL", "get_task_session", "task_router"]
