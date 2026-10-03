import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parents[2] / "data/geochange-fixtures/real-sentinel2-ndwi-v5"
_SCIENCE_PATH = Path(__file__).parents[2] / "scripts/validate_ndwi_science.py"
_SCIENCE_SPEC = importlib.util.spec_from_file_location("validate_ndwi_science", _SCIENCE_PATH)
assert _SCIENCE_SPEC and _SCIENCE_SPEC.loader
_SCIENCE = importlib.util.module_from_spec(_SCIENCE_SPEC)
_SCIENCE_SPEC.loader.exec_module(_SCIENCE)


def _manifest():
    path = ROOT / "manifest.json"
    assert (
        hashlib.sha256(path.read_bytes()).hexdigest()
        == (ROOT / "manifest.sha256").read_text().strip()
    )
    return json.loads(path.read_text())


def _expanded_scl(scl: np.ndarray) -> np.ndarray:
    return np.repeat(np.repeat(scl, 2, axis=0), 2, axis=1)


def test_ndwi_fixture_integrity_grid_and_provenance():
    manifest = _manifest()
    assert manifest["fixture_version"] == "geochange.real-sentinel2-ndwi.v1"
    assert manifest["preparation"]["target_grid"] == {
        "crs": "EPSG:32650",
        "resolution_m": 10,
        "dimensions": [24, 24],
    }
    for period, scene in manifest["scenes"].items():
        assert scene["coverage_pixels"] > 0
        assert scene["aoi_bounds_wgs84"] == [114.3, 30.5, 114.45, 30.62]
        assert (
            hashlib.sha256((ROOT / scene["fixture_file"]).read_bytes()).hexdigest()
            == scene["fixture_sha256"]
        )
        item = json.loads((ROOT / f"{period}.item.json").read_text())
        assert (scene["item_id"], scene["collection"], scene["acquisition_datetime"]) == (
            item["id"],
            item["collection"],
            item["properties"]["datetime"],
        )
        with np.load(ROOT / scene["fixture_file"], allow_pickle=False) as bundle:
            assert set(bundle.files) == {"green", "nir", "scl", "coverage"}
            assert bundle["green"].dtype == np.uint16
            assert bundle["nir"].dtype == np.uint16
            assert bundle["scl"].dtype == np.uint8
            assert (
                bundle["green"].shape == bundle["nir"].shape == bundle["coverage"].shape == (24, 24)
            )
            assert bundle["scl"].shape == (12, 12)
            assert int(bundle["coverage"].sum()) == scene["coverage_pixels"]
        for key in ("green", "nir"):
            asset = scene["asset_identity"][key]
            assert asset["source_resolution_m"] == 10
            assert asset["source_transform"] == [10, 0, 199980, 0, -10, 3400020]
            assert asset["scale"] == 0.0001
            assert asset["offset"] == -0.1
        scl = scene["asset_identity"]["scl"]
        assert scl["source_resolution_m"] == 20
        assert scl["source_transform"] == [20, 0, 199980, 0, -20, 3400020]


def test_ndwi_fixture_common_mask_has_reproducible_water_samples():
    manifest = _manifest()
    masks = []
    water_counts = []
    for period, scene in manifest["scenes"].items():
        with np.load(ROOT / scene["fixture_file"], allow_pickle=False) as bundle:
            green = bundle["green"].astype(np.float32) * 0.0001 - 0.1
            nir = bundle["nir"].astype(np.float32) * 0.0001 - 0.1
            scl = _expanded_scl(bundle["scl"])
            valid = (
                bundle["coverage"].astype(bool)
                & np.isin(scl, [4, 5, 6])
                & np.isfinite(green)
                & np.isfinite(nir)
                & (green >= 0)
                & (nir >= 0)
                & ((green + nir) > 0)
            )
            ndwi = (green - nir) / (green + nir)
            water = valid & (scl == 6)
            assert int(valid.sum()) > 100
            assert int(water.sum()) > 0
            assert np.all(np.isfinite(ndwi[valid]))
            masks.append(valid)
            water_counts.append(int(water.sum()))
    common = masks[0] & masks[1]
    assert int(common.sum()) > 100
    assert water_counts == [39, 47]


def test_existing_item_snapshots_expose_b11_for_future_reuse_without_preparing_ndbi():
    for period in ("period_a", "period_b"):
        item = json.loads((ROOT / f"{period}.item.json").read_text())
        asset = item["assets"]["swir16"]
        assert asset["gsd"] == 20
        assert asset["raster:bands"][0]["scale"] == 0.0001
        assert asset["raster:bands"][0]["offset"] == -0.1
        assert asset["raster:bands"][0]["nodata"] == 0


def test_science_assessment_reports_separate_periods_and_common_change_sensitivity():
    result = _SCIENCE.assess()
    assert result["periods"]["period_a"]["valid_pixels"] == 218
    assert result["periods"]["period_b"]["valid_pixels"] == 355
    assert result["periods"]["period_a"]["scl_water_pixels"] == 39
    assert result["periods"]["period_b"]["scl_water_pixels"] == 47
    assert result["common_valid_pixels"] == 160
    assert result["periods"]["period_a"]["thresholds"]["0.2"]["f1"] == 0.30434782608695654
    assert result["periods"]["period_b"]["thresholds"]["0.2"]["f1"] == 0.5309734513274337
    assert result["change_sensitivity"]["0.2"] == {
        "period_a_water_pixels": 2,
        "period_b_water_pixels": 22,
    }
