"""Implementation B: NDVI calculation and evidence-bound product output.

This module consumes only the server-owned ``PreparedPeriodPair`` handoff from
Implementation A.  It deliberately has no STAC, URL, QA, or reprojection code.
"""

from __future__ import annotations

import hashlib
import json
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
MAX_NUMERIC_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_DISPLAY_ARTIFACT_BYTES = 2 * 1024 * 1024


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
) -> LandsatNDVIProduct:
    """Calculate NDVI/delta and optionally write numeric and display artifacts."""
    if pair.contract_version != "v0.3-preparation-1":
        raise ValueError("unsupported preparation contract")
    a = pair.period_a
    b = pair.period_b
    if not np.array_equal(a.aoi_mask, b.aoi_mask):
        raise ValueError("period AOI masks do not match")
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
        _coverage(valid_a, denominator) < FINAL_NDVI_COVERAGE_GATE
        or _coverage(valid_b, denominator) < FINAL_NDVI_COVERAGE_GATE
    ):
        raise ValueError("insufficient_ndvi_coverage")
    if _coverage(common, denominator) < FINAL_COMMON_COVERAGE_GATE:
        raise ValueError("insufficient_comparison_coverage")
    delta = np.full(ndvi_a.shape, np.nan, dtype=np.float32)
    delta[common] = ndvi_b[common] - ndvi_a[common]
    values = {
        "aoi_rasterized_pixels": denominator,
        "aoi_area_m2": float(
            a.provenance.get("aoi_area_m2", denominator * a.target_grid.resolution_m**2)
        ),
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
    artifact_root = Path(artifact_dir) if artifact_dir is not None else None
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
        "artifact_checksums": {
            name: artifact_sha256(artifact_root / filename) for name, filename in artifacts.items()
        }
        if artifact_root is not None
        else {},
    }
    provenance["metrics_sha256"] = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
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
        _verify_numeric_artifact(path, grid, "float32", np.nan)
        if path.stat().st_size > MAX_NUMERIC_ARTIFACT_BYTES:
            raise ValueError("numeric artifact exceeds the bounded size")
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
        _verify_numeric_artifact(path, grid, "uint8", 0.0)
        if path.stat().st_size > MAX_NUMERIC_ARTIFACT_BYTES:
            raise ValueError("mask artifact exceeds the bounded size")
        artifacts[name] = path.name
    for name, array in {"ndvi_before": ndvi_a, "ndvi_after": ndvi_b, "ndvi_change": delta}.items():
        path = root / f"{name}.png"
        scaled = np.nan_to_num((array + 1.0) * 127.5, nan=0.0, posinf=255.0, neginf=0.0)
        from PIL import Image

        Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), mode="L").save(
            path, format="PNG", optimize=True
        )
        if path.stat().st_size > MAX_DISPLAY_ARTIFACT_BYTES:
            raise ValueError("display artifact exceeds the bounded size")
        artifacts[name] = path.name
    return artifacts


def _verify_numeric_artifact(path: Path, grid: Any, dtype: str, nodata: float) -> None:
    if rasterio is None or Affine is None:
        raise RuntimeError("rasterio is required for numeric artifact verification")
    with rasterio.open(path) as dataset:
        nodata_matches = (
            math.isnan(float(dataset.nodata))
            if math.isnan(float(nodata))
            else dataset.nodata == nodata
        )
        if (
            dataset.count != 1
            or dataset.width != grid.width
            or dataset.height != grid.height
            or str(dataset.crs) != str(grid.crs)
            or tuple(dataset.transform) != tuple(Affine(*grid.transform))
            or dataset.dtypes[0] != dtype
            or not nodata_matches
        ):
            raise ValueError("numeric artifact metadata mismatch")


