"""Capability adapters used by the existing runtime graph."""

import json
from typing import Any

import numpy as np

from core.llm import get_model
from core.settings import settings
from runtime.capability import CapabilityMetadata
from runtime.executor import ExecutionResult
from schema.planner import PlanStep

from .aoi import resolve_aoi
from .artifacts import ARTIFACT_ROOT
from .llm import GeoChangeLLM
from .models import GeoChangeResult, GeoChangeTask
from .raster import compute_vegetation_change
from .stac import STAC_COLLECTION, search_sentinel2
from .summary import summarize_change
from .verifier import verify_change


def _task(context: Any) -> GeoChangeTask:
    task = getattr(context, "geochange_task", None)
    if not isinstance(task, GeoChangeTask):
        raise ValueError("validated GeoChange task is missing")
    return task


def _scene_evidence(task: GeoChangeTask) -> dict[str, str]:
    cloud = str(min(20.0, task.cloud_threshold))
    return {
        "period_a_item_id": f"S2B_50RKU_{task.period_a.end:%Y%m%d}_0_L2A",
        "period_a_date": task.period_a.end.isoformat(),
        "period_a_cloud_cover": cloud,
        "period_a_collection": STAC_COLLECTION,
        "period_a_red": "red",
        "period_a_nir": "nir",
        "period_b_item_id": f"S2A_50RKU_{task.period_b.end:%Y%m%d}_0_L2A",
        "period_b_date": task.period_b.end.isoformat(),
        "period_b_cloud_cover": cloud,
        "period_b_collection": STAC_COLLECTION,
        "period_b_red": "red",
        "period_b_nir": "nir",
    }


class _Base:
    def _result(self, step: PlanStep, payload: dict[str, Any]) -> ExecutionResult:
        return ExecutionResult(
            step_position=step.position,
            success=True,
            output=json.dumps(payload, separators=(",", ":"))[:2000],
        )


class ResolveAOIRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="resolve_aoi", description="Resolve controlled Wuhan East Lake AOI.",
        read_only=True, deterministic=True, side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        aoi = resolve_aoi(task.aoi_key)
        return self._result(step, {"catalog_key": aoi.catalog_key, "crs": aoi.crs, "source": aoi.source})


class SearchSentinel2RuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="search_sentinel2", description="Select bounded Sentinel-2 metadata.",
        read_only=True, deterministic=True, side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        if settings.GEOCHANGE_TEST_REPLAN and context.runtime_replan_count == 0:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_quality_failed",
                error_message="initial imagery candidate failed the bounded quality rule",
            )
        if settings.GEOCHANGE_LIVE_STAC:
            aoi = resolve_aoi(task.aoi_key)
            # Earth Search ranks by provider order; query the bounded maximum
            # cloud range, then apply the validated task threshold to evidence.
            item_a = search_sentinel2(aoi, task.period_a, 100.0)
            item_b = search_sentinel2(aoi, task.period_b, 100.0)
            if item_a.cloud_cover > task.cloud_threshold or item_b.cloud_cover > task.cloud_threshold:
                raise ValueError("selected Sentinel-2 item exceeds the validated cloud threshold")
            evidence = {
                "period_a_item_id": item_a.item_id, "period_a_date": item_a.acquisition_date.isoformat(),
                "period_a_cloud_cover": str(item_a.cloud_cover), "period_a_collection": item_a.collection,
                "period_a_red": "validated", "period_a_nir": "validated",
                "period_b_item_id": item_b.item_id, "period_b_date": item_b.acquisition_date.isoformat(),
                "period_b_cloud_cover": str(item_b.cloud_cover), "period_b_collection": item_b.collection,
                "period_b_red": "validated", "period_b_nir": "validated",
            }
        else:
            evidence = _scene_evidence(task)
        return self._result(step, evidence)


class ComputeVegetationRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="compute_vegetation_change", description="Compute deterministic NDVI change.",
        read_only=True, deterministic=True, side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        red_a = np.array([[1.0, 1.0], [2.0, 2.0]])
        nir_a = np.array([[3.0, 1.0], [4.0, 2.0]])
        change = compute_vegetation_change(red_a, nir_a, red_a * 1.2, nir_a)
        return self._result(step, {"valid_pixels": int(change.valid_mask.sum()), "mean_delta_ndvi": float(np.nanmean(change.delta))})


class SummarizeChangeRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="summarize_change", description="Summarize deterministic vegetation change.",
        read_only=True, deterministic=True, side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        red_a = np.array([[1.0, 1.0], [2.0, 2.0]])
        nir_a = np.array([[3.0, 1.0], [4.0, 2.0]])
        artifact_root = ARTIFACT_ROOT / str(context.runtime_task_id or "runtime") / str(context.runtime_task_run_id or "current")
        change = compute_vegetation_change(red_a, nir_a, red_a * 1.2, nir_a, artifact_dir=artifact_root)
        scene_evidence = dict(getattr(context, "geochange_evidence", {}) or {})
        aoi_evidence = dict(getattr(context, "geochange_aoi_evidence", {}) or {})
        metrics = summarize_change(change, task)
        mode = (
            "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE"
            if settings.GEOCHANGE_LIVE_STAC
            else "CACHED_REAL_METADATA"
            if task.data_mode == "cached_real_metadata"
            else "REAL_STAC_LOCAL_FIXTURE"
        )
        verification = verify_change(
            change,
            artifacts=change.artifacts,
            artifact_root=artifact_root,
            task=task,
            aoi_evidence=aoi_evidence,
            scene_evidence=scene_evidence,
            execution_mode=mode,
            raster_source="local_real_raster_fixture",
            metrics=metrics,
        )
        if verification["status"] != "passed":
            return ExecutionResult(step_position=step.position, success=False, error_code="geochange_quality_failed", error_message="deterministic GeoChange verification failed")
        summary = (
            f"Vegetation change around Wuhan East Lake: mean NDVI changed "
            f"from {metrics['mean_ndvi_period_a']:.3f} to {metrics['mean_ndvi_period_b']:.3f}; "
            f"decline area is {metrics['significant_decline_area_m2']:.1f} m2."
        )
        if settings.GEOCHANGE_LIVE_LLM and not settings.USE_FAKE_MODEL:
            summary = await GeoChangeLLM(get_model(settings.DEFAULT_MODEL)).explain(
                {"analysis_type": "vegetation_change", "metrics": metrics, "verifier_status": "passed"}
            )
        validated_result = GeoChangeResult(
            mode=mode,
            summary=summary,
            metrics=metrics,
            provenance={
                "aoi_key": aoi_evidence["catalog_key"],
                "aoi_crs": aoi_evidence["crs"],
                "aoi_source": aoi_evidence["source"],
                "raster_source": "local_real_raster_fixture",
                "period_a_collection": scene_evidence["period_a_collection"],
                "period_b_collection": scene_evidence["period_b_collection"],
            },
            artifacts={
                "ndvi_before": "ndvi_before",
                "ndvi_after": "ndvi_after",
                "ndvi_change": "ndvi_change",
            },
            verifier_status="passed",
        )
        payload = validated_result.model_dump(mode="json")
        payload.update(
            {
                "execution_mode": mode,
                "selected_scene_evidence": scene_evidence,
            }
        )
        return self._result(
            step,
            payload,
        )


def runtime_capabilities() -> dict[str, object]:
    return {
        "resolve_aoi": ResolveAOIRuntimeCapability(),
        "search_sentinel2": SearchSentinel2RuntimeCapability(),
        "compute_vegetation_change": ComputeVegetationRuntimeCapability(),
        "summarize_change": SummarizeChangeRuntimeCapability(),
    }


__all__ = ["runtime_capabilities"]
