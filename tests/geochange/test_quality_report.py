import hashlib
import json

import pytest

from geochange.quality import build_landsat_quality_report


def _metadata():
    metrics = {
        "aoi_rasterized_pixels": 100,
        "preparation_valid_pixels_period_a": 90,
        "preparation_valid_pixels_period_b": 80,
        "final_ndvi_valid_pixels_period_a": 75,
        "final_ndvi_valid_pixels_period_b": 70,
        "final_common_comparison_pixels": 65,
        "preparation_coverage_period_a_pct": 90.0,
        "preparation_coverage_period_b_pct": 80.0,
        "final_ndvi_coverage_period_a_pct": 75.0,
        "final_ndvi_coverage_period_b_pct": 70.0,
        "final_common_comparison_coverage_pct": 65.0,
        "mean_ndvi_period_a": 0.4,
        "mean_ndvi_period_b": 0.41,
        "mean_delta_ndvi": 0.01,
    }
    digest = hashlib.sha256(
        json.dumps(metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    base = {
        "verifier_status": "passed",
        "execution_mode": "real_stac_landsat_local",
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "metrics": metrics,
        "selected_scene_evidence": {
            "period_a_item_id": "A",
            "period_a_date": "2023-07-01",
            "period_b_item_id": "B",
            "period_b_date": "2024-07-01",
            "target_grid": json.dumps({"crs": "EPSG:32649", "transform": [30, 0, 1, 0, -30, 2]}),
        },
        "provenance": {
            "aoi_id": "jianghan_district_420103",
            "aoi_hash": "a" * 64,
            "metrics_sha256": digest,
            "target_crs": "EPSG:32649",
            "provider": "microsoft-planetary-computer",
            "sensor": "Landsat 8/9 Collection 2 Level 2",
            "target_grid": {
                "resolution_m": 30,
                "crs": "EPSG:32649",
                "transform": [30, 0, 1, 0, -30, 2],
            },
            "scene_provenance": {
                "period_a": {"scene_ids": ["A"], "acquisition_dates": ["2023-07-01"]},
                "period_b": {"scene_ids": ["B"], "acquisition_dates": ["2024-07-01"]},
            },
            "target_dimensions": [10, 10],
        },
    }
    artifact_names = {
        "ndvi_before": "ndvi_before",
        "ndvi_after": "ndvi_after",
        "ndvi_change": "ndvi_change",
        "ndvi_before_raster": "ndvi_before_raster",
        "ndvi_after_raster": "ndvi_after_raster",
        "ndvi_change_raster": "ndvi_change_raster",
        "ndvi_valid_before": "ndvi_valid_before",
        "ndvi_valid_after": "ndvi_valid_after",
        "ndvi_common_comparison": "ndvi_common_comparison",
    }
    base["artifact_references"] = artifact_names
    base["provenance"]["artifact_checksums"] = {name: "a" * 64 for name in artifact_names}
    base["canonical_evidence"] = {
        key: base[key]
        for key in ("analysis_periods", "metrics", "provenance", "selected_scene_evidence")
    }
    base["canonical_evidence"]["artifacts"] = artifact_names
    return base


def test_quality_report_distinguishes_coverage_and_verification():
    metadata = _metadata()
    report = build_landsat_quality_report(
        metadata, canonical_evidence=metadata["canonical_evidence"]
    )
    assert report["coverage"]["common_comparison"]["gate_pct"] == 50.0
    assert report["verification"]["external_accuracy"] == "not assessed"
    assert report["metrics_digest"] == metadata["provenance"]["metrics_sha256"]
    assert report["target_transform"] == [30.0, 0.0, 1.0, 0.0, -30.0, 2.0]


@pytest.mark.parametrize(
    "change", [{"verifier_status": "failed"}, {"provenance": {"aoi_id": "wuhan_east_lake"}}]
)
def test_quality_report_never_fabricates_unavailable(change):
    metadata = _metadata()
    metadata.update(change)
    with pytest.raises(ValueError):
        build_landsat_quality_report(
            metadata, canonical_evidence=metadata.get("canonical_evidence")
        )


@pytest.mark.parametrize(
    ("a_policy", "a_warnings", "b_policy", "b_warnings", "expected"),
    [
        ("qa", [], "qa", [], "verified"),
        ("qa", ["cloud"], "qa", ["shadow"], "verified"),
        ("qa", [], "qa", None, "partial"),
        ("qa", None, "qa", [], "partial"),
        (None, [], "qa", [], "partial"),
        ("qa-a", [], "qa-b", [], "verified"),
        ("qa", [], "qa", None, "partial"),
        (None, None, "qa", None, "unavailable"),
    ],
)
def test_quality_report_preserves_per_period_qa_availability(
    a_policy, a_warnings, b_policy, b_warnings, expected
):
    metadata = _metadata()
    scenes = metadata["provenance"]["scene_provenance"]
    scenes["period_a"]["qa_policy"] = a_policy
    scenes["period_b"]["qa_policy"] = b_policy
    if a_warnings is not None:
        scenes["period_a"]["best_effort_warnings"] = a_warnings
    if b_warnings is not None:
        scenes["period_b"]["best_effort_warnings"] = b_warnings
    metadata["canonical_evidence"]["provenance"] = metadata["provenance"]
    report = build_landsat_quality_report(
        metadata, canonical_evidence=metadata["canonical_evidence"]
    )
    assert report["qa_status"] == expected
    assert report["qa_periods"]["a"]["policy"] == a_policy
    assert report["qa_periods"]["a"]["warnings_available"] is (a_warnings is not None)
    assert report["qa_periods"]["b"]["warnings_available"] is (b_warnings is not None)


def test_top_level_warnings_cannot_fill_missing_period_evidence():
    metadata = _metadata()
    scenes = metadata["provenance"]["scene_provenance"]
    scenes["period_a"].update({"qa_policy": "qa", "best_effort_warnings": []})
    scenes["period_b"]["qa_policy"] = "qa"
    metadata["provenance"]["best_effort_warnings"] = []
    metadata["canonical_evidence"]["provenance"] = metadata["provenance"]
    report = build_landsat_quality_report(
        metadata, canonical_evidence=metadata["canonical_evidence"]
    )
    assert report["qa_status"] == "partial"
    assert report["qa_warnings_available"] is False
    assert report["qa_periods"]["b"]["warnings_available"] is False
    assert report["qa_warnings"] is None


def test_contradictory_top_level_warning_projection_fails_closed():
    metadata = _metadata()
    scenes = metadata["provenance"]["scene_provenance"]
    scenes["period_a"].update({"qa_policy": "qa", "best_effort_warnings": ["cloud"]})
    scenes["period_b"].update({"qa_policy": "qa", "best_effort_warnings": []})
    metadata["provenance"]["best_effort_warnings"] = []
    metadata["canonical_evidence"]["provenance"] = metadata["provenance"]
    with pytest.raises(ValueError):
        build_landsat_quality_report(metadata, canonical_evidence=metadata["canonical_evidence"])


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("provenance", "target_crs"), None),
        (("provenance", "target_grid", "resolution_m"), None),
        (("artifact_references", "ndvi_before_raster"), ""),
        (("provenance", "artifact_checksums", "ndvi_before_raster"), "bad"),
        (("provenance", "scene_provenance", "period_a", "acquisition_dates"), ["not-a-date"]),
        (
            ("analysis_periods", "period_a"),
            "2022-07-01/2022-08-01",
        ),
        (
            ("analysis_periods", "period_b"),
            "2023-07-15/2023-08-15",
        ),
    ],
)
def test_quality_report_rejects_incomplete_canonical_identity(path, value):
    metadata = _metadata()
    target = metadata
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    metadata["canonical_evidence"] = {
        key: metadata[key]
        for key in ("analysis_periods", "metrics", "provenance", "selected_scene_evidence")
    }
    metadata["canonical_evidence"]["artifacts"] = metadata["artifact_references"]
    with pytest.raises(ValueError):
        build_landsat_quality_report(metadata, canonical_evidence=metadata["canonical_evidence"])


