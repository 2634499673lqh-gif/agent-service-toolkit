"""Deterministic exploratory NDWI calculation over the pinned v5 fixture."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .models import GeoChangeTask
from .paths import fixture_root

FIXTURE_ROOT = fixture_root("real-sentinel2-ndwi-v5")
FIXTURE_VERSION = "geochange.real-sentinel2-ndwi.v1"
MANIFEST_SHA256 = "90416a43c08d127a4ed46b313481d803123d04109c94bb4542e1e4439e867aad"
FIXTURE_SHA256 = {
    "period_a": "a3a40d907c5e33f633667aafadf0a378f3934c466a30954c533d762f605b6636",
    "period_b": "8793b491fca151d467764e6aafb20752fdbde3df45c1af104b24bcf910838f54",
}
MAX_FIXTURE_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 2_000_000
VALID_SCL = {4, 5, 6}
EXCLUDED_SCL = {0, 1, 2, 3, 7, 8, 9, 10, 11}


@dataclass(frozen=True)
class WaterChange:
    ndwi_a: np.ndarray
    ndwi_b: np.ndarray
    delta: np.ndarray
    valid_mask: np.ndarray
    pixel_area_m2: float
    artifacts: dict[str, str]


def _manifest() -> dict[str, Any]:
    path = FIXTURE_ROOT / "manifest.json"
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != MANIFEST_SHA256:
        raise ValueError("NDWI fixture manifest integrity mismatch")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("fixture_version") != FIXTURE_VERSION:
        raise ValueError("NDWI fixture version is invalid")
    if data.get("preparation", {}).get("target_grid") != {
        "crs": "EPSG:32650",
        "resolution_m": 10,
        "dimensions": [24, 24],
    }:
        raise ValueError("NDWI target grid is invalid")
    return data


def scene_evidence(task: GeoChangeTask) -> dict[str, str]:
    if task.analysis_type != "water_change" or task.indicator != "NDWI":
        raise ValueError("NDWI scene evidence requires the water Skill")
    manifest = _manifest()
    evidence = {"fixture_manifest": MANIFEST_SHA256}
    for suffix, selected in (("a", task.period_a), ("b", task.period_b)):
        scene = manifest["scenes"][f"period_{suffix}"]
        if not selected.start.isoformat() <= scene["acquisition_date"] <= selected.end.isoformat():
            raise ValueError("no pinned NDWI fixture for the validated period")
        if scene["aoi_bounds_wgs84"] != [114.3, 30.5, 114.45, 30.62]:
            raise ValueError("NDWI fixture AOI mismatch")
        asset_identity = scene["asset_identity"]
        asset_digest = hashlib.sha256(
            json.dumps(
                {
                    key: {
                        "band": value["band"],
                        "href": value["href"],
                        "etag": value["etag"],
                        "window_pixel_origin": value["window_pixel_origin"],
                        "window_size_pixels": value["window_size_pixels"],
                    }
                    for key, value in asset_identity.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        evidence.update(
            {
                f"period_{suffix}_item_id": scene["item_id"],
                f"period_{suffix}_date": scene["acquisition_date"],
                f"period_{suffix}_collection": scene["collection"],
                f"period_{suffix}_fixture": scene["fixture_sha256"],
                f"period_{suffix}_assets": asset_digest,
            }
        )
    return evidence


def validate_binding(task: GeoChangeTask, evidence: dict[str, str]) -> None:
    """Require the exact server-owned NDWI fixture binding."""
    if evidence != scene_evidence(task):
        raise ValueError("NDWI metadata and raster fixture binding mismatch")


def _load_period(
    manifest: dict[str, Any], period: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    scene = manifest["scenes"][period]
    path = FIXTURE_ROOT / scene["fixture_file"]
    if path.stat().st_size > MAX_FIXTURE_BYTES:
        raise ValueError("NDWI fixture exceeds the size bound")
    if hashlib.sha256(path.read_bytes()).hexdigest() != FIXTURE_SHA256[period]:
        raise ValueError("NDWI fixture checksum mismatch")
    with np.load(path, allow_pickle=False) as bundle:
        if set(bundle.files) != {"green", "nir", "scl", "coverage"}:
            raise ValueError("NDWI fixture fields are invalid")
        green = bundle["green"]
        nir = bundle["nir"]
        scl = bundle["scl"]
        coverage = bundle["coverage"]
        if green.dtype != np.uint16 or nir.dtype != np.uint16 or scl.dtype != np.uint8:
            raise ValueError("NDWI fixture dtype is invalid")
        if green.shape != (24, 24) or nir.shape != (24, 24) or coverage.shape != (24, 24):
            raise ValueError("NDWI target raster shape is invalid")
        if scl.shape != (12, 12):
            raise ValueError("NDWI SCL shape is invalid")
        return green.copy(), nir.copy(), scl.copy(), coverage.astype(bool, copy=True)


def _ndwi(
    green_dn: np.ndarray, nir_dn: np.ndarray, scl: np.ndarray, coverage: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    green = green_dn.astype(np.float32) * 0.0001 - 0.1
    nir = nir_dn.astype(np.float32) * 0.0001 - 0.1
    expanded_scl = np.repeat(np.repeat(scl, 2, axis=0), 2, axis=1)
    valid = (
        coverage
        & np.isin(expanded_scl, tuple(VALID_SCL))
        & ~np.isin(expanded_scl, tuple(EXCLUDED_SCL))
        & np.isfinite(green)
        & np.isfinite(nir)
        & (green >= 0)
        & (nir >= 0)
    )
    denominator = green + nir
    valid &= np.isfinite(denominator) & (denominator > 0)
    result = np.full(green.shape, np.nan, dtype=np.float32)
    result[valid] = (green[valid] - nir[valid]) / denominator[valid]
    valid &= np.isfinite(result) & (result >= -1.00001) & (result <= 1.00001)
    result[~valid] = np.nan
    return result, valid


def _write_png(array: np.ndarray, path: Path) -> None:
    scaled = np.nan_to_num((array + 1.0) * 127.5, nan=0.0, posinf=255.0, neginf=0.0)
    Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), mode="L").save(
        path, format="PNG", optimize=True
    )
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError("generated NDWI artifact exceeds the size bound")


def compute_cached_water_change(
    task: GeoChangeTask,
    evidence: dict[str, str],
    *,
    artifact_dir: str | Path | None = None,
) -> WaterChange:
    expected = scene_evidence(task)
    if evidence != expected:
        raise ValueError("NDWI scene evidence binding mismatch")
    manifest = _manifest()
    green_a, nir_a, scl_a, coverage_a = _load_period(manifest, "period_a")
    green_b, nir_b, scl_b, coverage_b = _load_period(manifest, "period_b")
    ndwi_a, valid_a = _ndwi(green_a, nir_a, scl_a, coverage_a)
    ndwi_b, valid_b = _ndwi(green_b, nir_b, scl_b, coverage_b)
    valid = valid_a & valid_b
    if not np.any(valid):
        raise ValueError("no common valid pixels remain for NDWI analysis")
    delta = np.full(ndwi_a.shape, np.nan, dtype=np.float32)
    delta[valid] = ndwi_b[valid] - ndwi_a[valid]
    artifacts: dict[str, str] = {}
    if artifact_dir is not None:
        root = Path(artifact_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        for name, array in (
            ("ndwi_before.png", ndwi_a),
            ("ndwi_after.png", ndwi_b),
            ("ndwi_change.png", delta),
        ):
            path = root / name
            _write_png(array, path)
            artifacts[name.removesuffix(".png")] = path.name
    return WaterChange(ndwi_a, ndwi_b, delta, valid, 100.0, artifacts)


def summarize_water_change(change: WaterChange) -> dict[str, float | int]:
    mask = np.asarray(change.valid_mask, dtype=bool)
    if mask.shape != change.delta.shape or not np.any(mask):
        raise ValueError("NDWI summary requires non-empty common-valid pixels")
    values = {
        "valid_pixels": int(mask.sum()),
        "valid_analysis_area_m2": float(mask.sum() * change.pixel_area_m2),
        "mean_ndwi_period_a": float(np.mean(change.ndwi_a[mask])),
        "mean_ndwi_period_b": float(np.mean(change.ndwi_b[mask])),
        "mean_delta_ndwi": float(np.mean(change.delta[mask])),
    }
    if not all(np.isfinite(float(value)) for value in values.values()):
        raise ValueError("NDWI summary contains a non-finite value")
    if not all(
        -1.00001 <= values[key] <= 1.00001 for key in ("mean_ndwi_period_a", "mean_ndwi_period_b")
    ):
        raise ValueError("NDWI mean is outside the bounded range")
    if not -2.00001 <= values["mean_delta_ndwi"] <= 2.00001:
        raise ValueError("NDWI delta is outside the bounded range")
    return values


__all__ = [
    "FIXTURE_VERSION",
    "MANIFEST_SHA256",
    "WaterChange",
    "compute_cached_water_change",
    "scene_evidence",
    "summarize_water_change",
]
