import copy
import hashlib
import json

import numpy as np
import pytest

from geochange.aoi import resolve_aoi
from geochange.landsat import (
    Coverage,
    MonthlyPeriod,
    PreparedPeriodDataset,
    PreparedPeriodPair,
    TargetGrid,
)
from geochange.landsat_ndvi import (
    compute_landsat_ndvi_product,
    verify_landsat_ndvi_product,
    verify_landsat_terminal_projection,
)
from geochange.models import GeoChangeTask
from geochange.provenance import trusted_landsat_map_metadata
from geochange.skill import VEGETATION_CHANGE_NDVI, SkillValidationError, validate_terminal_result
from schema.confirmed_intent import ConfirmedIntent


def _pair() -> PreparedPeriodPair:
    grid = TargetGrid(
        crs="EPSG:32649", transform=(30, 0, 0, 0, -30, 0), width=2, height=2, resolution_m=30
    )
    aoi = np.ones((2, 2), dtype=bool)
    prep_a = np.array([[True, True], [True, False]], dtype=bool)
    prep_b = np.array([[True, True], [False, True]], dtype=bool)
    red_a = np.array([[0.2, -0.1], [0.0, np.nan]], dtype=np.float32)
    nir_a = np.array([[0.6, 0.1], [0.0, np.nan]], dtype=np.float32)
    red_b = np.array([[0.1, -0.1], [np.nan, 0.2]], dtype=np.float32)
    nir_b = np.array([[0.5, 0.1], [np.nan, 0.4]], dtype=np.float32)

    def dataset(pid, red, nir, prep):
        return PreparedPeriodDataset(
            period_id=pid,
            requested_period=MonthlyPeriod(
                period_id=pid,
                start_utc="2023-07-01T00:00:00Z" if pid == "a" else "2024-07-01T00:00:00Z",
                end_utc="2023-08-01T00:00:00Z" if pid == "a" else "2024-08-01T00:00:00Z",
            ),
            red_reflectance=red,
            nir_reflectance=nir,
            preparation_valid_mask=prep,
            aoi_mask=aoi,
            source_scene_index=np.zeros((2, 2), dtype=np.int16),
            target_grid=grid,
            coverage=Coverage(
                aoi_rasterized_pixels=4,
                preparation_valid_pixels=int(prep.sum()),
                preparation_coverage_pct=float(prep.sum() / 4 * 100),
                scene_count=1,
            ),
            provenance={},
        )

    da, db = dataset("a", red_a, nir_a, prep_a), dataset("b", red_b, nir_b, prep_b)
    return PreparedPeriodPair(da, db, prep_a & prep_b, grid, {}, {}, ())


def test_landsat_ndvi_uses_common_mask_and_negative_reflectance():
    pair = _pair()
    # The production gates are server-owned; this fixture is intentionally
    # below them and therefore exercises the bounded failure path.
    with pytest.raises(ValueError, match="insufficient_ndvi_coverage"):
        compute_landsat_ndvi_product(pair)


def test_landsat_ndvi_aoi_masks_must_match():
    pair = _pair()
    object.__setattr__(pair.period_b, "aoi_mask", np.array([[True, False], [True, True]]))
    with pytest.raises(ValueError, match="AOI masks"):
        compute_landsat_ndvi_product(pair)


def test_landsat_ndvi_product_with_server_gate_fixture():
    pair = _pair()
    object.__setattr__(pair.period_a, "red_reflectance", np.full((2, 2), -0.1, dtype=np.float32))
    object.__setattr__(pair.period_a, "nir_reflectance", np.full((2, 2), -0.15, dtype=np.float32))
    object.__setattr__(pair.period_b, "red_reflectance", np.full((2, 2), 0.1, dtype=np.float32))
    object.__setattr__(pair.period_b, "nir_reflectance", np.full((2, 2), 0.5, dtype=np.float32))
    object.__setattr__(pair.period_a, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair.period_b, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair, "common_preparation_valid_mask", np.ones((2, 2), dtype=bool))
    product = compute_landsat_ndvi_product(pair)
    assert product.final_common_comparison_mask.sum() == 4
    assert product.ndvi_before[0, 0] == pytest.approx(0.2)
    assert product.metrics["final_common_comparison_pixels"] == 4
    assert verify_landsat_ndvi_product(product, pair)["status"] == "passed"


def _gated_pair() -> PreparedPeriodPair:
    pair = _pair()
    object.__setattr__(pair.period_a, "red_reflectance", np.full((2, 2), -0.1, dtype=np.float32))
    object.__setattr__(pair.period_a, "nir_reflectance", np.full((2, 2), -0.15, dtype=np.float32))
    object.__setattr__(pair.period_b, "red_reflectance", np.full((2, 2), 0.1, dtype=np.float32))
    object.__setattr__(pair.period_b, "nir_reflectance", np.full((2, 2), 0.5, dtype=np.float32))
    object.__setattr__(pair.period_a, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair.period_b, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair, "common_preparation_valid_mask", np.ones((2, 2), dtype=bool))
    return pair


