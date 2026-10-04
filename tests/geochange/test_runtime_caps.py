import json
from datetime import date
from uuid import uuid4

import pytest

from core.settings import settings
from geochange.fixture import load_manifest, scene_evidence
from geochange.models import GeoChangeTask
from geochange.runtime_caps import (
    ComputeVegetationRuntimeCapability,
    ResolveAOIRuntimeCapability,
    SearchSentinel2RuntimeCapability,
    SummarizeChangeRuntimeCapability,
)
from geochange.stac import Sentinel2Item
from runtime.context import ContextBuilder
from runtime.planner import PlannerTaskInput
from schema.planner import PlanStep


def _context(task: GeoChangeTask, instruction: str):
    return ContextBuilder().build(
        PlannerTaskInput(title="vegetation change", description="Wuhan East Lake"),
        PlanStep(position=1, instruction=instruction),
        runtime_task_id=str(uuid4()),
        runtime_task_run_id=str(uuid4()),
        geochange_task=task,
    )


@pytest.mark.asyncio
async def test_capabilities_consume_validated_geochange_parameters():
    task = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        cloud_threshold=30,
        decline_threshold=-0.1,
    )
    resolve = await ResolveAOIRuntimeCapability().execute(
        PlanStep(position=1, instruction="resolve_aoi"), _context(task, "resolve_aoi")
    )
    assert json.loads(resolve.output)["catalog_key"] == task.aoi_key

    search = await SearchSentinel2RuntimeCapability().execute(
        PlanStep(position=1, instruction="search_sentinel2"), _context(task, "search_sentinel2")
    )
    evidence = json.loads(search.output)
    assert evidence["period_a_date"] == "2023-07-28"
    assert evidence["period_b_date"] == "2024-07-30"
    assert evidence["period_a_cloud_cover"] == "22.900553"
    assert evidence["fixture_manifest"]

    context = _context(task, "summarize_change")
    context = context.model_copy(
        update={
            "geochange_evidence": evidence,
            "geochange_aoi_evidence": json.loads(resolve.output),
        }
    )
    summary = await SummarizeChangeRuntimeCapability().execute(
        PlanStep(position=1, instruction="summarize_change"), context
    )
    payload = json.loads(summary.output)
    assert payload["metrics"]["decline_threshold"] == -0.1
    assert payload["analysis_area"] == task.aoi_key
    assert payload["analysis_periods"] == {
        "period_a": "2023-07-01/2023-07-31",
        "period_b": "2024-07-01/2024-07-31",
    }
    assert payload["data_source"] == "cached_real_sentinel2_fixture"
    assert payload["provenance"]["aoi_key"] == task.aoi_key
    assert payload["provenance_summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", [False, True])
async def test_live_metadata_binds_exact_cached_scene_or_fails_closed(monkeypatch, mismatch):
    task = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )
    manifest = load_manifest()

    def search(aoi, period, threshold):
        scene = manifest["scenes"]["period_a" if period == task.period_a else "period_b"]
        return Sentinel2Item(
            item_id=scene["item_id"] + ("-different" if mismatch else ""),
            acquisition_date=date.fromisoformat(scene["acquisition_date"]),
            cloud_cover=scene["cloud_cover"],
            collection=scene["collection"],
            red_asset=scene["red_asset_identity"]["href"],
            nir_asset=scene["nir_asset_identity"]["href"],
            query_start=period.start,
            query_end=period.end,
        )

    monkeypatch.setattr(settings, "GEOCHANGE_LIVE_STAC", True)
    monkeypatch.setattr("geochange.runtime_caps.search_sentinel2", search)
    step = PlanStep(position=1, instruction="search_sentinel2")
    selected = await SearchSentinel2RuntimeCapability().execute(
        step, _context(task, step.instruction)
    )
    evidence = json.loads(selected.output)
    context = _context(task, "compute_vegetation_change").model_copy(
        update={"geochange_evidence": evidence}
    )
    result = await ComputeVegetationRuntimeCapability().execute(step, context)
    assert result.success == (not mismatch)
    if mismatch:
        assert result.error_code == "geochange_provenance_invalid"
    else:
        assert evidence == scene_evidence(task)


@pytest.mark.asyncio
async def test_cached_real_summary_remains_bounded_with_maximum_llm_prose(monkeypatch, tmp_path):
    task = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )

    async def explain(self, evidence):
        return "x" * 500

    monkeypatch.setattr(settings, "GEOCHANGE_LIVE_LLM", True)
    monkeypatch.setattr(settings, "USE_FAKE_MODEL", False)
    monkeypatch.setattr("geochange.runtime_caps.get_model", lambda model: object())
    monkeypatch.setattr("geochange.runtime_caps.GeoChangeLLM.explain", explain)
    monkeypatch.setattr("geochange.runtime_caps.ARTIFACT_ROOT", tmp_path)
    context = _context(task, "summarize_change").model_copy(
        update={
            "geochange_evidence": scene_evidence(task),
            "geochange_aoi_evidence": {
                "catalog_key": task.aoi_key,
                "crs": "EPSG:4326",
                "source": "taskpilot.geochange.catalog.v1",
            },
        }
    )
    result = await SummarizeChangeRuntimeCapability().execute(
        PlanStep(position=1, instruction="summarize_change"), context
    )
    assert result.success
    assert len(result.output or "") <= 2000
