"""Protected `/api/v1/tasks` routes for T036–T038."""

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from geochange.artifacts import artifact_path
from schema.geochange_api import GeoChangeMapResponse
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

_SCIENTIFIC_LIMITS = {
    "NDVI": "NDVI 是植被指数变化，不等同于植被面积变化。",
    "NDWI": "NDWI 是连续水体相关指数，不足以确认水域面积或扩张。",
    "NDBI": "NDBI 是连续建成区相关指数，不足以确认建设用地或城市扩张面积。",
}


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


@task_router.get("/{task_id}/runs/{run_id}/map", response_model=GeoChangeMapResponse)
async def get_task_run_map(
    task_id: UUID,
    run_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> GeoChangeMapResponse:
    run = await TaskRunService(session).get_run(principal, task_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=RESOURCE_NOT_FOUND_DETAIL)
    if run.status.value != "succeeded":
        raise HTTPException(status_code=409, detail="分析尚未完成")
    metadata = run.result_metadata if isinstance(run.result_metadata, dict) else {}
    if metadata.get("verifier_status") != "passed":
        raise HTTPException(status_code=409, detail="分析结果尚未验证")
    indicator = metadata.get("indicator")
    periods = metadata.get("analysis_periods")
    provenance = metadata.get("provenance")
    if not isinstance(indicator, str) or indicator not in _SCIENTIFIC_LIMITS:
        raise HTTPException(status_code=422, detail="分析结果缺少可信指标")
    if (
        not isinstance(periods, dict)
        or set(periods) != {"period_a", "period_b"}
        or not all(isinstance(v, str) and v.strip() for v in periods.values())
    ):
        raise HTTPException(status_code=422, detail="分析结果缺少可信时段")
    artifacts = metadata.get("artifact_references")
    safe_artifacts = artifacts if isinstance(artifacts, dict) else {}
    allowed_artifacts = {
        "ndvi_before",
        "ndvi_after",
        "ndvi_change",
        "ndwi_before",
        "ndwi_after",
        "ndwi_change",
        "ndbi_before",
        "ndbi_after",
        "ndbi_change",
    }
    if any(
        not isinstance(key, str) or key not in allowed_artifacts or value != key
        for key, value in safe_artifacts.items()
    ):
        raise HTTPException(status_code=422, detail="分析结果包含未验证的图层引用")
    bounds = metadata.get("bounds")
    safe_bounds = (
        bounds
        if isinstance(bounds, list)
        and len(bounds) == 4
        and all(isinstance(value, (int, float)) for value in bounds)
        else None
    )
    return GeoChangeMapResponse(
        indicator=indicator,
        period={str(k): str(v) for k, v in periods.items()},
        aoi_label=str(metadata.get("analysis_area", "武汉东湖（受限缓存覆盖区）")),
        data_source=str(metadata.get("data_source", "已验证的 Sentinel-2 缓存样例")),
        bounds=safe_bounds,
        crs=str(provenance.get("aoi_crs")) if isinstance(provenance, dict) else None,
        valid_value_summary=metadata.get("metrics")
        if isinstance(metadata.get("metrics"), dict)
        else None,
        artifacts={str(k): str(v) for k, v in safe_artifacts.items() if isinstance(v, str)},
        scientific_limit=_SCIENTIFIC_LIMITS[indicator],
    )


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
