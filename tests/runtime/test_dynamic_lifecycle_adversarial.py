"""Adversarial trusted-dynamic checkpoint and terminal lifecycle coverage."""

import copy
import hashlib
import json
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from geochange.aoi import resolve_aoi
from geochange.artifacts import dynamic_evidence_path, write_dynamic_evidence
from persistence.models import Task, TaskRun, TaskRunStatus, TaskStatus
from runtime import AgentState, PlannerNode, VerifierNode, build_runtime_graph
from runtime.executor import TrustedDynamicExecutionResult
from schema.confirmed_intent import ConfirmedIntent
from schema.planner import Plan
from schema.verifier import VerificationResult
from service.task_runtime import RuntimeExecutionResult, TaskRuntimeService

ORG = UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
THREAD_ID = f"taskpilot-run:{RUN_ID}"


class _FakeRuns:
    def __init__(self, task: Task, run: TaskRun) -> None:
        self.task = task
        self.run = run

    async def get_task_and_run_in_principal_tenant(self, task_id, run_id, organization_id):
        if (task_id, run_id, organization_id) != (self.task.id, self.run.id, ORG):
            return None
        return self.task, self.run


class _StaticResumeGraph:
    """A real TaskRuntime resume boundary with a deterministic graph result."""

    def __init__(self, state: AgentState) -> None:
        self.state = state

    async def aget_tuple(self, config):
        return {
            "config": config,
            "checkpoint": {"channel_values": self.state.checkpoint_data()},
        }

    async def ainvoke(self, _input, *, config, context=None):  # noqa: ARG002
        return self.state.checkpoint_data()


def _task_and_run() -> tuple[Task, TaskRun]:
    task = Task(
        id=TASK_ID,
        organization_id=ORG,
        created_by_user_id=USER_ID,
        title="Jianghan NDVI",
        description="July comparison",
        status=TaskStatus.RUNNING,
        confirmed_intent={
            "analysis_type": "vegetation_change",
            "indicator": "NDVI",
            "analysis_area": "jianghan_district_420103",
            "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
            "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
            "parameters": {"source": "Landsat-8/9"},
            "data_mode": "real_stac_landsat_local",
        },
    )
    return task, TaskRun(id=RUN_ID, task_id=TASK_ID, run_number=1, status=TaskRunStatus.RUNNING)


