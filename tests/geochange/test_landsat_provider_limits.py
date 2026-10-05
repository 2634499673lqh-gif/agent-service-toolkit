"""Focused provider-boundary and limit regression tests for Landsat preparation."""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest

import geochange.landsat as landsat
from geochange.aoi import load_trusted_aoi
from geochange.landsat import (
    DiscoveryLimits,
    PreparationFailure,
    TargetGrid,
    default_period_pair,
    discover_landsat,
    qa_aerosol_diagnostics,
    select_landsat_scenes,
)


def _feature(item_id: str, timestamp: str) -> dict[str, Any]:
    aoi = load_trusted_aoi()
    host = "landsateuwest.blob.core.windows.net"
    return {
        "type": "Feature",
        "id": item_id,
        "collection": "landsat-c2-l2",
        "geometry": aoi.geometry,
        "properties": {
            "datetime": timestamp,
            "eo:cloud_cover": 20.0,
            "platform": "landsat-8",
            "landsat:processing_level": "L2SP",
            "proj:epsg": 32649,
            "proj:transform": [30, 0, 0, 0, -30, 30],
            "proj:shape": [1, 1],
        },
        "assets": {
            "red": {
                "href": f"https://{host}/{item_id}_SR_B4.TIF",
                "raster:bands": [
                    {"data_type": "uint16", "scale": 2.75e-5, "offset": -0.2, "nodata": 0}
                ],
            },
            "nir08": {
                "href": f"https://{host}/{item_id}_SR_B5.TIF",
                "raster:bands": [
                    {"data_type": "uint16", "scale": 2.75e-5, "offset": -0.2, "nodata": 0}
                ],
            },
            "qa_pixel": {"href": f"https://{host}/{item_id}_QA_PIXEL.TIF"},
            "qa_radsat": {"href": f"https://{host}/{item_id}_QA_RADSAT.TIF"},
            "qa_aerosol": {"href": f"https://{host}/{item_id}_SR_QA_AEROSOL.TIF"},
        },
    }


class _RetrySearchClient:
    """A deterministic STAC fake that can return provider status failures."""

    def __init__(self, statuses: list[int]) -> None:
        self.statuses = list(statuses)
        self.calls = 0

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        del kwargs
        self.calls += 1
        status = self.statuses.pop(0) if self.statuses else 200
        request = httpx.Request("POST", url)
        if status != 200:
            return httpx.Response(status, request=request)
        period_id = "a" if self.calls % 2 else "b"
        year = "2023" if period_id == "a" else "2024"
        feature = _feature(
            f"LC08_RETRY_{year}",
            f"{year}-07-27T03:12:00Z",
        )
        return httpx.Response(
            200,
            json={"type": "FeatureCollection", "features": [feature]},
            request=request,
        )

    def close(self) -> None:
        return None


def _scene_for_grid(item_id: str, *, width: int = 1, height: int = 1):
    feature = _feature(item_id, "2023-07-27T03:12:00Z")
    feature["properties"]["proj:shape"] = [height, width]
    feature["properties"]["proj:transform"] = [30, 0, 0, 0, -30, 30]
    return landsat._scene_from_feature(feature, load_trusted_aoi())


def _fake_metadata(*, width: int, height: int, transform=(30, 0, 0, 0, -30, 30)):
    return {
        "dtype": "uint16",
        "crs": "EPSG:32649",
        "transform": transform,
        "nodata": 0,
        "scale": 2.75e-5,
        "offset": -0.2,
        "width": width,
        "height": height,
        "count": 1,
    }


def _matching_mtl_values() -> dict[str, float]:
    return {
        "REFLECTANCE_MULT_BAND_4": 2.75e-5,
        "REFLECTANCE_ADD_BAND_4": -0.2,
        "REFLECTANCE_MULT_BAND_5": 2.75e-5,
        "REFLECTANCE_ADD_BAND_5": -0.2,
    }


def test_storage_boundary_rejects_arbitrary_blob_accounts() -> None:
    assert landsat._provider_host_allowed(
        "https://landsateuwest.blob.core.windows.net/path/SR_B4.TIF"
    )
    assert not landsat._provider_host_allowed(
        "https://untrusted-account.blob.core.windows.net/path/SR_B4.TIF"
    )
    assert not landsat._provider_host_allowed(
        "http://landsateuwest.blob.core.windows.net/path/SR_B4.TIF"
    )


