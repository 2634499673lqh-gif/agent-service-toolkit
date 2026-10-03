"""Prepare a bounded, provenance-bound Sentinel-2 NDBI evidence fixture.

The script is preparation-time only.  It reuses the validated 3B B08/SCL and
footprint crop, fetches only the matching B11 COG tile through HTTP Range
requests, and never downloads a complete COG or enables the NDBI runtime.
"""

import argparse
import hashlib
import io
import json
import shutil
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
import tifffile

NDWI_ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-ndwi-v5"
WINDOW_ORIGIN_10M = (4120, 1240)
WINDOW_SIZE_10M = (24, 24)
WINDOW_ORIGIN_20M = (2060, 620)
WINDOW_SIZE_20M = (12, 12)
MAX_DOWNLOAD_BYTES = 20_000_000
MAX_TILE_BYTES = 4_000_000


def _read_range(url: str, start: int, size: int, state: dict[str, int]) -> bytes:
    request = urllib.request.Request(url, headers={"Range": f"bytes={start}-{start + size - 1}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 206:
            raise ValueError("source must honor bounded Range reads")
        data = response.read(size + 1)
    state["downloaded"] += len(data)
    if len(data) > size or state["downloaded"] > MAX_DOWNLOAD_BYTES:
        raise ValueError("preparation download bound exceeded")
    return data


def _read_b11_crop(
    url: str, origin: tuple[int, int], size: tuple[int, int], state: dict[str, int]
) -> tuple[np.ndarray, dict[str, Any]]:
    header = _read_range(url, 0, 16_384, state)
    with tifffile.TiffFile(io.BytesIO(header)) as tiff:
        page = tiff.pages[0]
        if (
            not page.is_tiled
            or list(page.shape) != [5490, 5490]
            or page.dtype != np.dtype("uint16")
        ):
            raise ValueError("unexpected B11 source layout")
        if tuple(page.tags["ModelPixelScaleTag"].value[:2]) != (20.0, 20.0):
            raise ValueError("unexpected B11 source resolution")
        tiepoint = tuple(page.tags["ModelTiepointTag"].value)
        if tiepoint[3:5] != (199980.0, 3400020.0):
            raise ValueError("unexpected B11 source origin")
        tile_width = int(page.tags["TileWidth"].value)
        tile_height = int(page.tags["TileLength"].value)
        col, row = origin
        tiles_across = (page.shape[1] + tile_width - 1) // tile_width
        tile_index = (row // tile_height) * tiles_across + col // tile_width
        count = int(page.databytecounts[tile_index])
        if count > MAX_TILE_BYTES:
            raise ValueError("source tile exceeds preparation bound")
        tile_data = _read_range(url, int(page.dataoffsets[tile_index]), count, state)
        decoded = page.decode(tile_data, tile_index)[0]
        if decoded is None:
            raise ValueError("source tile is empty")
        height, width = size
        crop = decoded[
            0,
            row % tile_height : row % tile_height + height,
            col % tile_width : col % tile_width + width,
            0,
        ]
        if list(crop.shape) != list(size):
            raise ValueError("B11 crop dimensions are invalid")
        return crop, {
            "source_shape": list(page.shape),
            "source_transform": [20, 0, 199980, 0, -20, 3400020],
            "tile_shape": [tile_height, tile_width],
            "source_resolution_m": 20,
        }


def _head(url: str) -> tuple[str, int]:
    with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as response:
        return response.headers["ETag"].strip('"'), int(response.headers["Content-Length"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    state = {"downloaded": 0}
    manifest: dict[str, Any] = {
        "fixture_version": "geochange.real-sentinel2-ndbi.v1",
        "format": "NPZ",
        "max_fixture_bytes": 1_000_000,
        "preparation": {
            "method": "bounded COG Range reads for B11; B08/SCL/coverage copied from immutable NDWI v5 crop",
            "target_grid": {
                "crs": "EPSG:32650",
                "transform": [10, 0, 199980, 0, -10, 3400020],
                "resolution_m": 10,
                "dimensions": list(WINDOW_SIZE_10M),
            },
            "b11_source_grid": {
                "crs": "EPSG:32650",
                "transform": [20, 0, 199980, 0, -20, 3400020],
                "resolution_m": 20,
                "dimensions": [5490, 5490],
            },
            "b11_to_target_resampling": "nearest_neighbour_2x2_block",
            "b11_window_pixel_origin": list(WINDOW_ORIGIN_20M),
            "b11_window_size_pixels": list(WINDOW_SIZE_20M),
            "target_window_pixel_origin": list(WINDOW_ORIGIN_10M),
            "target_window_size_pixels": list(WINDOW_SIZE_10M),
        },
        "source": {
            "access_basis": "public Earth Search STAC snapshots and public AWS Sentinel COG HTTP Range GET",
            "accessed_on": "2026-10-02",
            "attribution": "Contains modified Copernicus Sentinel data 2023, 2024. Sentinel-2 Cloud-Optimized GeoTIFFs accessed from https://registry.opendata.aws/sentinel-2-l2a-cogs/.",
            "dataset_url": "https://registry.opendata.aws/sentinel-2-l2a-cogs/",
            "license_url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
            "license_metadata": "Earth Search collection license metadata is preserved; no permissive license is inferred.",
        },
        "scenes": {},
    }
    for period in ("period_a", "period_b"):
        source_manifest = json.loads((NDWI_ROOT / "manifest.json").read_text(encoding="utf-8"))
        source_scene = source_manifest["scenes"][period]
        item_path = NDWI_ROOT / f"{period}.item.json"
        item = json.loads(item_path.read_text(encoding="utf-8"))
        b11_asset = item["assets"]["swir16"]
        b11_url = b11_asset["href"]
        etag, content_length = _head(b11_url)
        b11, source_layout = _read_b11_crop(b11_url, WINDOW_ORIGIN_20M, WINDOW_SIZE_20M, state)
        source_band = b11_asset["raster:bands"][0]
        expected = {
            "band": "B11",
            "href": b11_url,
            "etag": etag,
            "content_length": content_length,
            **source_layout,
            "nodata": source_band.get("nodata"),
            "scale": source_band.get("scale"),
            "offset": source_band.get("offset"),
            "window_pixel_origin": list(WINDOW_ORIGIN_20M),
            "window_size_pixels": list(WINDOW_SIZE_20M),
            "dtype": str(b11.dtype),
            "crop_sha256": hashlib.sha256(b11.tobytes()).hexdigest(),
        }
        # Keep the 3B B08, SCL and scene-footprint arrays byte-identical while
        # binding their source identities into this independent manifest.
        with np.load(NDWI_ROOT / f"{period}.npz", allow_pickle=False) as bundle:
            arrays = {name: bundle[name] for name in ("nir", "scl", "coverage")}
        arrays["swir16_native_20m"] = b11
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        data = buffer.getvalue()
        manifest["scenes"][period] = {
            "item_id": source_scene["item_id"],
            "acquisition_datetime": source_scene["acquisition_datetime"],
            "acquisition_date": source_scene["acquisition_date"],
            "collection": source_scene["collection"],
            "aoi_bounds_wgs84": source_scene["aoi_bounds_wgs84"],
            "cloud_cover": source_scene["cloud_cover"],
            "crs": "EPSG:32650",
            "target_dimensions": list(WINDOW_SIZE_10M),
            "b11_native_dimensions": list(WINDOW_SIZE_20M),
            "target_transform": [10, 0, 199980, 0, -10, 3400020],
            "b11_asset_identity": expected,
            "b08_asset_identity": source_scene["asset_identity"]["nir"],
            "scl_asset_identity": source_scene["asset_identity"]["scl"],
            "coverage_pixels": source_scene["coverage_pixels"],
            "fixture_file": f"{period}.npz",
            "fixture_sha256": hashlib.sha256(data).hexdigest(),
            "stac_item_sha256": source_scene["stac_item_sha256"],
            "reused_v5_fixture_manifest_sha256": hashlib.sha256(
                (NDWI_ROOT / "manifest.json").read_bytes()
            ).hexdigest(),
        }
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{period}.npz").write_bytes(data)
        shutil.copy2(item_path, output / f"{period}.item.json")
    manifest["preparation"]["downloaded_bytes"] = state["downloaded"]
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    (output / "manifest.json").write_bytes(manifest_bytes)
    (output / "manifest.sha256").write_text(
        hashlib.sha256(manifest_bytes).hexdigest() + "\n", encoding="ascii"
    )
    print(f"Prepared NDBI fixture; downloaded {state['downloaded']} bytes")


if __name__ == "__main__":
    main()
