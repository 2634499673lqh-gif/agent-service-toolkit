import numpy as np
import pytest

from geochange.landsat import (
    Coverage,
    MonthlyPeriod,
    PreparedPeriodDataset,
    PreparedPeriodPair,
    TargetGrid,
)
from geochange.landsat_ndvi import compute_landsat_ndvi_product, verify_landsat_ndvi_product


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
    product = compute_landsat_ndvi_product(
        pair, final_ndvi_coverage_gate=20, final_common_coverage_gate=20
    )
    assert product.final_common_comparison_mask.sum() == 1
    assert np.isnan(product.ndvi_before[0, 1])
    assert not product.final_ndvi_valid_mask_a[0, 1]
    assert product.metrics["final_common_comparison_pixels"] == 1
    assert verify_landsat_ndvi_product(product, pair)["status"] == "passed"


def test_landsat_ndvi_rejects_coverage_gate():
    with pytest.raises(ValueError, match="insufficient_ndvi_coverage"):
        compute_landsat_ndvi_product(_pair())
