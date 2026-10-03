import json
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver

from geochange.models import GeoChangeTask
from geochange.ndbi import (
    compute_cached_urban_change,
    scene_evidence,
    summarize_urban_change,
)
from geochange.skill import URBAN_CHANGE_NDBI, SkillValidationError, exploratory_ndbi_summary
from persistence.models import _validate_result_metadata
from runtime import AgentState, PlannerNode, RuntimeGraphContext, build_runtime_graph
from schema.confirmed_intent import ConfirmedIntent


def _task() -> GeoChangeTask:
    return GeoChangeTask(
        analysis_type="urban_change",
        indicator="NDBI",
        decline_threshold=None,
        decline_threshold_source=None,
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        cloud_threshold_source="user_text",
    )


def _intent() -> ConfirmedIntent:
    return ConfirmedIntent.model_validate(
        {
            "analysis_type": "urban_change",
            "indicator": "NDBI",
            "analysis_area": "wuhan_east_lake",
            "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
            "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
            "parameters": {"source": "Sentinel-2", "cloud_threshold": 30.0},
        }
    )


def test_ndbi_intent_and_skill_are_strict():
    intent = _intent()
    assert intent.runtime_task() == _task()
    assert URBAN_CHANGE_NDBI.capabilities == (
        "resolve_aoi",
        "search_sentinel2",
        "compute_urban_change",
        "summarize_change",
    )
    with pytest.raises(Exception):
        ConfirmedIntent.model_validate(
            {**intent.model_dump(mode="json"), "parameters": {"decline_threshold": -0.2}}
        )


def test_ndbi_fixture_formula_mask_and_artifacts_are_deterministic(tmp_path):
    task = _task()
    change = compute_cached_urban_change(task, scene_evidence(task), artifact_dir=tmp_path)
    metrics = summarize_urban_change(change)
    assert metrics["valid_pixels"] == 322
    assert metrics["valid_analysis_area_m2"] == 32200.0
    assert metrics["mean_delta_ndbi"] == pytest.approx(0.1404718757)
    assert set(change.artifacts) == {"ndbi_before", "ndbi_after", "ndbi_change"}
    assert all((tmp_path / name).is_file() for name in change.artifacts.values())


@pytest.mark.asyncio
async def test_ndbi_graph_start_succeeds_with_server_skill():
    async def planner(_request):
        return {
            "steps": [
                {"position": i, "instruction": name}
                for i, name in enumerate(URBAN_CHANGE_NDBI.capabilities, 1)
            ]
        }

    graph = build_runtime_graph(MemorySaver(), planner=PlannerNode(planner))
    state = AgentState.initial(
        task_id=UUID("22222222-2222-4222-8222-222222222222"),
        task_run_id=UUID("33333333-3333-4333-8333-333333333333"),
        title="urban change",
        description="NDBI",
    ).model_copy(update={"geochange_task": _task()})
    result = await graph.ainvoke(
        state.checkpoint_data(),
        config={"configurable": {"thread_id": "valid-ndbi"}},
        context=RuntimeGraphContext(skill=URBAN_CHANGE_NDBI),
    )
    assert result["terminal_outcome"] == "SUCCEEDED"
    payload = json.loads(result["execution_result"]["output"])
    assert payload["analysis_type"] == "urban_change"
    assert set(payload["metrics"]) == {
        "valid_pixels",
        "valid_analysis_area_m2",
        "mean_ndbi_period_a",
        "mean_ndbi_period_b",
        "mean_delta_ndbi",
    }
    assert "confirmed built-up" in payload["summary"]


def test_ndbi_summary_and_persistence_reject_claims():
    task = _task()
    metrics = summarize_urban_change(compute_cached_urban_change(task, scene_evidence(task)))
    summary = exploratory_ndbi_summary(metrics)
    metadata = {
        "schema_version": "taskpilot.runtime.v1",
        "analysis_type": "urban_change",
        "indicator": "NDBI",
        "summary": summary,
        "metrics": metrics,
        "analysis_area": task.aoi_key,
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "data_source": "cached_real_sentinel2_ndbi_fixture",
        "provenance_summary": "Exploratory NDBI over verified common-valid Sentinel-2 coverage; B11 native resolution is 20 m.",
        "provenance": {
            "aoi_key": "wuhan_east_lake",
            "aoi_crs": "EPSG:4326",
            "aoi_source": "taskpilot.geochange.catalog.v1",
            "raster_source": "cached_real_sentinel2_ndbi_fixture",
            "fixture_manifest": "a" * 64,
            "period_a_collection": "sentinel-2-l2a",
            "period_b_collection": "sentinel-2-l2a",
        },
        "artifact_references": {
            "ndbi_before": "ndbi_before",
            "ndbi_after": "ndbi_after",
            "ndbi_change": "ndbi_change",
        },
        "verifier_status": "passed",
    }
    assert _validate_result_metadata(metadata) == metadata
    forged = dict(metadata)
    forged["summary"] = "Confirmed urban expansion detected."
    with pytest.raises(ValueError, match="summary"):
        _validate_result_metadata(forged)
    forged = dict(metadata)
    forged["metrics"] = {**metrics, "built_up_area_m2": 0}
    with pytest.raises(ValueError, match="NDBI metrics"):
        _validate_result_metadata(forged)


def test_ndbi_terminal_validator_rejects_forged_summary():
    task = _task()
    with pytest.raises(SkillValidationError):
        URBAN_CHANGE_NDBI.validate_result(
            {
                "schema_version": "geochange.v1",
                "analysis_type": "urban_change",
                "indicator": "NDBI",
                "mode": "CACHED_REAL_SENTINEL2_NDBI_FIXTURE",
                "summary": "Confirmed urban expansion detected.",
                "metrics": summarize_urban_change(
                    compute_cached_urban_change(task, scene_evidence(task))
                ),
                "analysis_area": task.aoi_key,
                "analysis_periods": {
                    "period_a": "2023-07-01/2023-07-31",
                    "period_b": "2024-07-01/2024-07-31",
                },
                "data_source": "cached_real_sentinel2_ndbi_fixture",
                "provenance_summary": "Exploratory NDBI over verified common-valid Sentinel-2 coverage; B11 native resolution is 20 m.",
                "provenance": {
                    "aoi_key": task.aoi_key,
                    "aoi_crs": "EPSG:4326",
                    "aoi_source": "taskpilot.geochange.catalog.v1",
                    "raster_source": "cached_real_sentinel2_ndbi_fixture",
                    "fixture_manifest": "a" * 64,
                    "period_a_collection": "sentinel-2-l2a",
                    "period_b_collection": "sentinel-2-l2a",
                },
                "artifacts": {
                    "ndbi_before": "ndbi_before",
                    "ndbi_after": "ndbi_after",
                    "ndbi_change": "ndbi_change",
                },
                "verifier_status": "passed",
            }
        )
