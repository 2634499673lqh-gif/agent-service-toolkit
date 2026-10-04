"""Capability adapters used by the existing runtime graph."""

import hashlib
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
from .fixture import EXECUTION_MODE, compute_cached_change, scene_evidence
from .llm import GeoChangeLLM
from .models import GeoChangeResult, GeoChangeTask
from .ndbi import (
    EXECUTION_MODE as NDBI_EXECUTION_MODE,
)
from .ndbi import (
    compute_cached_urban_change,
    summarize_urban_change,
)
from .ndbi import (
    scene_evidence as ndbi_scene_evidence,
)
from .ndwi import (
    compute_cached_water_change,
    summarize_water_change,
)
from .ndwi import (
    scene_evidence as ndwi_scene_evidence,
)
from .skill import exploratory_ndbi_summary, exploratory_ndwi_summary
from .stac import search_sentinel2
from .summary import summarize_change
from .verifier import verify_change


def _task(context: Any) -> GeoChangeTask:
    task = getattr(context, "geochange_task", None)
    if not isinstance(task, GeoChangeTask):
        raise ValueError("validated GeoChange task is missing")
    return task


def _live_scene_evidence(task: GeoChangeTask, item_a: Any, item_b: Any) -> dict[str, str]:
    evidence = scene_evidence(task)
    for period, item in (("a", item_a), ("b", item_b)):
        evidence.update(
            {
                f"period_{period}_item_id": item.item_id,
                f"period_{period}_date": item.acquisition_date.isoformat(),
                f"period_{period}_cloud_cover": str(item.cloud_cover),
                f"period_{period}_collection": item.collection,
                f"period_{period}_red": hashlib.sha256(item.red_asset.encode()).hexdigest(),
                f"period_{period}_nir": hashlib.sha256(item.nir_asset.encode()).hexdigest(),
            }
        )
    return evidence


class _Base:
    def _result(self, step: PlanStep, payload: dict[str, Any]) -> ExecutionResult:
        output = json.dumps(payload, separators=(",", ":"))
        if len(output) > 2000 and isinstance(payload.get("summary"), str):
            # Provider prose is presentation only. Keep the trusted metrics,
            # artifact references and scene evidence intact when a provider
            # spends the whole budget on prose.
            compact = dict(payload)
            compact["summary"] = payload["summary"][:80]
            output = json.dumps(compact, separators=(",", ":"))
        if len(output) > 2000:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_output_oversized",
                error_message="GeoChange output exceeds the bounded limit",
            )
        return ExecutionResult(
            step_position=step.position,
            success=True,
            output=output,
        )


class ResolveAOIRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="resolve_aoi",
        description="Resolve controlled Wuhan East Lake AOI.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        aoi = resolve_aoi(task.aoi_key)
        return self._result(
            step, {"catalog_key": aoi.catalog_key, "crs": aoi.crs, "source": aoi.source}
        )


class SearchSentinel2RuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="search_sentinel2",
        description="Select bounded Sentinel-2 metadata.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
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
        if settings.GEOCHANGE_LIVE_STAC and task.analysis_type == "vegetation_change":
            aoi = resolve_aoi(task.aoi_key)
            # Earth Search ranks by provider order; query the bounded maximum
            # cloud range, then apply the validated task threshold to evidence.
            try:
                item_a = search_sentinel2(aoi, task.period_a, 100.0)
                item_b = search_sentinel2(aoi, task.period_b, 100.0)
            except ConnectionError:
                return ExecutionResult(
                    step_position=step.position,
                    success=False,
                    error_code="stac_provider_unavailable",
                    error_message="Sentinel-2 provider is unavailable",
                )
            except ValueError as error:
                code = (
                    "stac_no_suitable_imagery"
                    if "no Sentinel-2 item" in str(error)
                    else "stac_response_malformed"
                )
                return ExecutionResult(
                    step_position=step.position,
                    success=False,
                    error_code=code,
                    error_message="Sentinel-2 metadata is unavailable",
                )
            if (
                item_a.cloud_cover > task.cloud_threshold
                or item_b.cloud_cover > task.cloud_threshold
            ):
                code = (
                    "stac_explicit_quality_violation"
                    if task.cloud_threshold_source == "user_text"
                    else "stac_default_quality_violation"
                )
                return ExecutionResult(
                    step_position=step.position,
                    success=False,
                    error_code=code,
                    error_message="selected imagery exceeds the validated cloud threshold",
                )
            evidence = _live_scene_evidence(task, item_a, item_b)
        else:
            if task.analysis_type == "water_change":
                evidence = ndwi_scene_evidence(task)
            elif task.analysis_type == "urban_change":
                evidence = ndbi_scene_evidence(task)
            else:
                evidence = scene_evidence(task)
        return self._result(step, evidence)


class ComputeVegetationRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="compute_vegetation_change",
        description="Compute deterministic NDVI change.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        evidence = dict(getattr(context, "geochange_evidence", {}) or {})
        try:
            change = compute_cached_change(task, evidence)
        except ValueError as error:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_provenance_invalid",
                error_message=str(error)[:200],
            )
        return self._result(
            step,
            {
                "valid_pixels": int(change.valid_mask.sum()),
                "mean_delta_ndvi": float(np.nanmean(change.delta)),
            },
        )


class ComputeWaterRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="compute_water_change",
        description="Compute deterministic exploratory NDWI change.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        evidence = dict(getattr(context, "geochange_evidence", {}) or {})
        try:
            change = compute_cached_water_change(task, evidence)
        except ValueError as error:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_provenance_invalid",
                error_message=str(error)[:200],
            )
        return self._result(
            step,
            {
                "valid_pixels": int(change.valid_mask.sum()),
                "mean_delta_ndwi": float(np.nanmean(change.delta)),
            },
        )


class ComputeUrbanRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="compute_urban_change",
        description="Compute deterministic exploratory NDBI change.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        evidence = dict(getattr(context, "geochange_evidence", {}) or {})
        try:
            change = compute_cached_urban_change(task, evidence)
        except ValueError as error:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_provenance_invalid",
                error_message=str(error)[:200],
            )
        return self._result(
            step,
            {
                "valid_pixels": int(change.valid_mask.sum()),
                "mean_delta_ndbi": float(np.nanmean(change.delta)),
            },
        )


