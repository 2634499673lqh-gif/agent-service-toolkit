import json
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver

from geochange.models import GeoChangeTask
from geochange.skill import (
    VEGETATION_CHANGE_NDVI,
    SkillValidationError,
    resolve_skill,
)
from runtime import (
    AgentState,
    CapabilityDispatcher,
    CapabilityMetadata,
    ExecutionResult,
    PlannerNode,
    PlannerRequest,
    RuntimeGraphContext,
    build_runtime_graph,
)
from schema.planner import Plan


def _plan(*instructions: str) -> Plan:
    return Plan.model_validate(
        {
            "steps": [
                {"position": i, "instruction": value} for i, value in enumerate(instructions, 1)
            ]
        }
    )


def test_ndvi_skill_is_static_and_ordered() -> None:
    skill = resolve_skill("vegetation_change", "NDVI")
    assert skill is VEGETATION_CHANGE_NDVI
    skill.validate_plan(
        _plan("resolve_aoi", "search_sentinel2", "compute_vegetation_change", "summarize_change")
    )


@pytest.mark.parametrize(
    "analysis_type,indicator",
    [("water_change", "NDWI"), ("vegetation_change", "NDWI"), ("urban_change", "NDBI")],
)
def test_unknown_or_cross_skill_pairing_is_rejected(analysis_type: str, indicator: str) -> None:
    with pytest.raises(SkillValidationError):
        resolve_skill(analysis_type, indicator)


def test_ndvi_skill_rejects_unknown_and_wrong_order() -> None:
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_plan(_plan("resolve_aoi", "summarize_change"))
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_plan(
            _plan(
                "search_sentinel2", "resolve_aoi", "compute_vegetation_change", "summarize_change"
            )
        )


def test_result_type_and_artifacts_are_allowlisted() -> None:
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_result({"analysis_type": "urban_change"})
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_result(
            {"analysis_type": "vegetation_change", "artifacts": {"ndbi_change": "x"}}
        )


def _trusted_result_inputs() -> tuple[
    GeoChangeTask, dict[str, str], dict[str, str], dict[str, object]
]:
    task = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )
    aoi = {
        "catalog_key": task.aoi_key,
        "crs": "EPSG:4326",
        "source": "taskpilot.geochange.catalog.v1",
    }
    scene = {
        "fixture_manifest": "a" * 64,
        "period_a_item_id": "item-a",
        "period_a_date": "2023-07-28",
        "period_a_collection": "sentinel-2-l2a",
        "period_a_cloud_cover": "22.9",
        "period_a_red": "b" * 64,
        "period_a_nir": "c" * 64,
        "period_b_item_id": "item-b",
        "period_b_date": "2024-07-30",
        "period_b_collection": "sentinel-2-l2a",
        "period_b_cloud_cover": "12.0",
        "period_b_red": "d" * 64,
        "period_b_nir": "e" * 64,
    }
    result = {
        "schema_version": "geochange.v1",
        "analysis_type": "vegetation_change",
        "mode": "fixture",
        "summary": "valid",
        "metrics": {
            "valid_pixels": 10,
            "valid_analysis_area_m2": 1000.0,
            "mean_ndvi_period_a": 0.4,
            "mean_ndvi_period_b": 0.2,
            "mean_delta_ndvi": -0.2,
            "significant_decline_area_m2": 200.0,
            "decline_percentage": 20.0,
            "decline_threshold": -0.2,
        },
        "analysis_area": task.aoi_key,
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "data_source": "fixture",
        "provenance_summary": "trusted",
        "provenance": {
            "aoi_key": task.aoi_key,
            "aoi_crs": aoi["crs"],
            "aoi_source": aoi["source"],
            "raster_source": "cached_real_sentinel2_fixture",
            "fixture_manifest": scene["fixture_manifest"],
            "period_a_collection": scene["period_a_collection"],
            "period_b_collection": scene["period_b_collection"],
        },
        "artifacts": {
            "ndvi_before": "ndvi_before",
            "ndvi_after": "ndvi_after",
            "ndvi_change": "ndvi_change",
        },
        "verifier_status": "passed",
        "execution_mode": "CACHED_REAL_SENTINEL2_RASTER",
        "selected_scene_evidence": dict(scene),
    }
    return task, aoi, scene, result


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["provenance"].update(aoi_key="wrong"),
        lambda value: value["selected_scene_evidence"].update(period_a_item_id="wrong"),
        lambda value: value.update(execution_mode="malformed"),
        lambda value: value.update(selected_scene_evidence="malformed"),
    ],
)
def test_adversarial_provenance_and_terminal_fields_fail(mutate) -> None:
    task, aoi, scene, result = _trusted_result_inputs()
    mutate(result)
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_result(
            result, task=task, aoi_evidence=aoi, scene_evidence=scene
        )


def test_trusted_ndvi_result_still_validates() -> None:
    task, aoi, scene, result = _trusted_result_inputs()
    VEGETATION_CHANGE_NDVI.validate_result(
        result, task=task, aoi_evidence=aoi, scene_evidence=scene
    )