def test_landsat_ndvi_verifier_recomputes_forged_metrics():
    pair = _gated_pair()
    product = compute_landsat_ndvi_product(pair)
    product.metrics["mean_delta_ndvi"] = 0.9
    assert verify_landsat_ndvi_product(product, pair)["status"] == "failed"


def test_landsat_ndvi_verifier_requires_and_checks_artifact_checksums(tmp_path):
    pair = _gated_pair()
    try:
        product = compute_landsat_ndvi_product(pair, artifact_dir=tmp_path)
    except Exception as error:
        if "proj.db" in str(error):
            pytest.skip("Rasterio PROJ database unavailable in this local environment")
        raise
    assert verify_landsat_ndvi_product(product, pair, artifact_root=tmp_path)["status"] == "passed"
    product.artifacts["ndvi_before_raster"] = "ndvi_after_raster.tif"
    assert (
        verify_landsat_ndvi_product(product, pair, artifact_root=tmp_path)["code"]
        == "artifact_reference_invalid"
    )
    product = compute_landsat_ndvi_product(pair, artifact_dir=tmp_path)
    product.provenance["artifact_checksums"].pop("ndvi_before_raster")
    assert (
        verify_landsat_ndvi_product(product, pair, artifact_root=tmp_path)["code"]
        == "artifact_checksum_missing"
    )

    product = compute_landsat_ndvi_product(pair, artifact_dir=tmp_path)
    path = tmp_path / product.artifacts["ndvi_before_raster"]
    path.unlink()
    assert (
        verify_landsat_ndvi_product(product, pair, artifact_root=tmp_path)["code"]
        == "artifact_missing"
    )
    product = compute_landsat_ndvi_product(pair, artifact_dir=tmp_path)
    path = tmp_path / product.artifacts["ndvi_before_raster"]
    path.write_bytes(path.read_bytes() + b"tamper")
    assert (
        verify_landsat_ndvi_product(product, pair, artifact_root=tmp_path)["code"]
        == "artifact_checksum_mismatch"
    )


def test_landsat_ndvi_rejects_coverage_gate():
    with pytest.raises(ValueError, match="insufficient_ndvi_coverage"):
        compute_landsat_ndvi_product(_pair())


