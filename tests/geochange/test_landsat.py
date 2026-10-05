from datetime import UTC, datetime

import httpx
import numpy as np
import pytest

from geochange.aoi import load_trusted_aoi
from geochange.landsat import (
    DiscoveryLimits,
    MonthlyPeriod,
    PeriodPair,
    PreparationFailure,
    PreparedPeriodDataset,
    PreparedPeriodPair,
    TargetGrid,
    apply_radiometry,
    default_period_pair,
    discover_landsat,
    qa_pixel_valid_mask,
    qa_radsat_valid_mask,
    select_landsat_scenes,
    validate_mtl_metadata,
    validate_mtl_text,
)


def _asset_features() -> dict:
    aoi = load_trusted_aoi()
    return {
        "type": "Feature",
        "id": "LC08_L2SP_123039_20230727_02_T1",
        "collection": "landsat-c2-l2",
        "geometry": aoi.geometry,
        "properties": {
            "datetime": "2023-07-27T03:12:00Z",
            "eo:cloud_cover": 15.97,
            "platform": "landsat-8",
            "landsat:processing_level": "L2SP",
            "proj:epsg": 32649,
        },
        "assets": {
            "red": {"href": "https://landsateuwest.blob.core.windows.net/a_SR_B4.TIF"},
            "nir08": {"href": "https://landsateuwest.blob.core.windows.net/a_SR_B5.TIF"},
            "qa_pixel": {"href": "https://landsateuwest.blob.core.windows.net/a_QA_PIXEL.TIF"},
            "qa_radsat": {"href": "https://landsateuwest.blob.core.windows.net/a_QA_RADSAT.TIF"},
            "qa_aerosol": {
                "href": "https://landsateuwest.blob.core.windows.net/a_SR_QA_AEROSOL.TIF"
            },
        },
    }


class _SearchClient:
    def __init__(self, feature: dict):
        self.feature = feature
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs):
        self.calls.append(kwargs["json"])
        feature = dict(self.feature)
        feature["id"] = (
            "LC08_L2SP_123039_20240729_02_T1" if len(self.calls) == 2 else self.feature["id"]
        )
        feature["properties"] = dict(self.feature["properties"])
        feature["properties"]["datetime"] = (
            "2024-07-29T03:12:00Z" if len(self.calls) == 2 else "2023-07-27T03:12:00Z"
        )
        request = httpx.Request("POST", url)
        return httpx.Response(
            200, json={"type": "FeatureCollection", "features": [feature]}, request=request
        )

    def close(self):
        return None


def test_trusted_jianghan_snapshot_is_immutable_and_bounded():
    aoi = load_trusted_aoi()
    assert aoi.aoi_id == "jianghan_district_420103"
    assert aoi.admin_code == "420103"
    assert aoi.source_version == 21
    assert aoi.crs == "EPSG:4326"
    assert 20_000_000 < aoi.area_m2 < 50_000_000
    assert aoi.source_hash == "11633b0f428a884c414c90dd4c7c94d14f7ed903fa954aae666eae35984f8c25"


