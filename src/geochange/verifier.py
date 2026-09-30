from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .models import GeoChangeTask
from .raster import VegetationChange
from .stac import STAC_COLLECTION


def verify_change(
    change: VegetationChange,
    *,
    artifacts: dict[str, str] | None = None,
    artifact_root: str | Path | None = None,
    task: GeoChangeTask | None = None,
    aoi_evidence: dict[str, Any] | None = None,
    scene_evidence: dict[str, str] | None = None,
    execution_mode: str | None = None,
    raster_source: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate numerical output and, when supplied, the complete Product evidence contract."""

    if task is not None:
        if not isinstance(aoi_evidence, dict) or len(aoi_evidence) > 8 or any(
            not isinstance(key, str) or not isinstance(value, str) or len(key) > 64 or len(value) > 256
            for key, value in aoi_evidence.items()
        ):
            return {"status": "failed", "code": "aoi_evidence_invalid"}
        if not aoi_evidence or aoi_evidence.get("catalog_key") != task.aoi_key:
            return {"status": "failed", "code": "aoi_evidence_invalid"}
        if aoi_evidence.get("crs") != "EPSG:4326" or not aoi_evidence.get("source"):
            return {"status": "failed", "code": "aoi_provenance_invalid"}
        if aoi_evidence.get("source") != "taskpilot.geochange.catalog.v1":
            return {"status": "failed", "code": "aoi_provenance_invalid"}
        if not scene_evidence:
            return {"status": "failed", "code": "scene_evidence_missing"}
        if len(scene_evidence) > 16 or any(
            not isinstance(key, str) or not isinstance(value, str) or len(key) > 64 or len(value) > 256
            for key, value in scene_evidence.items()
        ):
            return {"status": "failed", "code": "scene_evidence_invalid"}
        for period in ("a", "b"):
            required = (
                f"period_{period}_item_id", f"period_{period}_date",
                f"period_{period}_collection", f"period_{period}_cloud_cover",
                f"period_{period}_red", f"period_{period}_nir",
            )
            if any(not scene_evidence.get(key) for key in required):
                return {"status": "failed", "code": f"period_{period}_evidence_missing"}
            if scene_evidence[f"period_{period}_collection"] != STAC_COLLECTION:
                return {"status": "failed", "code": "scene_collection_invalid"}
            try:
                date_value = date.fromisoformat(scene_evidence[f"period_{period}_date"])
                cloud = float(scene_evidence[f"period_{period}_cloud_cover"])
            except (TypeError, ValueError):
                return {"status": "failed", "code": "scene_metadata_invalid"}
            selected_period = task.period_a if period == "a" else task.period_b
            if not selected_period.start <= date_value <= selected_period.end:
                return {"status": "failed", "code": f"period_{period}_date_invalid"}
            if not np.isfinite(cloud):
                return {"status": "failed", "code": "scene_metadata_invalid"}
            if not 0 <= cloud <= task.cloud_threshold:
                return {"status": "failed", "code": f"period_{period}_cloud_invalid"}
        if (
            scene_evidence["period_a_item_id"] == scene_evidence["period_b_item_id"]
            or scene_evidence["period_a_date"] == scene_evidence["period_b_date"]
        ):
            return {"status": "failed", "code": "scenes_not_distinct"}
        if execution_mode not in {"REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE", "REAL_STAC_LOCAL_FIXTURE", "CACHED_REAL_METADATA"}:
            return {"status": "failed", "code": "execution_mode_invalid"}
        if execution_mode == "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE":
            if raster_source != "local_real_raster_fixture":
                return {"status": "failed", "code": "provenance_invalid"}
            if any(scene_evidence.get(f"period_{period}_{band}") != "validated" for period in ("a", "b") for band in ("red", "nir")):
                return {"status": "failed", "code": "live_scene_evidence_invalid"}
        if not metrics:
            return {"status": "failed", "code": "metrics_missing"}
        required_metrics = {"valid_pixels", "valid_analysis_area_m2", "mean_ndvi_period_a", "mean_ndvi_period_b", "mean_delta_ndvi", "significant_decline_area_m2", "decline_percentage", "decline_threshold"}
        if not required_metrics.issubset(metrics):
            return {"status": "failed", "code": "metrics_incomplete"}
        try:
            values = {key: float(metrics[key]) for key in required_metrics}
        except (TypeError, ValueError):
            return {"status": "failed", "code": "metrics_invalid"}
        if not all(np.isfinite(value) for value in values.values()):
            return {"status": "failed", "code": "metrics_non_finite"}
        if not all(-1.00001 <= values[key] <= 1.00001 for key in ("mean_ndvi_period_a", "mean_ndvi_period_b", "mean_delta_ndvi")):
            return {"status": "failed", "code": "ndvi_range_invalid"}
        if values["valid_analysis_area_m2"] <= 0 or values["significant_decline_area_m2"] < 0 or values["significant_decline_area_m2"] > values["valid_analysis_area_m2"]:
            return {"status": "failed", "code": "area_invalid"}
        if not 0 <= values["decline_percentage"] <= 100 or not -1 <= values["decline_threshold"] <= 0:
            return {"status": "failed", "code": "percentage_or_threshold_invalid"}
        if values["valid_pixels"] <= 0 or not values["valid_pixels"].is_integer():
            return {"status": "failed", "code": "empty_valid_pixels"}
        valid_pixels = int(np.count_nonzero(change.valid_mask))
        if valid_pixels == 0:
            return {"status": "failed", "code": "empty_valid_pixels"}
        if int(values["valid_pixels"]) != valid_pixels:
            return {"status": "failed", "code": "valid_pixel_count_mismatch"}
        if not np.isclose(values["valid_analysis_area_m2"], valid_pixels * change.pixel_area_m2):
            return {"status": "failed", "code": "valid_area_mismatch"}
        if not np.isclose(
            values["mean_delta_ndvi"],
            values["mean_ndvi_period_b"] - values["mean_ndvi_period_a"],
            atol=1e-5,
        ):
            return {"status": "failed", "code": "delta_metric_mismatch"}
        if not np.isclose(
            values["decline_percentage"],
            values["significant_decline_area_m2"] / values["valid_analysis_area_m2"] * 100,
            atol=1e-5,
        ):
            return {"status": "failed", "code": "decline_metric_mismatch"}
        required_artifacts = {"ndvi_before", "ndvi_after", "ndvi_change"}
        if not artifacts or set(artifacts) != required_artifacts:
            return {"status": "failed", "code": "artifacts_incomplete"}
    valid_ratio = float(np.count_nonzero(change.valid_mask) / change.valid_mask.size)
    if not 0 < valid_ratio <= 1:
        return {"status": "failed", "code": "empty_valid_pixels", "valid_pixel_ratio": valid_ratio}
    if not np.all(np.isfinite(change.delta[change.valid_mask])):
        return {"status": "failed", "code": "non_finite_ndvi", "valid_pixel_ratio": valid_ratio}
    if artifacts:
        root = None if artifact_root is None else Path(artifact_root).resolve()
        if task is not None and root is None:
            return {"status": "failed", "code": "artifact_root_missing", "valid_pixel_ratio": valid_ratio}
        for name, value in artifacts.items():
            if task is not None and value != f"{name}.png":
                return {"status": "failed", "code": "artifact_reference_invalid", "valid_pixel_ratio": valid_ratio}
            path = Path(value)
            if path.is_absolute() or path.name != value:
                return {"status": "failed", "code": "artifact_reference_invalid", "valid_pixel_ratio": valid_ratio}
            path = (root / path).resolve() if root is not None else path
            if root is not None and root not in path.parents:
                return {"status": "failed", "code": "artifact_reference_invalid", "valid_pixel_ratio": valid_ratio}
            if not path.is_file() or not 0 < path.stat().st_size <= 2_000_000:
                return {"status": "failed", "code": "artifact_missing", "valid_pixel_ratio": valid_ratio}
            try:
                with Image.open(path) as image:
                    if image.format != "PNG":
                        return {"status": "failed", "code": "artifact_invalid", "valid_pixel_ratio": valid_ratio}
                    image.verify()
            except (OSError, ValueError):
                return {"status": "failed", "code": "artifact_invalid", "valid_pixel_ratio": valid_ratio}
    return {"status": "passed", "valid_pixel_ratio": valid_ratio}


__all__ = ["verify_change"]
