import hashlib
import json
import shutil

import numpy as np
import pytest
from PIL import Image

from geochange.fixture import (
    FIXTURE_ROOT,
    MAX_FIXTURE_BYTES,
    compute_cached_change,
    load_manifest,
    scene_evidence,
    validate_binding,
)
from geochange.models import GeoChangeTask
from geochange.summary import summarize_change
from geochange.verifier import verify_change


def task():
    return GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )


def test_manifest_matches_independently_saved_stac_and_real_pixels():
    manifest = load_manifest()
    assert sum(p.stat().st_size for p in FIXTURE_ROOT.iterdir()) < MAX_FIXTURE_BYTES
    for period, scene in manifest["scenes"].items():
        item = json.loads((FIXTURE_ROOT / f"{period}.item.json").read_text())
        assert scene["item_id"] == item["id"]
        assert scene["collection"] == item["collection"] == "sentinel-2-l2a"
        assert scene["acquisition_datetime"] == item["properties"]["datetime"]
        assert scene["acquisition_date"] == item["properties"]["datetime"][:10]
        assert scene["fixture_version"] == manifest["fixture_version"]
        assert (
            scene["fixture_sha256"]
            == hashlib.sha256((FIXTURE_ROOT / scene["fixture_file"]).read_bytes()).hexdigest()
        )
        assert scene["dimensions"] == [64, 64]
        assert scene["crs"] == f"EPSG:{item['properties']['proj:epsg']}" == "EPSG:32650"
        for band in ("red", "nir"):
            asset = scene[f"{band}_asset_identity"]
            assert asset["href"] == item["assets"][band]["href"]
            assert asset["band"] == ("B04" if band == "red" else "B08")
            assert asset["source_shape"] == item["assets"][band]["proj:shape"]
            for key in ("scale", "offset", "nodata"):
                assert asset[key] == item["assets"][band]["raster:bands"][0][key]
        path = FIXTURE_ROOT / scene["fixture_file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == scene["fixture_sha256"]
        with np.load(path, allow_pickle=False) as bundle:
            assert set(bundle.files) == {"red", "nir"}
            assert bundle["red"].dtype == np.uint16
            assert bundle["red"].shape == (64, 64)
            assert np.unique(bundle["red"]).size > 100
    assert "Sentinel_Data_Legal_Notice" in manifest["source"]["license_url"]
    assert "Contains modified Copernicus Sentinel data" in manifest["source"]["attribution"]


@pytest.mark.parametrize("name", ["manifest.json", "period_a.npz", "period_b.npz"])
def test_corrupt_fixture_fails_closed(tmp_path, name):
    root = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, root)
    path = root / name
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValueError, match="integrity|checksum"):
        compute_cached_change(task(), scene_evidence(task()), root=root)


@pytest.mark.parametrize("key", list(scene_evidence(task())))
def test_every_metadata_binding_field_fails_closed(key):
    evidence = scene_evidence(task())
    evidence[key] += "mismatch"
    with pytest.raises(ValueError, match="binding mismatch"):
        validate_binding(task(), evidence)


def test_missing_extra_and_unsupported_period_fail_closed():
    evidence = scene_evidence(task())
    for changed in ({}, {**evidence, "path": "caller-owned"}):
        with pytest.raises(ValueError, match="binding mismatch"):
            validate_binding(task(), changed)
    unsupported = task().model_copy(update={"period_a": task().period_b})
    with pytest.raises(ValueError, match="no cached fixture"):
        scene_evidence(unsupported)


