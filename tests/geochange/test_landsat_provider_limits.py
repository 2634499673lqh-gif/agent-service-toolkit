"""Focused provider-boundary and limit regression tests for Landsat preparation."""

from __future__ import annotations

from typing import Any

import httpx
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
    monkeypatch.setattr(landsat, "_fetch_mtl_values", lambda *_args, **_kwargs: None)
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
                    json={
                        "href": "https://landsateuwest.blob.core.windows.net/asset.tif?sig=t"
                    },
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