def test_monthly_periods_are_utc_calendar_windows_and_non_overlapping():
    assert default_period_pair().period_a.end_utc == datetime(2023, 8, 1, tzinfo=UTC)
    with pytest.raises(ValueError):
        MonthlyPeriod(
            period_id="a",
            start_utc=datetime(2023, 7, 2, tzinfo=UTC),
            end_utc=datetime(2023, 8, 1, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="must not overlap"):
        PeriodPair(
            period_a=default_period_pair().period_a,
            period_b=MonthlyPeriod(
                period_id="b",
                start_utc=datetime(2023, 7, 1, tzinfo=UTC),
                end_utc=datetime(2023, 8, 1, tzinfo=UTC),
            ),
        )


def test_discovery_uses_polygon_and_validates_physical_asset_mapping():
    client = _SearchClient(_asset_features())
    report = discover_landsat(load_trusted_aoi(), default_period_pair(), client=client)
    assert all("intersects" in query and "bbox" not in query for query in client.calls)
    assert report.candidates["a"][0].asset_key_map["SR_B4"] == "red"
    assert report.candidates["a"][0].asset_key_map["SR_B5"] == "nir08"
    assert report.candidates["a"][0].assets["qa_pixel"].physical_band == "QA_PIXEL"
    assert report.candidates["a"][0].mtl_href is None
    selected = select_landsat_scenes(report)
    assert len(selected.period_a) == len(selected.period_b) == 1


def test_discovery_rejects_untrusted_asset_host_and_missing_band():
    feature = _asset_features()
    feature["assets"]["red"] = {"href": "https://evil.example/red_SR_B4.TIF"}
    with pytest.raises(PreparationFailure) as error:
        discover_landsat(load_trusted_aoi(), default_period_pair(), client=_SearchClient(feature))
    assert error.value.code == "no_candidate"


def test_radiometry_preserves_known_negative_values_but_masks_invalid_dn():
    reflectance, valid, out_of_nominal = apply_radiometry(
        np.array([[0, 1, 40000, 65456]], dtype=np.uint16),
        scale=0.0000275,
        offset=-0.2,
        nodata=0,
    )
    assert valid.tolist() == [[False, True, True, False]]
    assert reflectance[0, 1] < 0
    assert out_of_nominal.tolist() == [[False, True, False, False]]


def test_mtl_calibration_values_are_validated_and_raw_payload_is_not_retained():
    values = validate_mtl_metadata(
        {
            "LANDSAT_METADATA_FILE": {
                "LEVEL2_SURFACE_REFLECTANCE_PARAMETERS": {
                    "REFLECTANCE_MULT_BAND_4": "2.75e-05",
                    "REFLECTANCE_ADD_BAND_4": "-0.2",
                    "REFLECTANCE_MULT_BAND_5": "2.75e-05",
                    "REFLECTANCE_ADD_BAND_5": "-0.2",
                }
            }
        }
    )
    assert values == {
        "REFLECTANCE_MULT_BAND_4": 2.75e-05,
        "REFLECTANCE_ADD_BAND_4": -0.2,
        "REFLECTANCE_MULT_BAND_5": 2.75e-05,
        "REFLECTANCE_ADD_BAND_5": -0.2,
    }
    text = "\n".join(f"{key} = {value}" for key, value in values.items())
    assert validate_mtl_text(text) == values
    with pytest.raises(PreparationFailure):
        validate_mtl_text(text.replace("-0.2", "bad", 1))


@pytest.mark.parametrize("bit", [0, 1, 2, 3, 4, 5])
def test_qa_pixel_masks_required_quality_bits(bit):
    assert not qa_pixel_valid_mask(np.array([[1 << bit]], dtype=np.uint16))[0, 0]


def test_qa_pixel_retains_water_and_radsat_masks_red_nir_terrain():
    assert qa_pixel_valid_mask(np.array([[1 << 7]], dtype=np.uint16))[0, 0]
    assert qa_radsat_valid_mask(np.array([[0]], dtype=np.uint16))[0, 0]
    assert not qa_radsat_valid_mask(np.array([[1 << 3]], dtype=np.uint16))[0, 0]
    assert not qa_radsat_valid_mask(np.array([[1 << 4]], dtype=np.uint16))[0, 0]
    assert not qa_radsat_valid_mask(np.array([[1 << 11]], dtype=np.uint16))[0, 0]


def test_prepared_pair_contract_requires_pair_wide_grid_and_common_mask():
    grid = TargetGrid(
        crs="EPSG:32649", transform=(30, 0, 0, 0, -30, 0), width=2, height=1, resolution_m=30
    )
    period = default_period_pair()
    red = np.array([[0.2, np.nan]], dtype=np.float32)
    nir = np.array([[0.4, np.nan]], dtype=np.float32)
    valid = np.array([[True, False]])
    aoi = np.array([[True, True]])
    index = np.array([[0, -1]], dtype=np.int16)
    datasets = [
        PreparedPeriodDataset(
            period_id=period_id,
            requested_period=requested,
            red_reflectance=red,
            nir_reflectance=nir,
            preparation_valid_mask=valid,
            aoi_mask=aoi,
            source_scene_index=index,
            target_grid=grid,
            coverage={
                "aoi_rasterized_pixels": 2,
                "preparation_valid_pixels": 1,
                "preparation_coverage_pct": 50,
                "scene_count": 1,
            },
            provenance={},
        )
        for period_id, requested in (("a", period.period_a), ("b", period.period_b))
    ]
    pair = PreparedPeriodPair(
        period_a=datasets[0],
        period_b=datasets[1],
        common_preparation_valid_mask=np.array([[True, False]]),
        pair_grid=grid,
        hard_limits_applied=DiscoveryLimits().model_dump(),
        operational_metrics={"bytes_observed": 0},
        best_effort_warnings=(),
    )
    assert pair.common_preparation_valid_mask.tolist() == [[True, False]]
