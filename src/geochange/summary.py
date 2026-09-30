import numpy as np

from .models import GeoChangeTask
from .raster import VegetationChange


def summarize_change(change: VegetationChange, task: GeoChangeTask) -> dict[str, float | int]:
    mask = np.asarray(change.valid_mask, dtype=bool)
    if mask.shape != change.delta.shape or not np.any(mask):
        raise ValueError("summary requires non-empty valid pixels")
    delta = change.delta[mask]
    a = change.ndvi_a[mask]
    b = change.ndvi_b[mask]
    decline_pixels = int(np.count_nonzero(delta <= task.decline_threshold))
    valid_pixels = int(mask.sum())
    area = valid_pixels * change.pixel_area_m2
    decline_area = decline_pixels * change.pixel_area_m2
    percentage = decline_pixels / valid_pixels * 100.0
    values = {
        "valid_pixels": valid_pixels,
        "valid_analysis_area_m2": area,
        "mean_ndvi_period_a": float(np.mean(a)),
        "mean_ndvi_period_b": float(np.mean(b)),
        "mean_delta_ndvi": float(np.mean(delta)),
        "significant_decline_area_m2": decline_area,
        "decline_percentage": percentage,
        "decline_threshold": task.decline_threshold,
    }
    if not all(np.isfinite(float(value)) for value in values.values()):
        raise ValueError("summary contains a non-finite value")
    return values


__all__ = ["summarize_change"]