def test_scene_selection_is_bounded_to_three_even_for_full_footprint_candidates() -> None:
    report = landsat.DiscoveryReport(
        aoi_id=load_trusted_aoi().aoi_id,
        candidates={
            "a": [
                landsat._scene_from_feature(
                    _feature(f"LC08_{index}", "2023-07-27T03:12:00Z"), load_trusted_aoi()
                )
                for index in range(5)
            ],
            "b": [
                landsat._scene_from_feature(
                    _feature(f"LC09_{index}", "2024-07-27T03:12:00Z"), load_trusted_aoi()
                )
                for index in range(5)
            ],
        },
        query_bbox=tuple(load_trusted_aoi().bbox),
        hard_limits_applied={"max_selected_scenes": 3},
    )
    selected = select_landsat_scenes(report)
    assert len(selected.period_a) == len(selected.period_b) == 3


@pytest.mark.parametrize("first_status", [429, 500, 502])
def test_stac_provider_retry_recovers_from_bounded_transient_status(first_status: int) -> None:
    client = _RetrySearchClient([first_status, 200, 200])
    report = discover_landsat(
        load_trusted_aoi(),
        default_period_pair(),
        limits=DiscoveryLimits(request_deadline_seconds=5),
        client=client,
    )
    assert report.candidates["a"]
    assert report.candidates["b"]
    assert client.calls == 3


def test_stac_provider_retry_exhaustion_is_bounded_and_sanitized() -> None:
    client = _RetrySearchClient([429, 429, 429, 429, 429])
    with pytest.raises(PreparationFailure) as error:
        discover_landsat(
            load_trusted_aoi(),
            default_period_pair(),
            limits=DiscoveryLimits(request_deadline_seconds=5),
            client=client,
        )
    assert error.value.code == "provider_unavailable"
    assert error.value.retryable
    assert client.calls <= 3


def test_shared_deadline_is_consumed_by_discovery_then_preparation(monkeypatch) -> None:
    clock = [100.0]

    monkeypatch.setattr(landsat.time, "monotonic", lambda: clock[0])

    class SlowSearchClient(_RetrySearchClient):
        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            clock[0] += 0.4
            return super().post(url, **kwargs)

    deadline = clock[0] + 1.0
    report = discover_landsat(
        load_trusted_aoi(),
        default_period_pair(),
        limits=DiscoveryLimits(request_deadline_seconds=1.0),
        client=SlowSearchClient([]),
        deadline_monotonic=deadline,
    )
    assert report.candidates["a"]
    clock[0] = deadline + 0.01
    with pytest.raises(PreparationFailure, match="timeout"):
        landsat.prepare_landsat_pair(
            load_trusted_aoi(),
            default_period_pair(),
            select_landsat_scenes(report),
            limits=DiscoveryLimits(request_deadline_seconds=1.0),
            client=object(),
            deadline_monotonic=deadline,
        )


def test_operation_wrapper_reuses_deadline_after_discovery(monkeypatch) -> None:
    clock = [100.0]
    deadlines: list[float] = []
    monkeypatch.setattr(landsat.time, "monotonic", lambda: clock[0])

    def fake_discover(*_args, deadline_monotonic, **_kwargs):
        deadlines.append(deadline_monotonic)
        clock[0] += 0.5
        return object()

    def fake_select(_report):
        return object()

    def fake_prepare(*_args, deadline_monotonic, **_kwargs):
        deadlines.append(deadline_monotonic)
        return object()

    monkeypatch.setattr(landsat, "discover_landsat", fake_discover)
    monkeypatch.setattr(landsat, "select_landsat_scenes", fake_select)
    monkeypatch.setattr(landsat, "prepare_landsat_pair", fake_prepare)
    limits = DiscoveryLimits(request_deadline_seconds=1.0)
    landsat.prepare_landsat_operation(
        load_trusted_aoi(), default_period_pair(), limits, client=object()
    )
    assert deadlines[0] == deadlines[1]
    assert deadlines[0] - clock[0] == pytest.approx(0.5)


def test_array_gdal_budget_is_explicitly_per_run_not_process_global() -> None:
    evidence = landsat._hard_limits_evidence(DiscoveryLimits())
    assert evidence["array_cache_bytes"] == 96 * 1024 * 1024
    assert evidence["gdal_cache_bytes"] == 32 * 1024 * 1024
    assert evidence["array_plus_gdal_cache_bytes"] == 128 * 1024 * 1024
    assert evidence["array_gdal_budget_scope_per_run"] is True
    assert evidence["process_global_array_gdal_cap"] is False


