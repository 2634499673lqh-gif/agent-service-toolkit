"""Deterministic exploratory NDBI evidence assessment.

The assessment reports continuous NDBI statistics and threshold sensitivity. It
does not treat SCL classes or literature thresholds as independent built-up
ground truth and never authorizes an urban-expansion claim. All source files
are verified against code-owned digests before any raster data is loaded.
"""

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-ndbi-v1"
NDWI_ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-ndwi-v5"
THRESHOLDS = (-0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4)
SCL_VALID_CLASSES = (4, 5, 6)

# Values come from the independently verified evidence package. Mutable
# manifests and sidecars cannot replace these trust anchors.
TRUSTED_SHA256 = {
    "manifest.json": "253b37ed24d81f3c869653155579e9eeb9f3a63cb3eb5ac257eefc67d6071e6a",
    "period_a.npz": "e7a3ecb6b936706c94c0d94899c9c2aac23bddc4369a759b115ea87c12655b56",
    "period_b.npz": "e08d009765786ff887c536849a238d9552f12fea9dc96dcd5780fa08d9c9ffe8",
    "period_a.item.json": "ff1295247c91465bc74221a948065e9718e3849d667a8f15681e37bd66b6aaef",
    "period_b.item.json": "e39aac3fc11499ae274a065d782cf0978992cdec5f266c1db597ad9089c53a47",
}
TRUSTED_NDWI_SHA256 = {
    "manifest.json": "90416a43c08d127a4ed46b313481d803123d04109c94bb4542e1e4439e867aad",
    "period_a.npz": "a3a40d907c5e33f633667aafadf0a378f3934c466a30954c533d762f605b6636",
    "period_b.npz": "8793b491fca151d467764e6aafb20752fdbde3df45c1af104b24bcf910838f54",
}
EXPECTED_TARGET_GRID = {
    "crs": "EPSG:32650",
    "transform": [10, 0, 199980, 0, -10, 3400020],
    "resolution_m": 10,
    "dimensions": [24, 24],
}
EXPECTED_B11_SOURCE_GRID = {
    "crs": "EPSG:32650",
    "transform": [20, 0, 199980, 0, -20, 3400020],
    "resolution_m": 20,
    "dimensions": [5490, 5490],
}


def _sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"required evidence file is missing: {path.name}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_digests(root: Path, expected: dict[str, str]) -> None:
    for name, digest in expected.items():
        if _sha256(root / name) != digest:
            raise ValueError(f"evidence integrity mismatch: {name}")


def _verify_sidecar(root: Path, expected_manifest: str) -> None:
    sidecar = root / "manifest.sha256"
    if not sidecar.is_file() or sidecar.read_text(encoding="ascii").strip() != expected_manifest:
        raise ValueError("manifest sidecar mismatch")


def _asset_contract(manifest_scene: dict[str, Any], item: dict[str, Any], key: str) -> None:
    manifest_asset = manifest_scene[f"{key}_asset_identity"]
    asset_key = "swir16" if key == "b11" else "nir" if key == "b08" else "scl"
    item_asset = item["assets"][asset_key]
    if manifest_asset["href"] != item_asset["href"]:
        raise ValueError(f"{key} asset URL mismatch")
    if manifest_asset["source_shape"] != item_asset["proj:shape"]:
        raise ValueError(f"{key} source dimensions mismatch")
    if manifest_asset["source_transform"] != item_asset["proj:transform"]:
        raise ValueError(f"{key} source transform mismatch")
    band = item_asset["raster:bands"][0]
    for field in ("nodata", "scale", "offset"):
        if manifest_asset[field] != band.get(field):
            raise ValueError(f"{key} {field} mismatch")
    if key != "scl" and manifest_asset["source_resolution_m"] != item_asset["gsd"]:
        raise ValueError(f"{key} native resolution mismatch")
    if key == "scl" and manifest_asset["source_resolution_m"] != band["spatial_resolution"]:
        raise ValueError("SCL native resolution mismatch")


