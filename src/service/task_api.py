"""Protected `/api/v1/tasks` routes for T036–T038."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from geochange.artifacts import artifact_path
from schema.task_api import TaskCreateRequest, TaskResponse, TaskUpdateRequest
from schema.task_run_api import TaskRunResponse
from schema.trace_api import TraceEventResponse
from service.auth_dependency import PrincipalDependency, get_session_factory
from service.authorization import RESOURCE_NOT_FOUND_DETAIL, AuthorizationError
from service.task_lifecycle import (
    TaskLifecycleConflictError,
    TaskLifecycleInconsistentStateError,
    TaskNotFoundError,
)
from service.task_run_service import TaskRunService
from service.task_service import TaskService
from service.trace_service import TraceService

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


@task_router.post(
    "/{task_id}/runs",
    response_model=TaskRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_task_run(
    task_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
    request: Request,
) -> TaskRunResponse:
    try:
        task_run = await TaskRunService(session).start_task(principal, task_id)
    except TaskNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        ) from None
    except (TaskLifecycleConflictError, TaskLifecycleInconsistentStateError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=TASK_LIFECYCLE_CONFLICT_DETAIL,
        ) from None
    dispatcher = getattr(request.app.state, "runtime_dispatcher", None)
    if dispatcher is not None:
        dispatcher.dispatch(
            organization_id=principal.organization_id,
            task_id=task_id,
            task_run_id=task_run.id,
        )
    return TaskRunResponse.model_validate(task_run)


@task_router.get("/{task_id}/runs", response_model=list[TaskRunResponse])
async def list_task_runs(
    task_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> list[TaskRunResponse]:
    task_runs = await TaskRunService(session).list_runs(principal, task_id)
    if task_runs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        )
    return [TaskRunResponse.model_validate(task_run) for task_run in task_runs]


@task_router.get("/{task_id}/runs/{run_id}", response_model=TaskRunResponse)
async def get_task_run(
    task_id: UUID,
    run_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> TaskRunResponse:
    task_run = await TaskRunService(session).get_run(principal, task_id, run_id)
    if task_run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        )
    return TaskRunResponse.model_validate(task_run)


@task_router.get(
    "/{task_id}/runs/{run_id}/trace",
    response_model=list[TraceEventResponse],
)
async def get_task_trace(
    task_id: UUID,
    run_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
    limit: int = Query(default=100, ge=1),
) -> list[TraceEventResponse]:
    trace = await TraceService(session).get_trace(
        principal,
        task_id,
        run_id,
        limit=limit,
    )
    if trace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        )
    return trace


@task_router.get("/{task_id}/runs/{run_id}/artifacts/{artifact_name}")
async def get_task_artifact(
    task_id: UUID,
    run_id: UUID,
    artifact_name: str,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> FileResponse:
    run = await TaskRunService(session).get_run(principal, task_id, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_DETAIL)
    metadata = run.result_metadata if isinstance(run.result_metadata, dict) else {}
    references = metadata.get("artifact_references")
    if not isinstance(references, dict) or references.get(artifact_name) != artifact_name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_DETAIL)
    try:
        path = artifact_path(str(task_id), str(run_id), artifact_name)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_DETAIL
        ) from None
    if not path.is_file() or not 0 < path.stat().st_size <= 2_000_000:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_DETAIL)
    try:
        with Image.open(path) as image:
            if image.format != "PNG":
                raise ValueError("artifact is not PNG")
            image.verify()
    except (OSError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=RESOURCE_NOT_FOUND_DETAIL
        ) from None
    return FileResponse(path, media_type="image/png", filename=path.name)


__all__ = ["TASK_LIFECYCLE_CONFLICT_DETAIL", "get_task_session", "task_router"]