@pytest.mark.parametrize(
    ("field", "value", "stage"),
    [
        ("transform", (31, 0, 0, 0, -30, 30), "expected_transform"),
        ("width", 2, "expected_shape"),
        ("crs", "EPSG:32650", "expected_crs"),
    ],
)
def test_expected_stac_metadata_mismatch_fails_closed(monkeypatch, field, value, stage) -> None:
    aoi = load_trusted_aoi()
    scene = _scene_for_grid("LC08_EXPECTED", width=1, height=1)
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, 1), bool))

    def fake_read(href, *_args, **_kwargs):
        metadata = _fake_metadata(width=1, height=1)
        role = "red" if "SR_B4" in href else "nir08" if "SR_B5" in href else "qa_aerosol"
        if field == "transform" and role == "red":
            metadata["transform"] = value
        elif field == "width" and role == "red":
            metadata["width"] = value
        elif field == "crs" and role == "red":
            metadata["crs"] = value
        return landsat.np.array([[100]], dtype=landsat.np.uint16), metadata, 1

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    monkeypatch.setattr(
        landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: _matching_mtl_values()
    )
    with pytest.raises(PreparationFailure) as error:
        landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )
    assert error.value.stage == stage


@pytest.mark.parametrize(
    ("actual_nodata", "accepted"),
    [(None, False), (0, True), (1, False)],
)
def test_expected_stac_nodata_must_bind_to_opened_cog(actual_nodata, accepted) -> None:
    asset = landsat.AssetIdentity(
        key="red",
        physical_band="SR_B4",
        identity_hash="0" * 64,
        href="https://example.test/red.tif",
        nodata=0,
    )
    metadata = {"nodata": actual_nodata}
    if accepted:
        landsat._validate_expected_asset_metadata(asset, metadata)
    else:
        with pytest.raises(PreparationFailure, match="invalid_raster_metadata"):
            landsat._validate_expected_asset_metadata(asset, metadata)


def test_missing_mtl_fails_closed(monkeypatch) -> None:
    aoi = load_trusted_aoi()
    scene = _scene_for_grid("LC08_MTL_MISSING")
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, 1), bool))
    monkeypatch.setattr(landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        landsat,
        "_read_asset",
        lambda *_args, **_kwargs: (
            landsat.np.array([[100]], dtype=landsat.np.uint16),
            _fake_metadata(width=1, height=1),
            1,
        ),
    )
    with pytest.raises(PreparationFailure) as error:
        landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )
    assert error.value.stage == "mtl_required"


@pytest.mark.parametrize(
    "mtl_values",
    [
        {"REFLECTANCE_MULT_BAND_4": 2.75e-5},
        {
            "REFLECTANCE_MULT_BAND_4": 2.75e-5,
            "REFLECTANCE_ADD_BAND_4": -0.2,
            "REFLECTANCE_MULT_BAND_5": "malformed",
            "REFLECTANCE_ADD_BAND_5": -0.2,
        },
    ],
)
def test_malformed_or_incomplete_mtl_fails_closed(mtl_values) -> None:
    with pytest.raises(PreparationFailure, match="invalid_radiometry"):
        landsat.validate_mtl_metadata({"LEVEL2_SURFACE_REFLECTANCE_PARAMETERS": mtl_values})


@pytest.mark.parametrize("physical_band", ["QA_PIXEL", "SR_QA_AEROSOL"])
def test_packed_qa_fill_nodata_tag_is_optional(physical_band: str) -> None:
    asset = landsat.AssetIdentity(
        key=physical_band.lower(),
        physical_band=physical_band,
        identity_hash="0" * 64,
        href="https://example.test/qa.tif",
        nodata=1,
    )
    landsat._validate_expected_asset_metadata(asset, {"nodata": None})


@pytest.mark.parametrize("physical_band", ["QA_PIXEL", "SR_QA_AEROSOL"])
def test_packed_qa_contradictory_nodata_tag_fails_closed(physical_band: str) -> None:
    asset = landsat.AssetIdentity(
        key=physical_band.lower(),
        physical_band=physical_band,
        identity_hash="0" * 64,
        href="https://example.test/qa.tif",
        nodata=1,
    )
    with pytest.raises(PreparationFailure, match="invalid_raster_metadata"):
        landsat._validate_expected_asset_metadata(asset, {"nodata": 0})


