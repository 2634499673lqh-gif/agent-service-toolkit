"""One bounded, deterministic GeoChange execution path for the MVP."""

from pathlib import Path
from typing import Any

from .models import GeoChangeResult, GeoChangeTask
from .raster import compute_vegetation_change
from .summary import summarize_change
from .verifier import verify_change


def run_local_analysis(
    task: GeoChangeTask,
    *,
    red_a: Any,
    nir_a: Any,
    red_b: Any,
    nir_b: Any,
    artifact_root: str | Path,
    provenance: dict[str, str] | None = None,
) -> GeoChangeResult:
    """Execute the local real-raster mode; inputs are supplied by trusted runtime code."""

    root = Path(artifact_root).resolve()
    change = compute_vegetation_change(
        red_a, nir_a, red_b, nir_b, artifact_dir=root
    )
    verification = verify_change(change, artifacts=change.artifacts, artifact_root=root)
    if verification["status"] != "passed":
        raise ValueError(f"GeoChange verification failed: {verification['code']}")
    metrics = summarize_change(change, task)
    summary = (
        f"Vegetation change analysis for {task.aoi_key}: mean NDVI changed "
        f"from {metrics['mean_ndvi_period_a']:.3f} to {metrics['mean_ndvi_period_b']:.3f}; "
        f"{metrics['decline_percentage']:.1f}% of valid area exceeded the decline threshold."
    )
    return GeoChangeResult(
        mode=task.data_mode,
        summary=summary,
        metrics={**metrics, "valid_pixel_ratio": verification["valid_pixel_ratio"]},
        provenance=provenance or {},
        artifacts=change.artifacts,
        verifier_status="passed",
    )


__all__ = ["run_local_analysis"]