def test_real_radiometry_ndvi_masks_and_artifacts_are_reproducible(tmp_path):
    evidence = scene_evidence(task())
    first = compute_cached_change(task(), evidence, artifact_dir=tmp_path / "a")
    second = compute_cached_change(task(), evidence, artifact_dir=tmp_path / "b")
    np.testing.assert_array_equal(first.delta, second.delta)
    manifest = load_manifest()
    ndvis = []
    masks = []
    for period in ("period_a", "period_b"):
        with np.load(FIXTURE_ROOT / manifest["scenes"][period]["fixture_file"]) as bundle:
            red = bundle["red"].astype(np.float32) * 0.0001 - 0.1
            nir = bundle["nir"].astype(np.float32) * 0.0001 - 0.1
            valid = (
                (bundle["red"] != 0)
                & (bundle["nir"] != 0)
                & (red >= 0)
                & (nir >= 0)
                & ((red + nir) > 0)
            )
            ndvi = np.full(red.shape, np.nan, dtype=np.float32)
            ndvi[valid] = (nir[valid] - red[valid]) / (nir[valid] + red[valid])
            ndvis.append(ndvi)
            masks.append(valid)
    np.testing.assert_array_equal(first.valid_mask, masks[0] & masks[1])
    np.testing.assert_allclose(
        first.delta[first.valid_mask], (ndvis[1] - ndvis[0])[first.valid_mask]
    )
    metrics = summarize_change(first, task())
    assert 0 < metrics["valid_pixels"] < 4096
    assert metrics["valid_analysis_area_m2"] == metrics["valid_pixels"] * 100
    assert metrics == summarize_change(second, task())
    for name in first.artifacts.values():
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
        assert (tmp_path / "a" / name).stat().st_size < 2_000_000


def _verified_fixture_result(tmp_path):
    configured = task()
    evidence = scene_evidence(configured)
    change = compute_cached_change(configured, evidence, artifact_dir=tmp_path)
    metrics = summarize_change(change, configured)
    aoi = {
        "catalog_key": configured.aoi_key,
        "crs": "EPSG:4326",
        "source": "taskpilot.geochange.catalog.v1",
    }
    return configured, evidence, change, metrics, aoi


@pytest.mark.parametrize(
    "metric",
    [
        "valid_pixels",
        "mean_ndvi_period_a",
        "mean_ndvi_period_b",
        "mean_delta_ndvi",
        "decline_threshold",
        "valid_analysis_area_m2",
        "significant_decline_area_m2",
        "decline_percentage",
    ],
)
def test_verifier_rejects_forged_fixture_metrics(tmp_path, metric):
    configured, evidence, change, metrics, aoi = _verified_fixture_result(tmp_path)
    forged = dict(metrics)
    forged[metric] = float(forged[metric]) + (0.01 if metric != "valid_analysis_area_m2" else 100.0)
    result = verify_change(
        change,
        artifacts=change.artifacts,
        artifact_root=tmp_path,
        task=configured,
        aoi_evidence=aoi,
        scene_evidence=evidence,
        execution_mode="CACHED_REAL_SENTINEL2_RASTER",
        raster_source="cached_real_sentinel2_fixture",
        metrics=forged,
    )
    assert result["code"] in {
        "metrics_mismatch",
        "delta_metric_mismatch",
        "decline_metric_mismatch",
        "valid_area_mismatch",
        "empty_valid_pixels",
    }


@pytest.mark.parametrize("artifact_name", ["ndvi_before", "ndvi_after", "ndvi_change"])
def test_verifier_rejects_unrelated_valid_png_for_each_role(tmp_path, artifact_name):
    configured, evidence, change, metrics, aoi = _verified_fixture_result(tmp_path)
    Image.new("L", (64, 64), color=17).save(tmp_path / f"{artifact_name}.png", format="PNG")
    result = verify_change(
        change,
        artifacts=change.artifacts,
        artifact_root=tmp_path,
        task=configured,
        aoi_evidence=aoi,
        scene_evidence=evidence,
        execution_mode="CACHED_REAL_SENTINEL2_RASTER",
        raster_source="cached_real_sentinel2_fixture",
        metrics=metrics,
    )
    assert result["status"] == "failed"
    assert result["code"] == "artifact_content_mismatch"