def test_aerosol_expected_projection_mismatch_fails_closed(monkeypatch) -> None:
    aoi = load_trusted_aoi()
    scene = _scene_for_grid("LC08_AEROSOL_EXPECTED")
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, 1), bool))

    def fake_read(href, *_args, **_kwargs):
        metadata = _fake_metadata(width=1, height=1)
        if "SR_QA_AEROSOL" in href:
            metadata["transform"] = (30, 0, 5, 0, -30, 30)
        return landsat.np.array([[100]], dtype=landsat.np.uint16), metadata, 1

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    monkeypatch.setattr(
        landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: _matching_mtl_values()
    )
    with pytest.raises(PreparationFailure, match="grid_alignment_failed") as error:
        landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )
    assert error.value.stage == "expected_transform"


def test_raw_cog_unit_scale_requires_independent_mtl_evidence(monkeypatch) -> None:
    aoi = load_trusted_aoi()
    scene = _scene_for_grid("LC08_RAW_SCALE")
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, 1), bool))
    monkeypatch.setattr(landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: None)

    def fake_read(_href, *_args, **_kwargs):
        metadata = _fake_metadata(width=1, height=1)
        metadata["scale"] = 1.0
        metadata["offset"] = 0.0
        value = 0 if "QA_" in _href else 100
        return landsat.np.array([[value]], dtype=landsat.np.uint16), metadata, 1

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    with pytest.raises(PreparationFailure) as error:
        landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )
    assert error.value.stage == "mtl_required"


@pytest.mark.parametrize(
    ("stac_scale", "stac_offset", "mtl_values", "accepted", "stage"),
    [
        (
            2.75e-5,
            -0.2,
            {
                "REFLECTANCE_MULT_BAND_4": 2.75e-5,
                "REFLECTANCE_ADD_BAND_4": -0.2,
                "REFLECTANCE_MULT_BAND_5": 2.75e-5,
                "REFLECTANCE_ADD_BAND_5": -0.2,
            },
            True,
            None,
        ),
        (2.75e-5, -0.2, None, False, "mtl_required"),
        (1.0, 0.0, None, False, "mtl_required"),
        (
            2.75e-5,
            -0.2,
            {
                "REFLECTANCE_MULT_BAND_4": 1.0,
                "REFLECTANCE_ADD_BAND_4": 0.0,
                "REFLECTANCE_MULT_BAND_5": 1.0,
                "REFLECTANCE_ADD_BAND_5": 0.0,
            },
            False,
            "mtl_validation",
        ),
    ],
)
def test_raw_cog_requires_physical_stac_and_matching_mtl(
    monkeypatch, stac_scale, stac_offset, mtl_values, accepted, stage
) -> None:
    aoi = load_trusted_aoi()
    scene = _scene_for_grid("LC08_RAW_CALIBRATION")
    assets = dict(scene.assets)
    for role in ("red", "nir08"):
        assets[role] = assets[role].model_copy(update={"scale": stac_scale, "offset": stac_offset})
    scene = scene.model_copy(update={"assets": assets})
    grid = TargetGrid(
        crs="EPSG:32649", transform=(30, 0, 0, 0, -30, 30), width=1, height=1, resolution_m=30
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, 1), bool))
    monkeypatch.setattr(landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: mtl_values)

    def fake_read(_href, *_args, **_kwargs):
        metadata = _fake_metadata(width=1, height=1)
        metadata["scale"] = 1.0
        metadata["offset"] = 0.0
        value = 0 if "QA_" in _href else 100
        return landsat.np.array([[value]], dtype=landsat.np.uint16), metadata, 1

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    if accepted:
        dataset, _bytes, _warnings = landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )
        assert dataset.coverage.preparation_valid_pixels == 1
    else:
        with pytest.raises(PreparationFailure) as error:
            landsat._prepare_period(
                default_period_pair().period_a,
                [scene],
                aoi,
                grid,
                DiscoveryLimits(),
                client=object(),
                deadline_monotonic=landsat.time.monotonic() + 30,
            )
        assert error.value.stage == stage