def verify_landsat_ndvi_product(
    product: LandsatNDVIProduct,
    pair: PreparedPeriodPair,
    *,
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    """Independently recompute the science projection and artifact evidence."""

    if product.provenance.get("preparation_contract_version") != pair.contract_version:
        return {"status": "failed", "code": "preparation_contract_mismatch"}
    expected_shape = (pair.pair_grid.height, pair.pair_grid.width)
    if product.ndvi_before.shape != expected_shape or product.ndvi_after.shape != expected_shape:
        return {"status": "failed", "code": "target_grid_mismatch"}
    expected_a, expected_valid_a = _period_ndvi(
        pair.period_a.red_reflectance,
        pair.period_a.nir_reflectance,
        pair.period_a.preparation_valid_mask,
        pair.period_a.aoi_mask,
    )
    expected_b, expected_valid_b = _period_ndvi(
        pair.period_b.red_reflectance,
        pair.period_b.nir_reflectance,
        pair.period_b.preparation_valid_mask,
        pair.period_b.aoi_mask,
    )
    expected_common = pair.common_preparation_valid_mask & expected_valid_a & expected_valid_b
    expected_delta = np.full(expected_shape, np.nan, dtype=np.float32)
    expected_delta[expected_common] = expected_b[expected_common] - expected_a[expected_common]
    if not np.array_equal(product.final_ndvi_valid_mask_a, expected_valid_a):
        return {"status": "failed", "code": "period_a_mask_invalid"}
    if not np.array_equal(product.final_ndvi_valid_mask_b, expected_valid_b):
        return {"status": "failed", "code": "period_b_mask_invalid"}
    if not np.array_equal(product.final_common_comparison_mask, expected_common):
        return {"status": "failed", "code": "common_mask_invalid"}
    if not np.allclose(product.ndvi_before, expected_a, equal_nan=True, rtol=0, atol=1e-6):
        return {"status": "failed", "code": "period_a_ndvi_invalid"}
    if not np.allclose(product.ndvi_after, expected_b, equal_nan=True, rtol=0, atol=1e-6):
        return {"status": "failed", "code": "period_b_ndvi_invalid"}
    if not np.allclose(product.delta, expected_delta, equal_nan=True, rtol=0, atol=1e-6):
        return {"status": "failed", "code": "delta_array_invalid"}
    if not np.any(expected_common):
        return {"status": "failed", "code": "empty_common_mask"}
    for array, mask in ((expected_a, expected_valid_a), (expected_b, expected_valid_b)):
        if np.any(~np.isfinite(array[mask])) or np.any(
            (array[mask] < -1.00001) | (array[mask] > 1.00001)
        ):
            return {"status": "failed", "code": "ndvi_range_invalid"}

    denominator = int(np.count_nonzero(pair.period_a.aoi_mask & pair.period_b.aoi_mask))
    expected_metrics: dict[str, float | int] = {
        "aoi_rasterized_pixels": denominator,
        "preparation_valid_pixels_period_a": int(
            np.count_nonzero(pair.period_a.preparation_valid_mask & pair.period_a.aoi_mask)
        ),
        "preparation_valid_pixels_period_b": int(
            np.count_nonzero(pair.period_b.preparation_valid_mask & pair.period_b.aoi_mask)
        ),
        "final_ndvi_valid_pixels_period_a": int(np.count_nonzero(expected_valid_a)),
        "final_ndvi_valid_pixels_period_b": int(np.count_nonzero(expected_valid_b)),
        "final_common_comparison_pixels": int(np.count_nonzero(expected_common)),
        "preparation_coverage_period_a_pct": _coverage(
            pair.period_a.preparation_valid_mask & pair.period_a.aoi_mask, denominator
        ),
        "preparation_coverage_period_b_pct": _coverage(
            pair.period_b.preparation_valid_mask & pair.period_b.aoi_mask, denominator
        ),
        "final_ndvi_coverage_period_a_pct": _coverage(expected_valid_a, denominator),
        "final_ndvi_coverage_period_b_pct": _coverage(expected_valid_b, denominator),
        "final_common_comparison_coverage_pct": _coverage(expected_common, denominator),
        "mean_ndvi_period_a": float(np.mean(expected_a[expected_common])),
        "mean_ndvi_period_b": float(np.mean(expected_b[expected_common])),
        "mean_delta_ndvi": float(np.mean(expected_delta[expected_common])),
        "min_delta_ndvi": float(np.min(expected_delta[expected_common])),
        "max_delta_ndvi": float(np.max(expected_delta[expected_common])),
    }
    for key, expected in expected_metrics.items():
        actual = product.metrics.get(key)
        if isinstance(expected, int):
            if (
                not isinstance(actual, (int, float))
                or isinstance(actual, bool)
                or not float(actual).is_integer()
                or int(actual) != expected
            ):
                return {"status": "failed", "code": "metrics_mismatch"}
        elif (
            not isinstance(actual, (int, float))
            or isinstance(actual, bool)
            or not math.isclose(float(actual), expected, rel_tol=1e-6, abs_tol=1e-6)
        ):
            return {"status": "failed", "code": "metrics_mismatch"}
    if "aoi_area_m2" in product.metrics and "aoi_area_m2" in product.provenance:
        if not math.isclose(
            float(product.metrics["aoi_area_m2"]),
            float(product.provenance["aoi_area_m2"]),
            rel_tol=1e-6,
            abs_tol=1e-3,
        ):
            return {"status": "failed", "code": "aoi_area_mismatch"}

    if product.artifacts:
        expected_artifacts = set(NDVI_ARTIFACTS + MASK_ARTIFACTS + PNG_ARTIFACTS)
        if set(product.artifacts) != expected_artifacts:
            return {"status": "failed", "code": "artifacts_incomplete"}
        checksums = product.provenance.get("artifact_checksums")
        if not isinstance(checksums, dict) or set(checksums) != expected_artifacts:
            return {"status": "failed", "code": "artifact_checksum_missing"}
        if artifact_root is None:
            return {"status": "failed", "code": "artifact_root_missing"}
        root = Path(artifact_root).resolve()
        for name, filename in product.artifacts.items():
            if not isinstance(filename, str) or Path(filename).name != filename:
                return {"status": "failed", "code": "artifact_reference_invalid"}
            path = (root / filename).resolve()
            if root not in path.parents or not path.is_file():
                return {"status": "failed", "code": "artifact_missing"}
            digest = checksums.get(name)
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
                or artifact_sha256(path) != digest
            ):
                return {"status": "failed", "code": "artifact_checksum_mismatch"}
            try:
                if name in NDVI_ARTIFACTS:
                    _verify_numeric_artifact(path, pair.pair_grid, "float32", np.nan)
                elif name in MASK_ARTIFACTS:
                    _verify_numeric_artifact(path, pair.pair_grid, "uint8", 0.0)
                else:
                    from PIL import Image

                    with Image.open(path) as image:
                        if image.format != "PNG":
                            return {"status": "failed", "code": "artifact_invalid"}
                        image.verify()
            except (OSError, ValueError):
                return {"status": "failed", "code": "artifact_invalid"}
    return {"status": "passed", "common_pixels": int(np.count_nonzero(expected_common))}