def test_quality_report_rejects_malformed_warning_values():
    metadata = _metadata()
    metadata["provenance"]["scene_provenance"]["period_a"]["best_effort_warnings"] = [1]
    metadata["canonical_evidence"]["provenance"] = metadata["provenance"]
    with pytest.raises(ValueError):
        build_landsat_quality_report(metadata, canonical_evidence=metadata["canonical_evidence"])


@pytest.mark.parametrize(
    "day,accepted",
    [
        ("2023-07-01", True),
        ("2023-07-31", True),
        ("2023-06-30", False),
        ("2023-08-01", False),
        ("2023-07-32", False),
        ("2023-7-1", False),
    ],
)
def test_quality_uses_inclusive_canonical_period_end(day, accepted):
    metadata = _metadata()
    metadata["analysis_periods"]["period_a"] = "2023-07-01/2023-07-31"
    metadata["selected_scene_evidence"]["period_a_date"] = day
    metadata["provenance"]["scene_provenance"]["period_a"]["acquisition_dates"] = [day]
    if accepted:
        assert (
            build_landsat_quality_report(
                metadata, canonical_evidence=metadata["canonical_evidence"]
            )["status"]
            == "available"
        )
    else:
        with pytest.raises(ValueError, match="scene acquisition date"):
            build_landsat_quality_report(
                metadata, canonical_evidence=metadata["canonical_evidence"]
            )


@pytest.mark.parametrize("artifact", list(_metadata()["artifact_references"]))
def test_quality_rejects_missing_preview_or_scientific_artifact(artifact):
    metadata = _metadata()
    del metadata["artifact_references"][artifact]
    with pytest.raises(ValueError, match="artifact references"):
        build_landsat_quality_report(metadata, canonical_evidence=metadata["canonical_evidence"])
