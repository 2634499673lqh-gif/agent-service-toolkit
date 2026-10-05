"""Implementation B: NDVI calculation and evidence-bound product output.

This module consumes only the server-owned ``PreparedPeriodPair`` handoff from
Implementation A.  It deliberately has no STAC, URL, QA, or reprojection code.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .landsat import PreparedPeriodPair

try:  # rasterio is a production dependency, but keep import errors explicit.
    import rasterio
    from rasterio.transform import Affine
except ImportError:  # pragma: no cover - exercised only in a broken image
    rasterio = None
    Affine = None


EXECUTION_MODE = "real_stac_landsat_local"
NDVI_ARTIFACTS = ("ndvi_before_raster", "ndvi_after_raster", "ndvi_change_raster")
MASK_ARTIFACTS = (
    "ndvi_valid_before",
    "ndvi_valid_after",
    "ndvi_common_comparison",
)
PNG_ARTIFACTS = ("ndvi_before", "ndvi_after", "ndvi_change")
FINAL_NDVI_COVERAGE_GATE = 60.0  # provisional until user/reviewer freeze
FINAL_COMMON_COVERAGE_GATE = 50.0  # provisional until user/reviewer freeze


@dataclass(frozen=True)
class LandsatNDVIProduct:
    ndvi_before: np.ndarray
    ndvi_after: np.ndarray
    delta: np.ndarray
    final_ndvi_valid_mask_a: np.ndarray
    final_ndvi_valid_mask_b: np.ndarray
    final_common_comparison_mask: np.ndarray
    metrics: dict[str, float | int]
    artifacts: dict[str, str]
    provenance: dict[str, Any]


def _period_ndvi(
    red: np.ndarray, nir: np.ndarray, preparation: np.ndarray, aoi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    red = np.asarray(red, dtype=np.float32)
    nir = np.asarray(nir, dtype=np.float32)
    preparation = np.asarray(preparation, dtype=bool)
    aoi = np.asarray(aoi, dtype=bool)
    if red.shape != nir.shape or red.shape != preparation.shape or red.shape != aoi.shape:
        raise ValueError("prepared Red/NIR/masks must share one shape")
    denominator = nir + red
    finite = np.isfinite(red) & np.isfinite(nir)
    valid = aoi & preparation & finite & np.isfinite(denominator)
    valid &= np.abs(denominator) > 1e-6
    ndvi = np.full(red.shape, np.nan, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        ndvi[valid] = (nir[valid] - red[valid]) / denominator[valid]
    valid &= np.isfinite(ndvi)
    valid &= (ndvi >= -1.0 - 1e-5) & (ndvi <= 1.0 + 1e-5)
    ndvi[~valid] = np.nan
    return ndvi, valid


def _coverage(valid: np.ndarray, denominator: int) -> float:
    return float(np.count_nonzero(valid) / denominator * 100.0) if denominator else 0.0


def compute_landsat_ndvi_product(
    pair: PreparedPeriodPair,
    *,
    artifact_dir: str | Path | None = None,
    final_ndvi_coverage_gate: float = FINAL_NDVI_COVERAGE_GATE,
    final_common_coverage_gate: float = FINAL_COMMON_COVERAGE_GATE,
) -> LandsatNDVIProduct:
    """Calculate NDVI/delta and optionally write numeric and display artifacts."""
    if pair.contract_version != "v0.3-preparation-1":
        raise ValueError("unsupported preparation contract")
    if not (0 <= final_ndvi_coverage_gate <= 100 and 0 <= final_common_coverage_gate <= 100):
        raise ValueError("coverage gates are invalid")
    a = pair.period_a
    b = pair.period_b
    if not np.array_equal(
        pair.common_preparation_valid_mask,
        a.aoi_mask & a.preparation_valid_mask & b.preparation_valid_mask,
    ):
        raise ValueError("preparation common mask is not server-derived")
    aoi = np.asarray(a.aoi_mask & b.aoi_mask, dtype=bool)
    denominator = int(np.count_nonzero(aoi))
    if denominator <= 0:
        raise ValueError("empty AOI mask")
    ndvi_a, valid_a = _period_ndvi(
        a.red_reflectance, a.nir_reflectance, a.preparation_valid_mask, a.aoi_mask
    )
    ndvi_b, valid_b = _period_ndvi(
        b.red_reflectance, b.nir_reflectance, b.preparation_valid_mask, b.aoi_mask
    )
    common = np.asarray(pair.common_preparation_valid_mask & valid_a & valid_b, dtype=bool)
    if (
        _coverage(valid_a, denominator) < final_ndvi_coverage_gate
        or _coverage(valid_b, denominator) < final_ndvi_coverage_gate
    ):
        raise ValueError("insufficient_ndvi_coverage")
    if _coverage(common, denominator) < final_common_coverage_gate:
        raise ValueError("insufficient_comparison_coverage")
    delta = np.full(ndvi_a.shape, np.nan, dtype=np.float32)
    delta[common] = ndvi_b[common] - ndvi_a[common]
    values = {
        "aoi_rasterized_pixels": denominator,
        "preparation_valid_pixels_period_a": int(
            np.count_nonzero(a.preparation_valid_mask & a.aoi_mask)
        ),
        "preparation_valid_pixels_period_b": int(
            np.count_nonzero(b.preparation_valid_mask & b.aoi_mask)
        ),
        "final_ndvi_valid_pixels_period_a": int(np.count_nonzero(valid_a)),
        "final_ndvi_valid_pixels_period_b": int(np.count_nonzero(valid_b)),
        "final_common_comparison_pixels": int(np.count_nonzero(common)),
        "preparation_coverage_period_a_pct": _coverage(
            a.preparation_valid_mask & a.aoi_mask, denominator
        ),
        "preparation_coverage_period_b_pct": _coverage(
            b.preparation_valid_mask & b.aoi_mask, denominator
        ),
        "final_ndvi_coverage_period_a_pct": _coverage(valid_a, denominator),
        "final_ndvi_coverage_period_b_pct": _coverage(valid_b, denominator),
        "final_common_comparison_coverage_pct": _coverage(common, denominator),
        "mean_ndvi_period_a": float(np.mean(ndvi_a[common])),
        "mean_ndvi_period_b": float(np.mean(ndvi_b[common])),
        "mean_delta_ndvi": float(np.mean(delta[common])),
        "min_delta_ndvi": float(np.min(delta[common])),
        "max_delta_ndvi": float(np.max(delta[common])),
    }
    if not all(math.isfinite(float(v)) for v in values.values()):
        raise ValueError("non-finite NDVI metrics")
    artifacts: dict[str, str] = {}
    if artifact_dir is not None:
        artifacts = _write_artifacts(
            Path(artifact_dir), pair, ndvi_a, ndvi_b, delta, valid_a, valid_b, common
        )
    provenance = {
        "execution_mode": EXECUTION_MODE,
        "preparation_contract_version": pair.contract_version,
        "target_grid": pair.pair_grid.model_dump(mode="json"),
        "period_a": a.requested_period.model_dump(mode="json"),
        "period_b": b.requested_period.model_dump(mode="json"),
        "hard_limits_applied": dict(pair.hard_limits_applied),
        "operational_metrics": dict(pair.operational_metrics),
        "best_effort_warnings": list(pair.best_effort_warnings),
        "coverage_gates_provisional": True,
    }
    return LandsatNDVIProduct(
        ndvi_a, ndvi_b, delta, valid_a, valid_b, common, values, artifacts, provenance
    )


def _write_artifacts(
    root: Path,
    pair: PreparedPeriodPair,
    ndvi_a: np.ndarray,
    ndvi_b: np.ndarray,
    delta: np.ndarray,
    valid_a: np.ndarray,
    valid_b: np.ndarray,
    common: np.ndarray,
) -> dict[str, str]:
    if rasterio is None or Affine is None:
        raise RuntimeError("rasterio is required for numeric GeoTIFF artifacts")
    root.mkdir(parents=True, exist_ok=True)
    grid = pair.pair_grid
    transform = Affine(*grid.transform)
    artifacts: dict[str, str] = {}
    float_arrays = {
        "ndvi_before_raster": ndvi_a,
        "ndvi_after_raster": ndvi_b,
        "ndvi_change_raster": delta,
    }
    mask_arrays = {
        "ndvi_valid_before": valid_a,
        "ndvi_valid_after": valid_b,
        "ndvi_common_comparison": common,
    }
    for name, array in float_arrays.items():
        path = root / f"{name}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=1,
            dtype="float32",
            crs=grid.crs,
            transform=transform,
            nodata=np.nan,
            compress="deflate",
        ) as dst:
            dst.write(np.asarray(array, dtype=np.float32), 1)
        artifacts[name] = path.name
    for name, array in mask_arrays.items():
        path = root / f"{name}.tif"
        with rasterio.open(
            path,
            "w",
            driver="GTiff",
            width=grid.width,
            height=grid.height,
            count=1,
            dtype="uint8",
            crs=grid.crs,
            transform=transform,
            nodata=0,
            compress="deflate",
        ) as dst:
            dst.write(np.asarray(array, dtype=np.uint8), 1)
        artifacts[name] = path.name
    for name, array in {"ndvi_before": ndvi_a, "ndvi_after": ndvi_b, "ndvi_change": delta}.items():
        path = root / f"{name}.png"
        scaled = np.nan_to_num((array + 1.0) * 127.5, nan=0.0, posinf=255.0, neginf=0.0)
        from PIL import Image

        Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), mode="L").save(
            path, format="PNG", optimize=True
        )
        artifacts[name] = path.name
    return artifacts


def verify_landsat_ndvi_product(
    product: LandsatNDVIProduct, pair: PreparedPeriodPair
) -> dict[str, Any]:
    """Independent, bounded verifier for the numeric science contract."""
    if product.provenance.get("preparation_contract_version") != pair.contract_version:
        return {"status": "failed", "code": "preparation_contract_mismatch"}
    if product.ndvi_before.shape != (pair.pair_grid.height, pair.pair_grid.width):
        return {"status": "failed", "code": "target_grid_mismatch"}
    if not np.array_equal(
        product.final_common_comparison_mask,
        pair.common_preparation_valid_mask
        & product.final_ndvi_valid_mask_a
        & product.final_ndvi_valid_mask_b,
    ):
        return {"status": "failed", "code": "common_mask_invalid"}
    for array, mask in (
        (product.ndvi_before, product.final_ndvi_valid_mask_a),
        (product.ndvi_after, product.final_ndvi_valid_mask_b),
    ):
        if np.any(~np.isfinite(array[mask])) or np.any(
            (array[mask] < -1.00001) | (array[mask] > 1.00001)
        ):
            return {"status": "failed", "code": "ndvi_range_invalid"}
    common = product.final_common_comparison_mask
    expected = float(np.mean(product.ndvi_after[common] - product.ndvi_before[common]))
    if not math.isclose(
        expected,
        float(product.metrics.get("mean_delta_ndvi", math.nan)),
        rel_tol=1e-6,
        abs_tol=1e-6,
    ):
        return {"status": "failed", "code": "delta_stat_mismatch"}
    for key in ("mean_ndvi_period_a", "mean_ndvi_period_b", "mean_delta_ndvi"):
        if not math.isfinite(float(product.metrics.get(key, math.nan))):
            return {"status": "failed", "code": "metric_non_finite"}
    return {"status": "passed", "common_pixels": int(np.count_nonzero(common))}


def artifact_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "EXECUTION_MODE",
    "LandsatNDVIProduct",
    "compute_landsat_ndvi_product",
    "verify_landsat_ndvi_product",
    "artifact_sha256",
]