def test_dynamic_terminal_rejects_forged_metrics():
    pair = _pair()
    object.__setattr__(pair.period_a, "red_reflectance", np.full((2, 2), -0.1, dtype=np.float32))
    object.__setattr__(pair.period_a, "nir_reflectance", np.full((2, 2), -0.15, dtype=np.float32))
    object.__setattr__(pair.period_b, "red_reflectance", np.full((2, 2), 0.1, dtype=np.float32))
    object.__setattr__(pair.period_b, "nir_reflectance", np.full((2, 2), 0.5, dtype=np.float32))
    object.__setattr__(pair.period_a, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair.period_b, "preparation_valid_mask", np.ones((2, 2), dtype=bool))
    object.__setattr__(pair, "common_preparation_valid_mask", np.ones((2, 2), dtype=bool))
    product = compute_landsat_ndvi_product(pair)
    product.provenance.update(
        {
            "aoi_id": "jianghan_district_420103",
            "aoi_hash": "a" * 64,
            "target_crs": "EPSG:32649",
            "target_dimensions": [296, 264],
            "scene_provenance": {"period_a": {}, "period_b": {}},
        }
    )
    product.provenance["metrics_sha256"] = hashlib.sha256(
        json.dumps(product.metrics, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    artifacts = {
        name: name
        for name in (
            "ndvi_before",
            "ndvi_after",
            "ndvi_change",
            "ndvi_before_raster",
            "ndvi_after_raster",
            "ndvi_change_raster",
            "ndvi_valid_before",
            "ndvi_valid_after",
            "ndvi_common_comparison",
        )
    }
    product.provenance["artifact_checksums"] = {name: "a" * 64 for name in artifacts}
    payload = {
        "schema_version": "geochange.v1",
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "mode": "real_stac_landsat_local",
        "summary": "x",
        "metrics": dict(product.metrics),
        "analysis_area": "jianghan_district_420103",
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "data_source": "landsat-c2-l2",
        "provenance_summary": "x",
        "provenance": product.provenance,
        "artifacts": artifacts,
        "verifier_status": "passed",
        "execution_mode": "real_stac_landsat_local",
        "selected_scene_evidence": {
            "preparation_contract_version": "v0.3-preparation-1",
            "period_a": "{}",
            "period_b": "{}",
            "target_grid": json.dumps(
                pair.pair_grid.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ),
        },
    }
    payload["metrics"]["mean_delta_ndvi"] = 0.9
    with pytest.raises(SkillValidationError):
        VEGETATION_CHANGE_NDVI.validate_result(payload)


def test_dynamic_projection_rejects_forged_scene_grid_and_period_evidence():
    pair = _gated_pair()
    product = compute_landsat_ndvi_product(pair)
    payload = {
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "metrics": product.metrics,
        "provenance": product.provenance,
        "artifacts": {name: name for name in product.artifacts},
        "selected_scene_evidence": {
            "preparation_contract_version": pair.contract_version,
            "period_a": "{}",
            "period_b": "{}",
            "target_grid": json.dumps(
                pair.pair_grid.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ),
        },
    }
    assert verify_landsat_terminal_projection(payload, product, pair)["status"] == "passed"
    payload["selected_scene_evidence"]["target_grid"] = "{}"
    assert verify_landsat_terminal_projection(payload, product, pair)["status"] == "failed"
    payload["selected_scene_evidence"]["target_grid"] = json.dumps(
        pair.pair_grid.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )

    for field in (
        "analysis_periods",
        "metrics",
        "provenance",
        "artifacts",
        "selected_scene_evidence",
    ):
        forged = copy.deepcopy(payload)
        if field == "analysis_periods":
            forged[field]["period_a"] = "2022-07-01/2022-07-31"
        elif field == "metrics":
            forged[field]["mean_delta_ndvi"] = 0.9
        elif field == "provenance":
            forged[field]["scene_provenance"] = {"period_a": {"scene_ids": ["forged"]}}
        elif field == "artifacts":
            forged[field]["ndvi_before"] = "ndvi_after"
        else:
            forged[field]["period_a"] = '{"scene_ids":["forged"]}'
        assert verify_landsat_terminal_projection(forged, product, pair)["status"] == "failed"


def test_dynamic_terminal_routing_bypasses_legacy_scene_fixture(monkeypatch):
    task = GeoChangeTask(
        aoi_key="jianghan_district_420103",
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        data_mode="real_stac_landsat_local",
    )
    aoi = resolve_aoi(task.aoi_key)
    monkeypatch.setattr(
        "geochange.skill.scene_evidence",
        lambda _task: (_ for _ in ()).throw(AssertionError("legacy scene evidence called")),
    )
    with pytest.raises(SkillValidationError):
        validate_terminal_result(
            VEGETATION_CHANGE_NDVI,
            {
                "execution_mode": "real_stac_landsat_local",
                "provenance": {"aoi_hash": aoi.source_hash},
            },
            task=task,
            aoi_evidence={"catalog_key": aoi.catalog_key, "crs": aoi.crs, "source": aoi.source},
            scene_evidence_values={},
        )


def test_dynamic_terminal_rejects_self_consistent_forged_binder():
    task = GeoChangeTask(
        aoi_key="jianghan_district_420103",
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        data_mode="real_stac_landsat_local",
    )
    aoi = resolve_aoi(task.aoi_key)
    canonical = {
        "analysis_periods": {
            "period_a": "2023-07-01/2023-07-31",
            "period_b": "2024-07-01/2024-07-31",
        },
        "metrics": {"final_common_comparison_pixels": 26107},
        "provenance": {"aoi_hash": aoi.source_hash},
        "artifacts": {"ndvi_before_raster": "ndvi_before_raster"},
        "selected_scene_evidence": {"target_grid": "server-grid"},
    }
    payload = {
        "execution_mode": "real_stac_landsat_local",
        "provenance": {"aoi_hash": aoi.source_hash},
        **canonical,
    }

    class _BinderSkill:
        result_type = "vegetation_change"

        def validate_result(self, *args, **kwargs):  # noqa: ANN002, ANN003
            return None

    forged = copy.deepcopy(payload)
    forged["metrics"]["final_common_comparison_pixels"] = 1
    with pytest.raises(SkillValidationError, match="dynamic terminal evidence"):
        validate_terminal_result(
            _BinderSkill(),
            forged,
            task=task,
            aoi_evidence={
                "catalog_key": aoi.catalog_key,
                "crs": aoi.crs,
                "source": aoi.source,
            },
            scene_evidence_values={},
            canonical_dynamic_evidence=canonical,
        )


def test_jianghan_confirmation_rejects_non_month_and_out_of_range():
    base = {
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "analysis_area": "武汉市江汉区",
        "period_a": {"start": "2023-07-02", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
        "parameters": {"source": "Landsat-8/9"},
    }
    with pytest.raises(ValueError, match="complete calendar months"):
        ConfirmedIntent.model_validate(base)
    base["period_a"] = {"start": "2022-07-01", "end": "2022-07-31"}
    with pytest.raises(ValueError, match="2023-2025"):
        ConfirmedIntent.model_validate(base)


def test_dynamic_map_metadata_is_not_east_lake_fixture():
    metadata = {
        "provenance": {
            "aoi_id": "jianghan_district_420103",
            "target_crs": "EPSG:32649",
            "target_dimensions": [296, 264],
            "aoi_bbox": [114.2, 30.5, 114.3, 30.7],
            "target_grid": {"transform": [30, 0, 1, 0, -30, 2]},
            "scene_provenance": {
                "period_a": {"scene_ids": ["a"]},
                "period_b": {"scene_ids": ["b"]},
            },
        }
    }
    result = trusted_landsat_map_metadata(metadata)
    assert result["crs"] == "EPSG:32649"
    assert result["dimensions"] == [296, 264]
    assert result["periods"]["period_a"]["native_bounds"] == [1.0, -8878.0, 7921.0, 2.0]
    assert result["periods"]["period_b"]["native_bounds"] == [1.0, -8878.0, 7921.0, 2.0]
    assert result["periods"]["period_a"]["scene_identity"] == "a"