@pytest.mark.asyncio
async def test_confirmed_skill_rejects_malicious_planner_and_replan_escape() -> None:
    async def planner(request: PlannerRequest) -> object:
        return {"steps": [{"position": 1, "instruction": "unknown_capability"}]}

    graph = build_runtime_graph(MemorySaver(), planner=PlannerNode(planner))
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "skill-test"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert result["terminal_outcome"] == "FAILED"
    assert result["failure"]["code"] == "skill_plan_invalid"


class _TestCapability:
    metadata = CapabilityMetadata(
        name="placeholder",
        description="test capability",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    def __init__(self, name: str, output: str = "{}", *, success: bool = True) -> None:
        self.metadata = self.metadata.model_copy(update={"name": name})
        self.output = output
        self.success = success

    async def execute(self, step, context) -> ExecutionResult:
        return ExecutionResult(
            step_position=step.position,
            success=self.success,
            output=self.output if self.success else None,
            error_code=None if self.success else "recoverable_plan_inadequacy",
            error_message=None if self.success else "quality failure",
        )


def _test_dispatcher(
    *, summarize_output: str = "{}", fail_search: bool = False
) -> CapabilityDispatcher:
    return CapabilityDispatcher(
        {
            name: _TestCapability(
                name,
                summarize_output if name == "summarize_change" else "{}",
                success=not (name == "search_sentinel2" and fail_search),
            )
            for name in VEGETATION_CHANGE_NDVI.capabilities
        }
    )


@pytest.mark.asyncio
async def test_incomplete_terminal_capability_output_cannot_succeed() -> None:
    graph = build_runtime_graph(
        MemorySaver(),
        capability_dispatcher=_test_dispatcher(
            summarize_output='{"analysis_type":"vegetation_change"}'
        ),
    )
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "incomplete-result"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert result["terminal_outcome"] == "FAILED"
    assert result["failure"]["code"] == "skill_result_invalid"


@pytest.mark.asyncio
async def test_non_json_terminal_capability_output_cannot_succeed() -> None:
    graph = build_runtime_graph(
        MemorySaver(),
        capability_dispatcher=_test_dispatcher(summarize_output="not-json"),
    )
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "non-json-result"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert result["terminal_outcome"] == "FAILED"
    assert result["failure"]["code"] == "skill_result_invalid"


@pytest.mark.asyncio
async def test_replan_cannot_escape_confirmed_skill() -> None:
    calls = 0

    async def planner(request: PlannerRequest) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "steps": [
                    {"position": i, "instruction": capability}
                    for i, capability in enumerate(VEGETATION_CHANGE_NDVI.capabilities, 1)
                ]
            }
        return {"steps": [{"position": 1, "instruction": "foreign_skill_step"}]}

    graph = build_runtime_graph(
        MemorySaver(),
        planner=PlannerNode(planner),
        capability_dispatcher=_test_dispatcher(fail_search=True),
    )
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "replan-escape"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert calls == 2
    assert result["terminal_outcome"] == "FAILED"
    assert result["failure"]["code"] == "skill_plan_invalid"


@pytest.mark.asyncio
async def test_tampered_checkpoint_step_is_rejected_before_capability_execution() -> None:
    graph = build_runtime_graph(MemorySaver(), capability_dispatcher=_test_dispatcher())
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    ).model_copy(
        update={
            "plan": _plan(
                "resolve_aoi", "search_sentinel2", "foreign_skill_step", "summarize_change"
            ),
            "plan_position": 2,
        }
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "tampered-plan"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert result["terminal_outcome"] == "FAILED"
    assert result["failure"]["code"] == "skill_step_invalid"


@pytest.mark.asyncio
async def test_valid_ndvi_graph_execution_reaches_success() -> None:
    graph = build_runtime_graph(MemorySaver())
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    )
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "valid-ndvi"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert result["terminal_outcome"] == "SUCCEEDED"
    assert result["verification"]["verdict"] == "PASS"


@pytest.mark.asyncio
async def test_mutually_consistent_forged_checkpoint_and_result_cannot_pass() -> None:
    task, aoi, scene, result_payload = _trusted_result_inputs()
    aoi["catalog_key"] = "forged_aoi"
    scene["fixture_manifest"] = "f" * 64
    result_payload["provenance"]["aoi_key"] = "forged_aoi"
    result_payload["provenance"]["fixture_manifest"] = "f" * 64
    result_payload["selected_scene_evidence"] = dict(scene)
    graph = build_runtime_graph(
        MemorySaver(),
        capability_dispatcher=_test_dispatcher(summarize_output=json.dumps(result_payload)),
    )
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="vegetation change",
        description="NDVI",
    ).model_copy(
        update={
            "geochange_task": task,
            "geochange_aoi_evidence": aoi,
            "geochange_evidence": scene,
            "plan": _plan(
                "resolve_aoi", "search_sentinel2", "compute_vegetation_change", "summarize_change"
            ),
            "plan_position": 3,
        }
    )
    output = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "forged-matching-result"}},
        context=RuntimeGraphContext(skill=VEGETATION_CHANGE_NDVI),
    )
    assert output["terminal_outcome"] == "FAILED"
    assert output["failure"]["code"] == "skill_result_invalid"
