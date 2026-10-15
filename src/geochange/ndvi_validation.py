"""Independent, bounded numerical validation for a selected Landsat NDVI run."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np

REFERENCE_POLICY = "taskpilot.ndvi.reference.v1"
MAX_PACKAGE_BYTES = 128 * 1024 * 1024
TOLERANCES = {"period_ndvi": 1e-6, "delta_ndvi": 2e-6, "relative": 0.0, "mask_disagreement": 0}
_SCENE_ID = re.compile(r"^LC0[89]_L2SP_\d{6}_\d{8}_\d{2}_T\d$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_file(root: Path, name: str) -> Path:
    if not isinstance(name, str) or Path(name).name != name or name.startswith(("/", "\\")):
        raise ValueError("unsafe evidence path")
    path = (root / name).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("evidence path escapes package")
    if not path.is_file() or path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError("evidence file is missing or oversized")
    return path


def capture_ndvi_inputs(
    pair: Any,
    product: Any,
    root: str | Path,
    *,
    artifact_root: str | Path | None = None,
    aoi_hash: str | None = None,
) -> dict[str, Any]:
    """Capture the exact prepared arrays and production artifacts for one run."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    arrays = {
        "red_a": pair.period_a.red_reflectance,
        "nir_a": pair.period_a.nir_reflectance,
        "red_b": pair.period_b.red_reflectance,
        "nir_b": pair.period_b.nir_reflectance,
        "aoi_a": pair.period_a.aoi_mask,
        "aoi_b": pair.period_b.aoi_mask,
        "prep_a": pair.period_a.preparation_valid_mask,
        "prep_b": pair.period_b.preparation_valid_mask,
    }
    np.savez_compressed(root / "inputs.npz", **arrays)
    artifacts = {}
    artifact_root_path = Path(artifact_root).resolve() if artifact_root is not None else Path.cwd()
    for name in (
        "ndvi_before_raster",
        "ndvi_after_raster",
        "ndvi_change_raster",
        "ndvi_valid_before",
        "ndvi_valid_after",
        "ndvi_common_comparison",
    ):
        source_name = getattr(product, "artifacts", {}).get(name)
        if not source_name:
            raise ValueError(f"production artifact missing: {name}")
        source = artifact_root_path / source_name
        if not source.is_file():
            raise ValueError(f"production artifact missing: {name}")
        destination = root / source.name
        shutil.copyfile(source, destination)
        artifacts[name] = destination.name
    grid = pair.pair_grid
    manifest = {
        "package_version": "taskpilot.ndvi.capture.v1",
        "reference_policy": REFERENCE_POLICY,
        "shape": [int(grid.height), int(grid.width)],
        "aoi_id": "jianghan_district_420103",
        "aoi_hash": aoi_hash or pair.period_a.provenance.get("aoi_hash"),
        "periods": {
            "a": pair.period_a.requested_period.model_dump(mode="json"),
            "b": pair.period_b.requested_period.model_dump(mode="json"),
        },
        "scene_bindings": {
            "a": {
                "scene_ids": pair.period_a.provenance.get("scene_ids", []),
                "acquisition_dates": pair.period_a.provenance.get("acquisition_dates", []),
            },
            "b": {
                "scene_ids": pair.period_b.provenance.get("scene_ids", []),
                "acquisition_dates": pair.period_b.provenance.get("acquisition_dates", []),
            },
        },
        "band_roles": {
            "red": "SR_B4",
            "nir": "SR_B5",
            "calibration": "Landsat Collection 2 Level 2 surface reflectance scale/offset",
        },
        "grid": {
            "crs": grid.crs,
            "dimensions": [grid.height, grid.width],
            "transform": list(grid.transform),
            "resolution_m": grid.resolution_m,
        },
        "provenance": {
            "period_a": pair.period_a.provenance,
            "period_b": pair.period_b.provenance,
            "contract_version": pair.contract_version,
        },
        "input_file": "inputs.npz",
        "input_sha256": _sha256(root / "inputs.npz"),
        "artifacts": artifacts,
        "artifact_sha256": {name: _sha256(root / filename) for name, filename in artifacts.items()},
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    return manifest


def _reference(
    red: np.ndarray, nir: np.ndarray, prep: np.ndarray, aoi: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    if red.shape != nir.shape or red.shape != prep.shape or red.shape != aoi.shape:
        raise ValueError("input geometry mismatch")
    values = np.full(red.shape, np.nan, dtype=np.float32)
    mask = np.zeros(red.shape, dtype=bool)
    for index in np.ndindex(red.shape):
        if not bool(aoi[index] and prep[index]):
            continue
        r, n = float(red[index]), float(nir[index])
        if not math.isfinite(r) or not math.isfinite(n):
            continue
        denominator = n + r
        if not math.isfinite(denominator) or abs(denominator) <= 1e-6:
            continue
        value = float(np.float32((n - r) / denominator))
        if not math.isfinite(value) or value < -1.0 - 1e-5 or value > 1.0 + 1e-5:
            continue
        values[index] = value
        mask[index] = True
    return values, mask


def _read_raster(
    root: Path,
    name: str,
    expected_shape: tuple[int, int],
    *,
    grid: dict[str, Any],
    mask: bool = False,
) -> np.ndarray:
    path = _safe_file(root, name)
    try:
        import rasterio
        from rasterio.crs import CRS
        from rasterio.errors import CRSError

        with rasterio.open(path) as dataset:
            if (dataset.count, dataset.height, dataset.width) != (1, *expected_shape):
                raise ValueError("raster geometry mismatch")
            expected_transform = tuple(float(value) for value in grid.get("transform", []))
            actual_transform = tuple(float(value) for value in dataset.transform)[:6]
            try:
                crs_matches = dataset.crs == CRS.from_user_input(grid.get("crs"))
            except (TypeError, ValueError, CRSError):
                expected_crs = str(grid.get("crs"))
                actual_crs = str(dataset.crs)
                crs_matches = (
                    expected_crs == actual_crs
                    or expected_crs == "EPSG:32649"
                    and "UTM zone 49N" in actual_crs
                )
            if not crs_matches or actual_transform != expected_transform:
                raise ValueError("raster grid or affine mismatch")
            if mask and dataset.nodata != 0:
                raise ValueError("raster NoData mismatch")
            if not mask and (dataset.nodata is None or not math.isnan(float(dataset.nodata))):
                raise ValueError("raster NoData mismatch")
            array = dataset.read(1)
            if mask and dataset.dtypes[0] != "uint8":
                raise ValueError("mask dtype mismatch")
            if not mask and dataset.dtypes[0] != "float32":
                raise ValueError("numeric dtype mismatch")
            if mask and not np.isin(array, (0, 1)).all():
                raise ValueError("mask domain mismatch")
            return np.asarray(array, dtype=bool if mask else np.float32)
    except ImportError as error:
        raise ValueError("rasterio is required") from error


def validate_capture(root: str | Path) -> dict[str, Any]:
    root = Path(root).resolve()
    manifest = json.loads(_safe_file(root, "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("capture manifest must be an object")
    if (
        manifest.get("package_version") != "taskpilot.ndvi.capture.v1"
        or manifest.get("reference_policy") != REFERENCE_POLICY
    ):
        raise ValueError("reference policy mismatch")
    if (
        manifest.get("aoi_id") != "jianghan_district_420103"
        or not isinstance(manifest.get("aoi_hash"), str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest["aoi_hash"])
    ):
        raise ValueError("AOI identity evidence is incomplete")
    if not isinstance(manifest.get("periods"), dict) or set(manifest["periods"]) != {"a", "b"}:
        raise ValueError("period identity evidence is incomplete")
    period_windows: dict[str, tuple[date, date]] = {}
    for period_id in ("a", "b"):
        period = manifest["periods"][period_id]
        if not isinstance(period, dict) or period.get("period_id") != period_id:
            raise ValueError("period identity evidence is invalid")
        if not isinstance(period.get("start_utc"), str) or not isinstance(
            period.get("end_utc"), str
        ):
            raise ValueError("period dates are invalid")
        try:
            start = datetime.fromisoformat(period["start_utc"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(period["end_utc"].replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError):
            raise ValueError("period dates are invalid") from None
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or start.utcoffset() != timedelta(0)
            or end.utcoffset() != timedelta(0)
            or end <= start
        ):
            raise ValueError("period ordering is invalid")
        period_windows[period_id] = (start.date(), end.date())
    if period_windows["a"][1] > period_windows["b"][0]:
        raise ValueError("periods overlap or are reversed")
    bindings = manifest.get("scene_bindings")
    if (
        not isinstance(bindings, dict)
        or set(bindings) != {"a", "b"}
        or any(
            not isinstance(bindings.get(key), dict)
            or not bindings[key].get("scene_ids")
            or not bindings[key].get("acquisition_dates")
            for key in ("a", "b")
        )
    ):
        raise ValueError("scene identity evidence is incomplete")
    if any(
        not isinstance(bindings[key]["scene_ids"], list)
        or not isinstance(bindings[key]["acquisition_dates"], list)
        for key in ("a", "b")
    ):
        raise ValueError("scene identity evidence is invalid")
    for period_id in ("a", "b"):
        binding = bindings[period_id]
        if not isinstance(binding, dict) or any(
            not isinstance(item, str) or not item or not _SCENE_ID.fullmatch(item)
            for item in binding["scene_ids"]
        ):
            raise ValueError("scene IDs are invalid")
        if len(binding["scene_ids"]) != len(binding["acquisition_dates"]):
            raise ValueError("scene identity evidence is inconsistent")
        if any(not isinstance(item, str) for item in binding["acquisition_dates"]):
            raise ValueError("scene acquisition dates are invalid")
        for scene_id, item in zip(binding["scene_ids"], binding["acquisition_dates"]):
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item):
                    raise ValueError
                acquisition_date = date.fromisoformat(item)
                if scene_id.split("_")[3] != item.replace("-", ""):
                    raise ValueError
                if (
                    not period_windows[period_id][0]
                    <= acquisition_date
                    < period_windows[period_id][1]
                ):
                    raise ValueError
            except ValueError:
                raise ValueError("scene acquisition dates are invalid") from None
    roles = manifest.get("band_roles")
    if (
        not isinstance(roles, dict)
        or roles.get("red") != "SR_B4"
        or roles.get("nir") != "SR_B5"
        or roles.get("calibration")
        != "Landsat Collection 2 Level 2 surface reflectance scale/offset"
    ):
        raise ValueError("band role evidence is invalid")
    provenance = manifest.get("provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("contract_version") != "v0.3-preparation-1"
    ):
        raise ValueError("preparation provenance is incomplete")
    for period_id in ("a", "b"):
        period_provenance = provenance.get(f"period_{period_id}")
        binding = bindings[period_id]
        required_provenance = {
            "scene_ids",
            "acquisition_dates",
            "asset_identity_hashes",
            "asset_key_map",
            "qa_policy",
            "scale_offset",
            "resampling",
            "composite_policy",
            "source_hashes",
        }
        if not isinstance(period_provenance, dict) or not required_provenance.issubset(
            period_provenance
        ):
            raise ValueError("period preparation provenance is incomplete")
        if (
            period_provenance["scene_ids"] != binding["scene_ids"]
            or period_provenance["acquisition_dates"] != binding["acquisition_dates"]
        ):
            raise ValueError("scene provenance binding is contradictory")
        if (
            isinstance(period_provenance.get("aoi_area_m2"), bool)
            or not isinstance(period_provenance.get("aoi_area_m2"), (int, float))
            or not math.isfinite(float(period_provenance["aoi_area_m2"]))
            or float(period_provenance["aoi_area_m2"]) <= 0
        ):
            raise ValueError("period AOI preparation evidence is invalid")
        if (
            not isinstance(period_provenance["qa_policy"], str)
            or not period_provenance["qa_policy"]
        ):
            raise ValueError("QA preparation policy is invalid")
        if period_provenance["composite_policy"] != "deterministic priority-fill":
            raise ValueError("composite preparation policy is invalid")
        if period_provenance["resampling"] != {"qa": "nearest", "reflectance": "bilinear"}:
            raise ValueError("preparation resampling policy is invalid")
        scale_offset = period_provenance["scale_offset"]
        if not isinstance(scale_offset, dict) or set(scale_offset) != {"red", "nir08"}:
            raise ValueError("preparation calibration is incomplete")
        for values in scale_offset.values():
            if (
                not isinstance(values, list)
                or len(values) != 2
                or any(
                    isinstance(item, bool) or not isinstance(item, (int, float)) for item in values
                )
                or values != [2.75e-05, -0.2]
            ):
                raise ValueError("preparation calibration is invalid")
        for identity_key in ("asset_identity_hashes", "source_hashes"):
            hashes = period_provenance[identity_key]
            if (
                not isinstance(hashes, list)
                or not hashes
                or any(not isinstance(item, dict) for item in hashes)
            ):
                raise ValueError("preparation source identity is incomplete")
            for item in hashes:
                required_assets = {"red", "nir08", "qa_pixel", "qa_radsat", "qa_aerosol"}
                if set(item) != required_assets or any(
                    not isinstance(value, str) or not _SHA256.fullmatch(value)
                    for value in item.values()
                ):
                    raise ValueError("preparation source identity is invalid")
        if period_provenance["asset_identity_hashes"] != period_provenance["source_hashes"]:
            raise ValueError("preparation source identities disagree")
        asset_key_map = period_provenance["asset_key_map"]
        if (
            not isinstance(asset_key_map, list)
            or not asset_key_map
            or any(
                not isinstance(item, dict)
                or item
                != {
                    "SR_B4": "red",
                    "SR_B5": "nir08",
                    "QA_PIXEL": "qa_pixel",
                    "QA_RADSAT": "qa_radsat",
                    "SR_QA_AEROSOL": "qa_aerosol",
                }
                for item in asset_key_map
            )
        ):
            raise ValueError("preparation asset mapping is invalid")
    if manifest.get("input_file") != "inputs.npz":
        raise ValueError("input identity binding is invalid")
    input_path = _safe_file(root, manifest.get("input_file"))
    if (
        not isinstance(manifest.get("input_sha256"), str)
        or not _SHA256.fullmatch(manifest["input_sha256"])
        or _sha256(input_path) != manifest["input_sha256"]
    ):
        raise ValueError("input fingerprint mismatch")
    with np.load(input_path, allow_pickle=False) as bundle:
        required = {"red_a", "nir_a", "red_b", "nir_b", "aoi_a", "aoi_b", "prep_a", "prep_b"}
        if set(bundle.files) != required:
            raise ValueError("input arrays are incomplete")
        arrays = {key: bundle[key] for key in required}
    if any(arrays[key].dtype != np.float32 for key in ("red_a", "nir_a", "red_b", "nir_b")):
        raise ValueError("reflectance dtype mismatch")
    shape = tuple(int(value) for value in manifest.get("shape", []))
    if len(shape) != 2 or any(array.shape != shape for array in arrays.values()):
        raise ValueError("input shape mismatch")
    if not all(arrays[key].dtype == np.bool_ for key in ("aoi_a", "aoi_b", "prep_a", "prep_b")):
        raise ValueError("mask dtype mismatch")
    if not np.array_equal(arrays["aoi_a"], arrays["aoi_b"]):
        raise ValueError("AOI identity mismatch")
    ndvi_a, valid_a = _reference(
        arrays["red_a"], arrays["nir_a"], arrays["prep_a"], arrays["aoi_a"]
    )
    ndvi_b, valid_b = _reference(
        arrays["red_b"], arrays["nir_b"], arrays["prep_b"], arrays["aoi_b"]
    )
    common = arrays["aoi_a"] & arrays["prep_a"] & arrays["prep_b"] & valid_a & valid_b
    artifact_names = manifest.get("artifacts")
    if not isinstance(artifact_names, dict):
        raise ValueError("artifact bindings missing")
    required_artifacts = (
        "ndvi_before_raster",
        "ndvi_after_raster",
        "ndvi_change_raster",
        "ndvi_valid_before",
        "ndvi_valid_after",
        "ndvi_common_comparison",
    )
    if set(artifact_names) != set(required_artifacts) or any(
        artifact_names.get(name) != f"{name}.tif" for name in required_artifacts
    ):
        raise ValueError("artifact identity bindings missing")
    fingerprints = manifest.get("artifact_sha256")
    if not isinstance(fingerprints, dict) or set(fingerprints) != set(required_artifacts):
        raise ValueError("artifact fingerprints missing")
    for name, filename in artifact_names.items():
        if (
            not isinstance(fingerprints.get(name), str)
            or not _SHA256.fullmatch(fingerprints[name])
            or _sha256(_safe_file(root, filename)) != fingerprints[name]
        ):
            raise ValueError("artifact fingerprint mismatch")
    grid = manifest.get("grid")
    if not isinstance(grid, dict):
        raise ValueError("grid evidence missing")
    if (
        grid.get("dimensions") != list(shape)
        or grid.get("crs") != "EPSG:32649"
        or not isinstance(grid.get("transform"), list)
        or len(grid["transform"]) != 6
        or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in grid["transform"]
        )
        or isinstance(grid.get("resolution_m"), bool)
        or not isinstance(grid.get("resolution_m"), (int, float))
        or not math.isfinite(float(grid["resolution_m"]))
        or float(grid["resolution_m"]) != 30.0
    ):
        raise ValueError("grid identity evidence is incomplete")
    actual_a = _read_raster(root, artifact_names["ndvi_before_raster"], shape, grid=grid)
    actual_b = _read_raster(root, artifact_names["ndvi_after_raster"], shape, grid=grid)
    actual_delta = _read_raster(root, artifact_names["ndvi_change_raster"], shape, grid=grid)
    actual_valid_a = _read_raster(
        root, artifact_names["ndvi_valid_before"], shape, grid=grid, mask=True
    )
    actual_valid_b = _read_raster(
        root, artifact_names["ndvi_valid_after"], shape, grid=grid, mask=True
    )
    actual_common = _read_raster(
        root, artifact_names["ndvi_common_comparison"], shape, grid=grid, mask=True
    )
    if (
        np.any(np.isfinite(actual_a) & ~valid_a)
        or np.any(np.isfinite(actual_b) & ~valid_b)
        or np.any(np.isfinite(actual_delta) & ~common)
    ):
        raise ValueError("invalid-region pixels must be NoData")
    if (
        np.any(~np.isfinite(actual_a[valid_a]))
        or np.any(~np.isfinite(actual_b[valid_b]))
        or np.any(~np.isfinite(actual_delta[common]))
    ):
        raise ValueError("valid-region pixels must be finite")
    if not np.any(common):
        return {
            "status": "NOT_COMPARABLE",
            "reference_policy": REFERENCE_POLICY,
            "production_provenance_authenticated": False,
            "scope": "standalone captured-package numerical consistency only",
            "compared_pixels": 0,
            "mask_mismatch_count": int(
                np.count_nonzero(actual_valid_a != valid_a)
                + np.count_nonzero(actual_valid_b != valid_b)
                + np.count_nonzero(actual_common != common)
            ),
            "tolerances": TOLERANCES,
            "limitations": ["The common comparison set is empty."],
        }
    mask_mismatch = int(
        np.count_nonzero(actual_valid_a != valid_a)
        + np.count_nonzero(actual_valid_b != valid_b)
        + np.count_nonzero(actual_common != common)
    )

    def stats(
        actual: np.ndarray, expected: np.ndarray, domain: np.ndarray, tolerance: float
    ) -> dict[str, float]:
        diff = np.abs(actual[domain].astype(np.float64) - expected[domain].astype(np.float64))
        return {
            "mae": float(np.mean(diff)),
            "rmse": float(np.sqrt(np.mean(diff * diff))),
            "maxae": float(np.max(diff)),
            "tolerance": tolerance,
        }

    a_stats, b_stats = (
        stats(actual_a, ndvi_a, valid_a, 1e-6),
        stats(actual_b, ndvi_b, valid_b, 1e-6),
    )
    expected_delta = ndvi_b - ndvi_a
    d_stats = stats(actual_delta, expected_delta, common, 2e-6)
    passed = mask_mismatch == 0 and all(
        item[key] <= item["tolerance"]
        for item in (a_stats, b_stats, d_stats)
        for key in ("mae", "rmse", "maxae")
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "reference_policy": REFERENCE_POLICY,
        "production_provenance_authenticated": False,
        "scope": "standalone captured-package numerical consistency only",
        "compared_pixels": int(common.sum()),
        "mask_mismatch_count": mask_mismatch,
        "period_a": a_stats,
        "period_b": b_stats,
        "delta": d_stats,
        "tolerances": TOLERANCES,
        "limitations": [
            "Computational agreement is not external remote-sensing accuracy or ecological confidence."
        ],
    }