def verify_landsat_ndvi_metadata(
    product: LandsatNDVIProduct,
    pair: PreparedPeriodPair,
    *,
    expected_aoi: dict[str, Any],
    expected_periods: dict[str, Any],
    artifact_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify the server-owned evidence projection used by terminal results."""
    result = verify_landsat_ndvi_product(product, pair, artifact_root=artifact_root)
    if result["status"] != "passed":
        return result
    if product.provenance.get("aoi_hash") != expected_aoi.get("source_hash"):
        return {"status": "failed", "code": "aoi_evidence_mismatch"}
    for provenance_key, expected_key in (
        ("aoi_id", "aoi_id"),
        ("aoi_source_version", "source_version"),
        ("aoi_source_url", "source_url"),
        ("aoi_area_m2", "area_m2"),
    ):
        if (
            expected_key in expected_aoi
            and product.provenance.get(provenance_key) != expected_aoi[expected_key]
        ):
            return {"status": "failed", "code": "aoi_evidence_mismatch"}
    if product.provenance.get("period_a") != expected_periods.get("period_a"):
        return {"status": "failed", "code": "period_a_evidence_mismatch"}
    if product.provenance.get("period_b") != expected_periods.get("period_b"):
        return {"status": "failed", "code": "period_b_evidence_mismatch"}
    if product.provenance.get("target_grid") != pair.pair_grid.model_dump(mode="json"):
        return {"status": "failed", "code": "target_grid_mismatch"}
    if product.provenance.get("target_crs") != pair.pair_grid.crs or product.provenance.get(
        "target_dimensions"
    ) != [pair.pair_grid.height, pair.pair_grid.width]:
        return {"status": "failed", "code": "target_grid_mismatch"}
    if product.provenance.get("scene_provenance") != {
        "period_a": pair.period_a.provenance,
        "period_b": pair.period_b.provenance,
    }:
        return {"status": "failed", "code": "scene_provenance_mismatch"}
    return {"status": "passed", "common_pixels": result["common_pixels"]}


def artifact_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


__all__ = [
    "EXECUTION_MODE",
    "LandsatNDVIProduct",
    "compute_landsat_ndvi_product",
    "verify_landsat_ndvi_product",
    "verify_landsat_ndvi_metadata",
    "artifact_sha256",
]
