from dataclasses import replace

import numpy as np
import pytest

from geochange import GeoChangeTask, compute_vegetation_change, resolve_aoi, run_local_analysis
from geochange.fixture import compute_cached_change, scene_evidence
from geochange.stac import select_sentinel2
from geochange.summary import summarize_change
from geochange.verifier import verify_change


def _task(**kwargs):
    values = {
        "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
    }
    values.update(kwargs)
    return GeoChangeTask(**values)


def test_east_lake_and_task_are_bounded():
    assert resolve_aoi("Wuhan East Lake").bbox == (114.30, 30.50, 114.45, 30.62)
    assert _task().decline_threshold == -0.2


def test_unknown_aoi_and_invalid_period_rejected():
    with pytest.raises(ValueError, match="unsupported AOI"):
        resolve_aoi("Paris")
    with pytest.raises(ValueError):
        _task(period_a={"start": "2023-08-01", "end": "2023-07-01"})


def test_stac_selection_requires_assets_and_cloud_threshold():
    feature = {
        "id": "scene-1",
        "collection": "sentinel-2-l2a",
        "properties": {"datetime": "2023-07-10T00:00:00Z", "eo:cloud_cover": 5},
        "assets": {
            "red": {"href": "https://example/red.tif"},
            "nir": {"href": "https://example/nir.tif"},
        },
    }
    period = _task().period_a
    item = select_sentinel2([feature], period, 10)
    assert item.item_id == "scene-1"
    with pytest.raises(ValueError):
        select_sentinel2([feature], period, 1)


def test_ndvi_delta_summary_and_png(tmp_path):
    red_a = np.array([[1, 1], [2, 2]], dtype=float)
    nir_a = np.array([[3, 1], [4, 2]], dtype=float)
    change = compute_vegetation_change(red_a, nir_a, red_a * 2, nir_a, artifact_dir=tmp_path)
    assert np.isclose(change.ndvi_a[0, 0], 0.5)
    assert change.delta.shape == (2, 2)
    assert set(change.artifacts) == {"ndvi_before", "ndvi_after", "ndvi_change"}
    summary = summarize_change(change, _task(decline_threshold=-0.1))
    assert summary["valid_pixels"] == 4
    assert 0 <= summary["decline_percentage"] <= 100
    assert (
        verify_change(change, artifacts=change.artifacts, artifact_root=tmp_path)["status"]
        == "passed"
    )


def test_divide_by_zero_and_grid_mismatch_rejected():
    with pytest.raises(ValueError, match="no valid pixels"):
        compute_vegetation_change([[1]], [[-1]], [[1]], [[-1]])
    with pytest.raises(ValueError, match="matching"):
        compute_vegetation_change([[1]], [[1, 2]], [[1]], [[1]])


def test_local_analysis_returns_bounded_result(tmp_path):
    result = run_local_analysis(
        _task(),
        red_a=np.array([[1.0]]),
        nir_a=np.array([[3.0]]),
        red_b=np.array([[2.0]]),
        nir_b=np.array([[3.0]]),
        artifact_root=tmp_path,
        provenance={"source": "local_real_raster_fixture"},
    )
    assert result.verifier_status == "passed"
    assert result.metrics["valid_pixels"] == 1


def _complete_evidence(
    task: GeoChangeTask,
) -> tuple[dict[str, str], dict[str, str], dict[str, float | int]]:
    aoi = {
        "catalog_key": task.aoi_key,
        "crs": "EPSG:4326",
        "source": "taskpilot.geochange.catalog.v1",
    }
    scenes = {
        "period_a_item_id": "scene-a",
        "period_a_date": task.period_a.start.isoformat(),
        "period_a_collection": "sentinel-2-l2a",
        "period_a_cloud_cover": "10",
        "period_a_red": "red",
        "period_a_nir": "nir",
        "period_b_item_id": "scene-b",
        "period_b_date": task.period_b.start.isoformat(),
        "period_b_collection": "sentinel-2-l2a",
        "period_b_cloud_cover": "10",
        "period_b_red": "red",
        "period_b_nir": "nir",
    }
    metrics = {
        "valid_pixels": 1,
        "valid_analysis_area_m2": 100,
        "mean_ndvi_period_a": 0.5,
        "mean_ndvi_period_b": 0.4,
        "mean_delta_ndvi": -0.1,
        "significant_decline_area_m2": 0,
        "decline_percentage": 0,
        "decline_threshold": task.decline_threshold,
    }
    return aoi, scenes, metrics


@pytest.mark.parametrize(
    ("change", "execution_mode", "evidence_mutation", "expected_code"),
    [
        (
            "valid",
            "REAL_STAC_LOCAL_FIXTURE",
            lambda e: e.update(period_a_item_id="scene-b"),
            "scenes_not_distinct",
        ),
        (
            "valid",
            "REAL_STAC_LOCAL_FIXTURE",
            lambda e: e.update(period_a_date="2022-01-01"),
            "period_a_date_invalid",
        ),
        (
            "valid",
            "REAL_STAC_LOCAL_FIXTURE",
            lambda e: e.update(period_a_collection="landsat"),
            "scene_collection_invalid",
        ),
        (
            "valid",
            "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE",
            lambda e: e.update(period_a_red="synthetic"),
            "provenance_invalid",
        ),
        (
            "valid",
            "REAL_STAC_LOCAL_FIXTURE",
            lambda e: e.update(period_a_cloud_cover="nan"),
            "scene_metadata_invalid",
        ),
    ],
)
def test_complete_verifier_rejects_invalid_external_evidence(
    tmp_path, change, execution_mode, evidence_mutation, expected_code
):
    task = _task()
    red = np.array([[1.0]])
    nir = np.array([[3.0]])
    computed = compute_vegetation_change(red, nir, red * 2, nir, artifact_dir=tmp_path)
    aoi, scenes, metrics = _complete_evidence(task)
    evidence_mutation(scenes)
    result = verify_change(
        computed,
        artifacts=computed.artifacts,
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=scenes,
        execution_mode=execution_mode,
        metrics=metrics,
    )
    assert result == {"status": "failed", "code": expected_code}