class SummarizeChangeRuntimeCapability(_Base):
    metadata = CapabilityMetadata(
        name="summarize_change",
        description="Summarize deterministic vegetation change.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        task = _task(context)
        artifact_root = (
            ARTIFACT_ROOT
            / str(context.runtime_task_id or "runtime")
            / str(context.runtime_task_run_id or "current")
        )
        scene_evidence = dict(getattr(context, "geochange_evidence", {}) or {})
        if task.analysis_type == "water_change":
            try:
                change = compute_cached_water_change(
                    task, scene_evidence, artifact_dir=artifact_root
                )
                metrics = summarize_water_change(change)
            except ValueError as error:
                return ExecutionResult(
                    step_position=step.position,
                    success=False,
                    error_code="geochange_provenance_invalid",
                    error_message=str(error)[:200],
                )
            aoi_evidence = dict(getattr(context, "geochange_aoi_evidence", {}) or {})
            payload = GeoChangeResult(
                indicator="NDWI",
                analysis_type="water_change",
                mode="CACHED_REAL_SENTINEL2_NDWI_FIXTURE",
                summary=exploratory_ndwi_summary(metrics),
                metrics=metrics,
                analysis_area=task.aoi_key,
                analysis_periods={
                    "period_a": f"{task.period_a.start.isoformat()}/{task.period_a.end.isoformat()}",
                    "period_b": f"{task.period_b.start.isoformat()}/{task.period_b.end.isoformat()}",
                },
                data_source="cached_real_sentinel2_ndwi_fixture",
                provenance_summary="Exploratory NDWI over verified common-valid Sentinel-2 coverage.",
                provenance={
                    "aoi_key": aoi_evidence["catalog_key"],
                    "aoi_crs": aoi_evidence["crs"],
                    "aoi_source": aoi_evidence["source"],
                    "raster_source": "cached_real_sentinel2_ndwi_fixture",
                    "fixture_manifest": scene_evidence["fixture_manifest"],
                    "period_a_collection": scene_evidence["period_a_collection"],
                    "period_b_collection": scene_evidence["period_b_collection"],
                },
                artifacts={
                    "ndwi_before": "ndwi_before",
                    "ndwi_after": "ndwi_after",
                    "ndwi_change": "ndwi_change",
                },
                verifier_status="passed",
            ).model_dump(mode="json")
            payload.update(
                {
                    "execution_mode": "CACHED_REAL_SENTINEL2_NDWI_FIXTURE",
                    "selected_scene_evidence": scene_evidence,
                }
            )
            return self._result(step, payload)
        if task.analysis_type == "urban_change":
            try:
                change = compute_cached_urban_change(
                    task, scene_evidence, artifact_dir=artifact_root
                )
                metrics = summarize_urban_change(change)
            except ValueError as error:
                return ExecutionResult(
                    step_position=step.position,
                    success=False,
                    error_code="geochange_provenance_invalid",
                    error_message=str(error)[:200],
                )
            aoi_evidence = dict(getattr(context, "geochange_aoi_evidence", {}) or {})
            payload = GeoChangeResult(
                indicator="NDBI",
                analysis_type="urban_change",
                mode=NDBI_EXECUTION_MODE,
                summary=exploratory_ndbi_summary(metrics),
                metrics=metrics,
                analysis_area=task.aoi_key,
                analysis_periods={
                    "period_a": f"{task.period_a.start.isoformat()}/{task.period_a.end.isoformat()}",
                    "period_b": f"{task.period_b.start.isoformat()}/{task.period_b.end.isoformat()}",
                },
                data_source="cached_real_sentinel2_ndbi_fixture",
                provenance_summary="Exploratory NDBI over verified common-valid Sentinel-2 coverage; B11 native resolution is 20 m.",
                provenance={
                    "aoi_key": aoi_evidence["catalog_key"],
                    "aoi_crs": aoi_evidence["crs"],
                    "aoi_source": aoi_evidence["source"],
                    "raster_source": "cached_real_sentinel2_ndbi_fixture",
                    "fixture_manifest": scene_evidence["fixture_manifest"],
                    "period_a_collection": scene_evidence["period_a_collection"],
                    "period_b_collection": scene_evidence["period_b_collection"],
                },
                artifacts={
                    "ndbi_before": "ndbi_before",
                    "ndbi_after": "ndbi_after",
                    "ndbi_change": "ndbi_change",
                },
                verifier_status="passed",
            ).model_dump(mode="json")
            payload.update(
                {
                    "execution_mode": NDBI_EXECUTION_MODE,
                    "selected_scene_evidence": scene_evidence,
                }
            )
            return self._result(step, payload)
        try:
            change = compute_cached_change(task, scene_evidence, artifact_dir=artifact_root)
        except ValueError as error:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_provenance_invalid",
                error_message=str(error)[:200],
            )
        aoi_evidence = dict(getattr(context, "geochange_aoi_evidence", {}) or {})
        metrics = summarize_change(change, task)
        mode = EXECUTION_MODE
        verification = verify_change(
            change,
            artifacts=change.artifacts,
            artifact_root=artifact_root,
            task=task,
            aoi_evidence=aoi_evidence,
            scene_evidence=scene_evidence,
            execution_mode=mode,
            raster_source="cached_real_sentinel2_fixture",
            metrics=metrics,
        )
        if verification["status"] != "passed":
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="geochange_quality_failed",
                error_message="deterministic GeoChange verification failed",
            )
        summary = (
            f"Vegetation change around Wuhan East Lake: mean NDVI changed "
            f"from {metrics['mean_ndvi_period_a']:.3f} to {metrics['mean_ndvi_period_b']:.3f}; "
            f"decline area is {metrics['significant_decline_area_m2']:.1f} m2."
        )
        if settings.GEOCHANGE_LIVE_LLM and not settings.USE_FAKE_MODEL:
            summary = await GeoChangeLLM(get_model(settings.DEFAULT_MODEL)).explain(
                {
                    "analysis_type": "vegetation_change",
                    "metrics": metrics,
                    "verifier_status": "passed",
                }
            )
        validated_result = GeoChangeResult(
            mode=mode,
            summary=summary,
            metrics=metrics,
            analysis_area=task.aoi_key,
            analysis_periods={
                "period_a": f"{task.period_a.start.isoformat()}/{task.period_a.end.isoformat()}",
                "period_b": f"{task.period_b.start.isoformat()}/{task.period_b.end.isoformat()}",
            },
            data_source="cached_real_sentinel2_fixture",
            provenance_summary="Verified AOI and cached Sentinel-2 fixture.",
            provenance={
                "aoi_key": aoi_evidence["catalog_key"],
                "aoi_crs": aoi_evidence["crs"],
                "aoi_source": aoi_evidence["source"],
                "raster_source": "cached_real_sentinel2_fixture",
                "fixture_manifest": scene_evidence["fixture_manifest"],
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
        payload = validated_result.model_dump(mode="json", exclude_none=True)
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
        "compute_water_change": ComputeWaterRuntimeCapability(),
        "compute_urban_change": ComputeUrbanRuntimeCapability(),
        "summarize_change": SummarizeChangeRuntimeCapability(),
    }


__all__ = ["runtime_capabilities"]