def _payload() -> dict[str, Any]:
    aoi = resolve_aoi("jianghan_district_420103")
    metrics: dict[str, Any] = {
        "mean_ndvi_period_a": 0.2,
        "mean_ndvi_period_b": 0.4,
        "mean_delta_ndvi": 0.2,
        "final_common_comparison_pixels": 4,
    }
    scene_provenance = {
        "period_a": {
            "scene_ids": ["scene-a"],
            "acquisition_dates": ["2023-07-15"],
            "asset_identity_hashes": ["a" * 64],
        },
        "period_b": {
            "scene_ids": ["scene-b"],
            "acquisition_dates": ["2024-07-15"],
            "asset_identity_hashes": ["b" * 64],
        },
    }
    target_grid = {"crs": "EPSG:32649", "width": 296, "height": 264}
    selected_scene = {
        "preparation_contract_version": "v0.3-preparation-1",
        "period_a": json.dumps(scene_provenance["period_a"], sort_keys=True),
        "period_b": json.dumps(scene_provenance["period_b"], sort_keys=True),
        "target_grid": json.dumps(target_grid, sort_keys=True),
    }
    artifacts = {
        name: name
        for name in (
            "ndvi_before",
            "ndvi_after",
            "ndvi_change",
            "ndvi_before_raster",
            "ndvi_after_raster",
            "ndvi_change_raster",
            "ndvi_valid_before",
            "ndvi_valid_after",
            "ndvi_common_comparison",
        )
    }
    provenance: dict[str, Any] = {
        "aoi_id": "jianghan_district_420103",
        "aoi_hash": str(getattr(aoi, "source_hash", "")),
        "target_crs": "EPSG:32649",
        "target_dimensions": [296, 264],
        "target_grid": target_grid,
        "scene_provenance": scene_provenance,
        "artifact_checksums": {name: "c" * 64 for name in artifacts},
    }
    provenance["metrics_sha256"] = hashlib.sha256(
        json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    return {
        "schema_version": "geochange.v1",
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "execution_mode": "real_stac_landsat_local",
        "mode": "real_stac_landsat_local",
        "analysis_area": "jianghan_district_420103",
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "summary": "trusted test product",
        "data_source": "landsat-c2-l2",
        "provenance_summary": "server-owned test evidence",
        "metrics": metrics,
        "provenance": provenance,
        "artifacts": artifacts,
        "selected_scene_evidence": selected_scene,
        "verifier_status": "passed",
    }


def _dynamic_state(
    *, payload: dict[str, Any] | None = None, binder: dict[str, Any] | None = None
) -> AgentState:
    task, _ = _task_and_run()
    runtime_task = ConfirmedIntent.model_validate(task.confirmed_intent).runtime_task()
    payload = copy.deepcopy(payload or _payload())
    binder = copy.deepcopy(
        binder
        or {
            key: payload[key]
            for key in (
                "analysis_periods",
                "metrics",
                "provenance",
                "artifacts",
                "selected_scene_evidence",
            )
        }
    )
    aoi = resolve_aoi("jianghan_district_420103")
    result = TrustedDynamicExecutionResult(
        step_position=4,
        success=True,
        output=json.dumps(payload, separators=(",", ":")),
        trusted_dynamic=True,
        canonical_evidence=binder,
    )
    return AgentState(
        task_id=str(TASK_ID),
        task_run_id=str(RUN_ID),
        task_input={
            "title": "vegetation_change NDVI Wuhan East Lake",
            "description": runtime_task.model_dump_json(),
        },
        geochange_task=runtime_task,
        geochange_aoi_evidence={
            "catalog_key": aoi.catalog_key,
            "crs": aoi.crs,
            "source": aoi.source,
        },
        geochange_evidence={
            "execution_mode": "real_stac_landsat_local",
            "collection": "landsat-c2-l2",
        },
        plan=Plan.model_validate(
            {
                "steps": [
                    {"position": 1, "instruction": "resolve_aoi"},
                    {"position": 2, "instruction": "search_sentinel2"},
                    {"position": 3, "instruction": "compute_vegetation_change"},
                    {"position": 4, "instruction": "summarize_change"},
                ]
            }
        ),
        plan_position=3,
        execution_result=result,
        trusted_dynamic_evidence=binder,
        verification=VerificationResult(verdict="PASS", reason="trusted", evidence=[]),
        terminal_outcome="SUCCEEDED",
    )


def _binder(state: AgentState) -> dict[str, Any]:
    assert isinstance(state.trusted_dynamic_evidence, dict)
    return state.trusted_dynamic_evidence


def _lifecycle(run: TaskRun) -> Mock:
    lifecycle = Mock()

    async def succeed(*_args):
        run.status = TaskRunStatus.SUCCEEDED

    async def fail(*_args):
        run.status = TaskRunStatus.FAILED

    lifecycle.succeed_run = AsyncMock(side_effect=succeed)
    lifecycle.fail_run = AsyncMock(side_effect=fail)
    return lifecycle


async def _resume(
    monkeypatch,
    tmp_path,
    state: AgentState,
    sidecar: dict[str, Any] | str | None,
) -> tuple[RuntimeExecutionResult, Mock]:
    monkeypatch.setattr("geochange.artifacts.ARTIFACT_ROOT", tmp_path)
    path = dynamic_evidence_path(str(TASK_ID), str(RUN_ID))
    path.parent.mkdir(parents=True, exist_ok=True)
    if sidecar is None:
        path.unlink(missing_ok=True)
    elif isinstance(sidecar, str):
        path.write_text(sidecar, encoding="utf-8")
    else:
        write_dynamic_evidence(str(TASK_ID), str(RUN_ID), sidecar)
    task, run = _task_and_run()
    lifecycle = _lifecycle(run)
    graph = _StaticResumeGraph(state)
    service = TaskRuntimeService(graph, graph=graph)
    with (
        patch("service.task_runtime.TaskRunRepository", return_value=_FakeRuns(task, run)),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            AsyncMock(spec=AsyncSession),
            organization_id=ORG,
            task_id=TASK_ID,
            task_run_id=RUN_ID,
        )
    return cast(RuntimeExecutionResult, result), lifecycle


@pytest.mark.asyncio
async def test_self_consistent_forged_checkpoint_cannot_resume(monkeypatch, tmp_path):
    real = _dynamic_state()
    forged_payload = copy.deepcopy(_payload())
    forged_payload["metrics"]["final_common_comparison_pixels"] = 1
    forged = _dynamic_state(payload=forged_payload)
    result, _ = await _resume(monkeypatch, tmp_path, forged, real.trusted_dynamic_evidence)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "skill_result_invalid"


@pytest.mark.asyncio
async def test_checkpoint_binder_change_is_rejected_by_unchanged_sidecar(monkeypatch, tmp_path):
    real = _dynamic_state()
    forged_payload = copy.deepcopy(_payload())
    forged_payload["analysis_periods"]["period_a"] = "2022-07-01/2022-07-31"
    forged = _dynamic_state(payload=forged_payload)
    result, _ = await _resume(monkeypatch, tmp_path, forged, real.trusted_dynamic_evidence)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "skill_result_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize("sidecar", [None, "not-json", {"metrics": {"forged": 1}}])
async def test_missing_malformed_or_mismatched_sidecar_fails_closed(monkeypatch, tmp_path, sidecar):
    state = _dynamic_state()
    result, lifecycle = await _resume(monkeypatch, tmp_path, state, sidecar)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "skill_result_invalid"
    lifecycle.succeed_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_matching_sidecar_allows_real_runtime_resume(monkeypatch, tmp_path):
    state = _dynamic_state()
    result, lifecycle = await _resume(monkeypatch, tmp_path, state, state.trusted_dynamic_evidence)
    assert result.terminal_outcome == "SUCCEEDED"
    assert result.state.terminal_outcome == "SUCCEEDED"
    lifecycle.succeed_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_terminal_finish_rejects_dynamic_binder_without_persisting_success(
    monkeypatch, tmp_path
):
    real = _dynamic_state()
    forged_payload = copy.deepcopy(_payload())
    forged_payload["metrics"]["mean_delta_ndvi"] = 0.99
    forged = _dynamic_state(payload=forged_payload)
    task, run = _task_and_run()
    lifecycle = _lifecycle(run)
    monkeypatch.setattr("geochange.artifacts.ARTIFACT_ROOT", tmp_path)
    write_dynamic_evidence(str(TASK_ID), str(RUN_ID), _binder(real))
    graph = _StaticResumeGraph(forged)
    service = TaskRuntimeService(graph, graph=graph)
    with patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle):
        result = await service._finish(
            AsyncMock(spec=AsyncSession),
            cast(Any, _FakeRuns(task, run)),
            organization_id=ORG,
            task_id=TASK_ID,
            task_run_id=RUN_ID,
            thread_id=THREAD_ID,
            state=forged,
        )
    assert result.terminal_outcome == "FAILED"
    lifecycle.fail_run.assert_awaited_once()
    assert lifecycle.result_metadata["summary"] == "Runtime failed"
    assert lifecycle.result_metadata["execution_mode"] == "deterministic_fixture"