def _prepare_with_scene_masks(monkeypatch, masks: list[list[bool]]):
    width = len(masks[0])
    aoi = load_trusted_aoi()
    scenes = [
        _scene_for_grid(f"LC08_MASK_{index}", width=width, height=1) for index in range(len(masks))
    ]
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=width,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(
        landsat, "_rasterize_aoi", lambda *_args: landsat.np.ones((1, width), dtype=bool)
    )
    monkeypatch.setattr(
        landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: _matching_mtl_values()
    )

    def fake_read(href, *_args, **_kwargs):
        scene_index = next(index for index in range(len(masks)) if f"MASK_{index}" in href)
        role = "qa_pixel" if "QA_PIXEL" in href else "qa_radsat" if "QA_RADSAT" in href else "other"
        values = landsat.np.full((1, width), 100, dtype=landsat.np.uint16)
        if role == "qa_pixel":
            values = landsat.np.where(
                landsat.np.asarray(masks[scene_index])[None, :], 0, 1 << 3
            ).astype(landsat.np.uint16)
        metadata = _fake_metadata(width=width, height=1)
        return values, metadata, 1

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    return landsat._prepare_period(
        default_period_pair().period_a,
        scenes,
        aoi,
        grid,
        DiscoveryLimits(),
        client=object(),
        deadline_monotonic=landsat.time.monotonic() + 30,
    )[0]


def test_priority_fill_uses_complementary_second_scene_and_same_source_pairs(monkeypatch) -> None:
    dataset = _prepare_with_scene_masks(
        monkeypatch,
        [
            [True, True, True, True, True, False, False, False, False, False],
            [False, False, False, False, False, True, True, True, True, True],
        ],
    )
    assert dataset.coverage.scene_count == 2
    assert dataset.coverage.preparation_valid_pixels == 10
    assert dataset.source_scene_index.tolist() == [[0, 0, 0, 0, 0, 1, 1, 1, 1, 1]]
    assert np.all(np.isfinite(dataset.red_reflectance) == dataset.preparation_valid_mask)
    assert np.all(np.isfinite(dataset.nir_reflectance) == dataset.preparation_valid_mask)


def test_high_quality_first_scene_stops_after_one(monkeypatch) -> None:
    dataset = _prepare_with_scene_masks(monkeypatch, [[True] * 10, [True] * 10])
    assert dataset.coverage.scene_count == 1
    assert dataset.source_scene_index.tolist() == [[0] * 10]


def test_insufficient_preparation_coverage_fails_closed(monkeypatch) -> None:
    with pytest.raises(PreparationFailure) as error:
        _prepare_with_scene_masks(monkeypatch, [[False] * 10])
    assert error.value.code == "insufficient_preparation_coverage"