def test_complete_verifier_rejects_undeclared_or_incomplete_contract(tmp_path):
    task = _task()
    computed = compute_vegetation_change([[1.0]], [[3.0]], [[2.0]], [[3.0]], artifact_dir=tmp_path)
    aoi, scenes, metrics = _complete_evidence(task)
    result = verify_change(
        computed,
        artifacts={"ndvi_before": "ndvi_before"},
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=scenes,
        execution_mode="REAL_STAC_LOCAL_FIXTURE",
        metrics=metrics,
    )
    assert result["code"] == "artifacts_incomplete"


@pytest.mark.parametrize(
    ("mutate", "expected_code"),
    [
        (lambda a, s, m: a.clear(), "aoi_evidence_invalid"),
        (lambda a, s, m: s.pop("period_a_item_id"), "period_a_evidence_missing"),
        (lambda a, s, m: s.pop("period_b_item_id"), "period_b_evidence_missing"),
        (lambda a, s, m: s.update(period_a_red=""), "period_a_evidence_missing"),
        (lambda a, s, m: s.update(period_b_nir=""), "period_b_evidence_missing"),
        (lambda a, s, m: None, "execution_mode_invalid"),
        (lambda a, s, m: m.pop("mean_delta_ndvi"), "metrics_incomplete"),
        (lambda a, s, m: m.update(mean_delta_ndvi=float("nan")), "metrics_non_finite"),
        (lambda a, s, m: m.update(mean_ndvi_period_a=2), "ndvi_range_invalid"),
        (lambda a, s, m: m.update(valid_analysis_area_m2=-1), "area_invalid"),
        (lambda a, s, m: m.update(significant_decline_area_m2=200), "area_invalid"),
        (lambda a, s, m: m.update(decline_percentage=101), "percentage_or_threshold_invalid"),
    ],
)
def test_complete_verifier_rejects_required_contract_failures(tmp_path, mutate, expected_code):
    task = _task()
    computed = compute_vegetation_change([[1.0]], [[3.0]], [[2.0]], [[3.0]], artifact_dir=tmp_path)
    aoi, scenes, metrics = _complete_evidence(task)
    mutate(aoi, scenes, metrics)
    mode = "REAL_STAC_LOCAL_FIXTURE" if expected_code != "execution_mode_invalid" else "unsupported"
    result = verify_change(
        computed,
        artifacts=computed.artifacts,
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=scenes,
        execution_mode=mode,
        metrics=metrics,
    )
    assert result["code"] == expected_code


def test_cached_real_sentinel_mode_requires_manifest_and_asset_binding(tmp_path):
    task = _task()
    evidence = scene_evidence(task)
    computed = compute_cached_change(task, evidence, artifact_dir=tmp_path)
    aoi, _, _ = _complete_evidence(task)
    metrics = summarize_change(computed, task)
    assert (
        verify_change(
            computed,
            artifacts=computed.artifacts,
            artifact_root=tmp_path,
            task=task,
            aoi_evidence=aoi,
            scene_evidence=evidence,
            execution_mode="CACHED_REAL_SENTINEL2_RASTER",
            raster_source="cached_real_sentinel2_fixture",
            metrics=metrics,
        )["status"]
        == "passed"
    )
    evidence["fixture_manifest"] = "tampered"
    result = verify_change(
        computed,
        artifacts=computed.artifacts,
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=evidence,
        execution_mode="CACHED_REAL_SENTINEL2_RASTER",
        raster_source="cached_real_sentinel2_fixture",
        metrics=metrics,
    )
    assert result["code"] == "fixture_manifest_invalid"


def test_verifier_rejects_missing_artifact_file(tmp_path):
    task = _task()
    computed = compute_vegetation_change([[1.0]], [[3.0]], [[2.0]], [[3.0]], artifact_dir=tmp_path)
    aoi, scenes, metrics = _complete_evidence(task)
    (tmp_path / "ndvi_after.png").unlink()
    artifacts = {
        "ndvi_before": "ndvi_before.png",
        "ndvi_after": "ndvi_after.png",
        "ndvi_change": "ndvi_change.png",
    }
    result = verify_change(
        computed,
        artifacts=artifacts,
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=scenes,
        execution_mode="REAL_STAC_LOCAL_FIXTURE",
        metrics=metrics,
    )
    assert result["code"] == "artifact_missing"


def test_verifier_rejects_zero_valid_analysis(tmp_path):
    task = _task()
    computed = compute_vegetation_change([[1.0]], [[3.0]], [[2.0]], [[3.0]], artifact_dir=tmp_path)
    empty = replace(computed, valid_mask=np.array([[False]]), delta=np.array([[np.nan]]))
    aoi, scenes, metrics = _complete_evidence(task)
    result = verify_change(
        empty,
        artifacts=empty.artifacts,
        artifact_root=tmp_path,
        task=task,
        aoi_evidence=aoi,
        scene_evidence=scenes,
        execution_mode="REAL_STAC_LOCAL_FIXTURE",
        metrics=metrics,
    )
    assert result["code"] == "empty_valid_pixels"