@pytest.mark.asyncio
async def test_real_replan_clears_dynamic_result_and_binder_before_retry(monkeypatch, tmp_path):
    dynamic = _dynamic_state()
    monkeypatch.setattr("geochange.artifacts.ARTIFACT_ROOT", tmp_path)
    write_dynamic_evidence(str(TASK_ID), str(RUN_ID), _binder(dynamic))
    plan = Plan.model_validate({"steps": [{"position": 1, "instruction": "Inspect the task"}]})
    initial = AgentState.initial(
        task_id=TASK_ID,
        task_run_id=RUN_ID,
        title="Read-only lifecycle test",
        description="Keep this test outside GeoChange skill routing.",
    ).model_copy(
        update={
            "plan": plan,
            "execution_result": dynamic.execution_result,
            "trusted_dynamic_evidence": dynamic.trusted_dynamic_evidence,
        }
    )

    async def replacement_planner(_request):
        return _replacement_plan()

    planner = PlannerNode(replacement_planner)
    verifier_outputs = iter(
        [
            {"verdict": "FAIL", "reason": "force one replan", "evidence": []},
            {"verdict": "PASS", "reason": "replacement passed", "evidence": []},
        ]
    )

    async def verifier_model(_request):
        return next(verifier_outputs)

    verifier = VerifierNode(verifier_model)
    saver = MemorySaver()
    graph = build_runtime_graph(saver, planner=planner, verifier=verifier)
    result = await graph.ainvoke(
        initial.checkpoint_data(), config={"configurable": {"thread_id": THREAD_ID}}
    )
    assert result["replan_count"] == 1
    assert result["trusted_dynamic_evidence"] is None
    assert result["execution_result"].get("trusted_dynamic") is not True


def _replacement_plan() -> dict[str, object]:
    return {"steps": [{"position": 1, "instruction": "Inspect the replacement"}]}