def test_mtl_sas_query_is_rejected_before_signing() -> None:
    class Client:
        calls = 0

        def get(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("signing must not be attempted")

    client = Client()
    with pytest.raises(PreparationFailure) as error:
        landsat._fetch_mtl_values(
            "https://landsateuwest.blob.core.windows.net/scene_MTL.txt?sig=secret",
            client=client,
        )
    assert error.value.code == "security_rejected"
    assert client.calls == 0
    assert "secret" not in str(error.value.as_dict())


def test_mtl_untrusted_signed_host_is_rejected_without_leakage() -> None:
    class Client:
        def get(self, url: str, **_kwargs):
            request = httpx.Request("GET", url)
            if url == landsat.SAS_SIGN_ENDPOINT:
                return httpx.Response(
                    200,
                    json={"href": "https://evil.example/scene_MTL.txt?sig=secret"},
                    request=request,
                )
            raise AssertionError("untrusted signed host must not be read")

    with pytest.raises(PreparationFailure) as error:
        landsat._fetch_mtl_values(
            "https://landsateuwest.blob.core.windows.net/scene_MTL.txt",
            client=Client(),
        )
    assert error.value.code == "security_rejected"
    diagnostics = str(error.value.as_dict())
    assert "secret" not in diagnostics
    assert "evil.example" not in diagnostics


def test_mtl_redirect_is_rejected_before_content_read() -> None:
    class Client:
        def get(self, url: str, **_kwargs):
            request = httpx.Request("GET", url)
            if url == landsat.SAS_SIGN_ENDPOINT:
                return httpx.Response(
                    200,
                    json={
                        "href": "https://landsateuwest.blob.core.windows.net/scene_MTL.txt?sig=secret"
                    },
                    request=request,
                )
            response = httpx.Response(
                302,
                headers={"location": "https://evil.example/redirect"},
                request=request,
            )
            response.history = [httpx.Response(301, request=request)]
            return response

    with pytest.raises(PreparationFailure) as error:
        landsat._fetch_mtl_values(
            "https://landsateuwest.blob.core.windows.net/scene_MTL.txt",
            client=Client(),
        )
    assert error.value.code == "security_rejected"


def test_deadline_helper_fails_closed_after_shared_deadline() -> None:
    with pytest.raises(PreparationFailure, match="timeout"):
        landsat._remaining_seconds(landsat.time.monotonic() - 1)


def test_aerosol_diagnostics_are_bounded_and_interpolation_is_not_nodata() -> None:
    # Fill, valid retrieval, interpolated retrieval, and high aerosol level.
    values = landsat.np.array([[1, 2, 32, 14]], dtype=landsat.np.uint16)
    diagnostics = qa_aerosol_diagnostics(values)
    assert diagnostics["pixels"] == 4
    assert diagnostics["fill_pixels"] == 1
    assert diagnostics["valid_retrieval_pixels"] == 2
    assert diagnostics["interpolated_pixels"] == 1
    assert diagnostics["aerosol_level_low_medium_high_pixels"] == 1


def test_child_cleanup_terminates_then_kills_and_closes_ipc() -> None:
    class Process:
        def __init__(self) -> None:
            self.alive = True
            self.actions: list[str] = []

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.actions.append("terminate")

        def kill(self) -> None:
            self.actions.append("kill")
            self.alive = False

        def join(self, *, timeout: float) -> None:
            del timeout
            self.actions.append("join")

    class Pipe:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    process = Process()
    pipe = Pipe()
    landsat._cleanup_child(process, pipe)
    assert process.actions == ["terminate", "join", "kill", "join"]
    assert pipe.closed


def test_prepare_period_passes_runtime_window_limit_to_every_asset(monkeypatch) -> None:
    aoi = load_trusted_aoi()
    scene = landsat._scene_from_feature(_feature("LC08_LIMIT", "2023-07-27T03:12:00Z"), aoi)
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: landsat.np.array([[True]]))
    calls: list[int] = []

    def fake_read(_href, _grid, *, max_window_pixels, **_kwargs):
        calls.append(max_window_pixels)
        is_qa = "QA_" in _href or "QA_PIXEL" in _href
        dtype = landsat.np.uint16
        return (
            landsat.np.array([[0 if is_qa else 100]], dtype=dtype),
            {
                "dtype": "uint16",
                "crs": "EPSG:32649",
                "transform": (30, 0, 0, 0, -30, 30),
                "nodata": 0,
                "scale": 2.75e-5,
                "offset": -0.2,
                "width": 1,
                "height": 1,
                "count": 1,
            },
            1,
        )

    monkeypatch.setattr(landsat, "_read_asset", fake_read)
    monkeypatch.setattr(
        landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: _matching_mtl_values()
    )
    limits = DiscoveryLimits(max_window_pixels=7, array_cache_bytes=128 * 1024 * 1024)
    landsat._prepare_period(
        default_period_pair().period_a,
        [scene],
        aoi,
        grid,
        limits,
        client=object(),
        deadline_monotonic=landsat.time.monotonic() + 30,
    )
    assert calls and set(calls) == {7}


def test_prepare_period_rejects_memory_before_array_allocation(monkeypatch) -> None:
    aoi = load_trusted_aoi()
    scene = landsat._scene_from_feature(_feature("LC08_MEMORY", "2023-07-27T03:12:00Z"), aoi)
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=100,
        height=100,
        resolution_m=30,
    )
    rasterized = landsat.np.ones((100, 100), dtype=bool)
    monkeypatch.setattr(landsat, "_rasterize_aoi", lambda *_args: rasterized)
    with pytest.raises(PreparationFailure, match="read_budget_exceeded"):
        landsat._prepare_period(
            default_period_pair().period_a,
            [scene],
            aoi,
            grid,
            DiscoveryLimits(array_cache_bytes=1),
            client=object(),
            deadline_monotonic=landsat.time.monotonic() + 30,
        )