def validate_evidence(root: Path = ROOT, *, ndwi_root: Path = NDWI_ROOT) -> dict[str, Any]:
    """Validate all trusted evidence and asset contracts before raster loading."""

    root = root.resolve()
    ndwi_root = ndwi_root.resolve()
    _verify_digests(root, TRUSTED_SHA256)
    _verify_sidecar(root, TRUSTED_SHA256["manifest.json"])
    _verify_digests(ndwi_root, TRUSTED_NDWI_SHA256)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    ndwi_manifest = json.loads((ndwi_root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["fixture_version"] != "geochange.real-sentinel2-ndbi.v1":
        raise ValueError("unexpected NDBI fixture version")
    if manifest["preparation"]["target_grid"] != EXPECTED_TARGET_GRID:
        raise ValueError("target grid contract mismatch")
    if manifest["preparation"]["b11_source_grid"] != EXPECTED_B11_SOURCE_GRID:
        raise ValueError("B11 source grid contract mismatch")
    if manifest["preparation"]["b11_to_target_resampling"] != "nearest_neighbour_2x2_block":
        raise ValueError("B11 resampling contract mismatch")
    if manifest["source"].get("license_url") is None:
        raise ValueError("source license provenance is missing")
    for period in ("period_a", "period_b"):
        scene = manifest["scenes"].get(period)
        if not isinstance(scene, dict):
            raise ValueError("required period evidence is missing")
        item = json.loads((root / f"{period}.item.json").read_text(encoding="utf-8"))
        if (scene["item_id"], scene["collection"], scene["acquisition_datetime"]) != (
            item["id"],
            item["collection"],
            item["properties"]["datetime"],
        ):
            raise ValueError("STAC identity mismatch")
        if scene["acquisition_date"] != item["properties"]["datetime"][:10]:
            raise ValueError("acquisition date mismatch")
        if scene["crs"] != "EPSG:32650" or item["properties"]["proj:epsg"] != 32650:
            raise ValueError("scene CRS mismatch")
        if scene["target_dimensions"] != [24, 24] or scene["b11_native_dimensions"] != [12, 12]:
            raise ValueError("crop dimensions contract mismatch")
        if scene["target_transform"] != EXPECTED_TARGET_GRID["transform"]:
            raise ValueError("target transform mismatch")
        _asset_contract(scene, item, "b11")
        _asset_contract(scene, item, "b08")
        _asset_contract(scene, item, "scl")
        b11 = scene["b11_asset_identity"]
        b08 = scene["b08_asset_identity"]
        scl = scene["scl_asset_identity"]
        if b11["window_pixel_origin"] != [2060, 620] or b11["window_size_pixels"] != [12, 12]:
            raise ValueError("B11 crop contract mismatch")
        if b08["window_pixel_origin"] != [4120, 1240] or b08["window_size_pixels"] != [24, 24]:
            raise ValueError("B08 crop contract mismatch")
        if scl["window_pixel_origin"] != [2060, 620] or scl["window_size_pixels"] != [12, 12]:
            raise ValueError("SCL crop contract mismatch")
        if b11["source_transform"][2:] != [199980, 0, -20, 3400020]:
            raise ValueError("B11 alignment contract mismatch")
        if b08["source_transform"][2:] != [199980, 0, -10, 3400020]:
            raise ValueError("B08 alignment contract mismatch")
        if scene["reused_v5_fixture_manifest_sha256"] != TRUSTED_NDWI_SHA256["manifest.json"]:
            raise ValueError("reused NDWI manifest binding mismatch")
        with np.load(root / scene["fixture_file"], allow_pickle=False) as bundle:
            if set(bundle.files) != {"nir", "scl", "coverage", "swir16_native_20m"}:
                raise ValueError("NDBI array names mismatch")
            if bundle["nir"].shape != (24, 24) or bundle["nir"].dtype != np.uint16:
                raise ValueError("B08 array geometry or dtype mismatch")
            if bundle["scl"].shape != (12, 12) or bundle["scl"].dtype != np.uint8:
                raise ValueError("SCL array geometry or dtype mismatch")
            if bundle["coverage"].shape != (24, 24) or bundle["coverage"].dtype != np.uint8:
                raise ValueError("coverage array geometry or dtype mismatch")
            if (
                bundle["swir16_native_20m"].shape != (12, 12)
                or bundle["swir16_native_20m"].dtype != np.uint16
            ):
                raise ValueError("B11 array geometry or dtype mismatch")
            if (
                hashlib.sha256(bundle["swir16_native_20m"].tobytes()).hexdigest()
                != b11["crop_sha256"]
            ):
                raise ValueError("B11 crop checksum mismatch")
            with np.load(ndwi_root / f"{period}.npz", allow_pickle=False) as reused:
                for name in ("nir", "scl", "coverage"):
                    if not np.array_equal(bundle[name], reused[name]):
                        raise ValueError(f"reused {name} pixels mismatch")
                if int(bundle["coverage"].sum()) != scene["coverage_pixels"]:
                    raise ValueError("coverage provenance mismatch")
    if ndwi_manifest["fixture_version"] != "geochange.real-sentinel2-ndwi.v1":
        raise ValueError("unexpected reused NDWI fixture version")
    return manifest


def _load_period(
    period: str, manifest: dict[str, Any], *, root: Path
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    scene = manifest["scenes"][period]
    with np.load(root / scene["fixture_file"], allow_pickle=False) as bundle:
        b08_asset = scene["b08_asset_identity"]
        b11_asset = scene["b11_asset_identity"]
        b08 = bundle["nir"].astype(np.float32) * b08_asset["scale"] + b08_asset["offset"]
        b11_native = (
            bundle["swir16_native_20m"].astype(np.float32) * b11_asset["scale"]
            + b11_asset["offset"]
        )
        # Nearest neighbour is an explicit 2x2 block expansion, not new 10 m information.
        b11 = np.repeat(np.repeat(b11_native, 2, axis=0), 2, axis=1)
        scl = np.repeat(np.repeat(bundle["scl"], 2, axis=0), 2, axis=1)
        coverage = bundle["coverage"].astype(bool)
    denominator = b11 + b08
    valid = (
        coverage
        & np.isin(scl, SCL_VALID_CLASSES)
        & np.isfinite(b08)
        & np.isfinite(b11)
        & (b08 >= 0)
        & (b11 >= 0)
        & np.isfinite(denominator)
        & (denominator != 0)
    )
    ndbi = np.full(b08.shape, np.nan, dtype=np.float32)
    ndbi[valid] = (b11[valid] - b08[valid]) / denominator[valid]
    valid &= np.isfinite(ndbi) & (ndbi >= -1.00001) & (ndbi <= 1.00001)
    ndbi[~valid] = np.nan
    return ndbi, valid, scl


def assess(root: Path = ROOT, *, ndwi_root: Path = NDWI_ROOT) -> dict[str, Any]:
    root = root.resolve()
    manifest = validate_evidence(root, ndwi_root=ndwi_root)
    periods = {
        period: _load_period(period, manifest, root=root) for period in ("period_a", "period_b")
    }
    result: dict[str, Any] = {
        "fixture_version": "geochange.real-sentinel2-ndbi.v1",
        "formula": "(B11 - B08) / (B11 + B08)",
        "b11_resampling": "nearest_neighbour_2x2_block",
        "pixel_area_m2": 100,
        "thresholds": list(THRESHOLDS),
        "threshold_interpretation": "exploratory candidate counts only; no threshold is validated built-up ground truth",
        "periods": {},
    }
    for period, (ndbi, valid, scl) in periods.items():
        result["periods"][period] = {
            "valid_pixels": int(valid.sum()),
            "mean_ndbi": float(np.mean(ndbi[valid])),
            "min_ndbi": float(np.min(ndbi[valid])),
            "max_ndbi": float(np.max(ndbi[valid])),
            "scl_class_counts": {
                str(cls): int((valid & (scl == cls)).sum()) for cls in SCL_VALID_CLASSES
            },
            "candidate_threshold_counts": {
                str(threshold): int((valid & (ndbi > threshold)).sum()) for threshold in THRESHOLDS
            },
        }
    common = periods["period_a"][1] & periods["period_b"][1]
    if not np.any(common):
        raise ValueError("common-valid analysis mask is empty")
    delta = periods["period_b"][0] - periods["period_a"][0]
    result["common_valid_pixels"] = int(common.sum())
    result["common_valid_area_m2"] = int(common.sum()) * 100
    result["common_mean_ndbi_period_a"] = float(np.mean(periods["period_a"][0][common]))
    result["common_mean_ndbi_period_b"] = float(np.mean(periods["period_b"][0][common]))
    result["common_mean_delta_ndbi"] = float(np.mean(delta[common]))
    result["common_delta_min"] = float(np.min(delta[common]))
    result["common_delta_max"] = float(np.max(delta[common]))
    result["common_candidate_threshold_sensitivity"] = {
        str(threshold): {
            "period_a_pixels": int((common & (periods["period_a"][0] > threshold)).sum()),
            "period_b_pixels": int((common & (periods["period_b"][0] > threshold)).sum()),
            "difference_pixels": int(
                (common & (periods["period_b"][0] > threshold)).sum()
                - (common & (periods["period_a"][0] > threshold)).sum()
            ),
        }
        for threshold in THRESHOLDS
    }
    return result


if __name__ == "__main__":
    print(json.dumps(assess(), indent=2, sort_keys=True))