def test_asset_retry_resigns_sas_after_transient_probe_failure(monkeypatch) -> None:
    class Pipe:
        def __init__(self, inbox=None) -> None:
            self.inbox = inbox if inbox is not None else {}
            self.closed = False

        def send(self, value) -> None:
            self.inbox["value"] = value

        def poll(self, _timeout) -> bool:
            return "value" in self.inbox

        def recv(self):
            return self.inbox["value"]

        def close(self) -> None:
            self.closed = True

    class Context:
        def Pipe(self, *, duplex=False):
            del duplex
            inbox = {}
            return Pipe(inbox), Pipe(inbox)

        def Process(self, *, target, args):
            class Process:
                def __init__(self) -> None:
                    self.alive = False

                def start(self) -> None:
                    target(*args)

                def is_alive(self) -> bool:
                    return self.alive

                def terminate(self) -> None:
                    self.alive = False

                def kill(self) -> None:
                    self.alive = False

                def join(self, *, timeout: float) -> None:
                    del timeout

            return Process()

    class Client:
        def __init__(self) -> None:
            self.sign_calls = 0
            self.probe_urls: list[str] = []

        def get(self, url: str, **kwargs):
            del kwargs
            request = httpx.Request("GET", url)
            if url == landsat.SAS_SIGN_ENDPOINT:
                self.sign_calls += 1
                return httpx.Response(
                    200,
                    json={
                        "href": (
                            "https://landsateuwest.blob.core.windows.net/asset.tif?"
                            f"sig=token-{self.sign_calls}"
                        )
                    },
                    request=request,
                )
            self.probe_urls.append(url)
            status = 429 if len(self.probe_urls) == 1 else 206
            return httpx.Response(status, content=b"range", request=request)

    def fake_worker(connection, *_args) -> None:
        connection.send(
            {
                "array": landsat.np.array([[100]], dtype=landsat.np.uint16),
                "dtype": "uint16",
                "crs": "EPSG:32649",
                "transform": (30, 0, 0, 0, -30, 30),
                "nodata": 0,
                "scale": 2.75e-5,
                "offset": -0.2,
                "width": 1,
                "height": 1,
                "count": 1,
            }
        )

    monkeypatch.setattr(landsat.mp, "get_context", lambda _name: Context())
    monkeypatch.setattr(landsat, "_rasterio_worker", fake_worker)
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    client = Client()
    array, _metadata, _bytes = landsat._read_asset(
        "https://landsateuwest.blob.core.windows.net/asset.tif",
        grid,
        resampling="nearest",
        client=client,
        deadline_seconds=5,
    )
    assert array.tolist() == [[100]]
    assert client.sign_calls == 2
    assert len(client.probe_urls) == 2
    assert client.probe_urls[0] != client.probe_urls[1]


def test_asset_timeout_runs_supervised_child_cleanup(monkeypatch) -> None:
    class Pipe:
        def __init__(self) -> None:
            self.closed = False

        def poll(self, _timeout) -> bool:
            return False

        def close(self) -> None:
            self.closed = True

    parent = Pipe()
    child = Pipe()

    class Process:
        def __init__(self) -> None:
            self.alive = True
            self.actions: list[str] = []

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self.alive

        def terminate(self) -> None:
            self.actions.append("terminate")

        def kill(self) -> None:
            self.actions.append("kill")
            self.alive = False

        def join(self, *, timeout: float) -> None:
            del timeout
            self.actions.append("join")

    process = Process()

    class Context:
        def Pipe(self, *, duplex=False):
            del duplex
            return parent, child

        def Process(self, **_kwargs):
            return process

    class Client:
        def get(self, url: str, **kwargs):
            del kwargs
            request = httpx.Request("GET", url)
            if url == landsat.SAS_SIGN_ENDPOINT:
                return httpx.Response(
                    200,
                    json={"href": "https://landsateuwest.blob.core.windows.net/asset.tif?sig=t"},
                    request=request,
                )
            return httpx.Response(206, content=b"range", request=request)

    monkeypatch.setattr(landsat.mp, "get_context", lambda _name: Context())
    grid = TargetGrid(
        crs="EPSG:32649",
        transform=(30, 0, 0, 0, -30, 30),
        width=1,
        height=1,
        resolution_m=30,
    )
    with pytest.raises(PreparationFailure, match="timeout"):
        landsat._read_asset(
            "https://landsateuwest.blob.core.windows.net/asset.tif",
            grid,
            resampling="nearest",
            client=Client(),
            deadline_seconds=5,
        )
    assert process.actions == ["terminate", "join", "kill", "join"]
    assert parent.closed
