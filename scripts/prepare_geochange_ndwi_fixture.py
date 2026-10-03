"""Prepare a bounded, provenance-bound Sentinel-2 NDWI fixture.

This is a preparation-time script.  It performs bounded COG Range reads and
does not add runtime dependencies or enable the water Skill.
"""

import argparse
import hashlib
import io
import json
import shutil
import urllib.request
from pathlib import Path

import numpy as np
import tifffile
from pyproj import Transformer

ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-v1"
MAX_DOWNLOAD_BYTES = 20_000_000
WINDOW_ORIGIN = (4120, 1240)
WINDOW_SIZE = (24, 24)
SCL_ORIGIN = (2060, 620)
SCL_SIZE = (12, 12)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    downloaded = 0

    def read(url: str, start: int, size: int) -> bytes:
        nonlocal downloaded
        request = urllib.request.Request(
            url, headers={"Range": f"bytes={start}-{start + size - 1}"}
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            if response.status != 206:
                raise ValueError("source must honor bounded Range reads")
            data = response.read(size + 1)
        downloaded += len(data)
        if len(data) > size or downloaded > MAX_DOWNLOAD_BYTES:
            raise ValueError("preparation download bound exceeded")
        return data

    prepared: dict[str, bytes] = {}
    manifest: dict[str, object] = {
        "fixture_version": "geochange.real-sentinel2-ndwi.v1",
        "format": "NPZ",
        "max_fixture_bytes": 1_000_000,
        "preparation": {
            "method": "bounded COG Range reads; native B03/B08 and native SCL crop, no resampling",
            "window_pixel_origin": list(WINDOW_ORIGIN),
            "window_size_pixels": list(WINDOW_SIZE),
            "scl_pixel_origin": list(SCL_ORIGIN),
            "scl_size_pixels": list(SCL_SIZE),
            "target_grid": {
                "crs": "EPSG:32650",
                "resolution_m": 10,
                "dimensions": list(WINDOW_SIZE),
            },
        },
        "scenes": {},
        "source": {
            "access_basis": "public Earth Search STAC snapshots and public AWS Sentinel COG HTTP Range GET",
            "accessed_on": "2026-10-02",
            "attribution": "Contains modified Copernicus Sentinel data 2023, 2024. Sentinel-2 Cloud-Optimized GeoTIFFs accessed from https://registry.opendata.aws/sentinel-2-l2a-cogs/.",
            "dataset_url": "https://registry.opendata.aws/sentinel-2-l2a-cogs/",
            "license_url": "https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice",
            "license_metadata": "Earth Search collection license metadata is preserved; no permissive license is inferred.",
        },
    }

    for period in ("period_a", "period_b"):
        item = json.loads((ROOT / f"{period}.item.json").read_text(encoding="utf-8"))
        props = item["properties"]
        assets = item["assets"]
        bands: dict[str, np.ndarray] = {}
        identities: dict[str, dict[str, object]] = {}
        for output_key, asset_key, origin, size in (
            ("green", "green", WINDOW_ORIGIN, WINDOW_SIZE),
            ("nir", "nir", WINDOW_ORIGIN, WINDOW_SIZE),
            ("scl", "scl", SCL_ORIGIN, SCL_SIZE),
        ):
            asset = assets[asset_key]
            url = asset["href"]
            with urllib.request.urlopen(
                urllib.request.Request(url, method="HEAD"), timeout=30
            ) as response:
                etag = response.headers["ETag"].strip('"')
                content_length = int(response.headers["Content-Length"])
            expected = asset["raster:bands"][0]
            header = read(url, 0, 16_384)
            with tifffile.TiffFile(io.BytesIO(header)) as tiff:
                page = tiff.pages[0]
                if list(page.shape) != asset["proj:shape"]:
                    raise ValueError(f"{period} {asset_key} source shape changed")
                transform = list(asset["proj:transform"])
                tiepoint = list(page.tags["ModelTiepointTag"].value)
                if tiepoint[3] != transform[2] or tiepoint[4] != transform[5]:
                    raise ValueError(f"{period} {asset_key} source transform changed")
                if page.tags["ModelPixelScaleTag"].value[0] != transform[0]:
                    raise ValueError(f"{period} {asset_key} source resolution changed")
                tile_width = page.tags["TileWidth"].value
                tile_height = page.tags["TileLength"].value
                col, row = origin
                tile_index = (row // tile_height) * (
                    (page.shape[1] + tile_width - 1) // tile_width
                ) + col // tile_width
                count = page.databytecounts[tile_index]
                if count > 4_000_000:
                    raise ValueError("source tile exceeds preparation bound")
                data = read(url, page.dataoffsets[tile_index], count)
                decoded = page.decode(data, tile_index)[0]
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
                    raise ValueError(f"{period} {asset_key} crop is invalid")
                bands[output_key] = crop
                identities[output_key] = {
                    "band": {"green": "B03", "nir": "B08", "scl": "SCL"}[output_key],
                    "href": url,
                    "etag": etag,
                    "content_length": content_length,
                    "source_shape": asset["proj:shape"],
                    "source_transform": transform,
                    "source_resolution_m": transform[0],
                    "nodata": expected.get("nodata"),
                    "scale": expected.get("scale"),
                    "offset": expected.get("offset"),
                    "window_pixel_origin": list(origin),
                    "window_size_pixels": list(size),
                    "dtype": str(crop.dtype),
                }
        # Preserve scene-footprint coverage explicitly; period B's footprint does
        # not cover the entire common AOI crop and those pixels must be masked.
        projected = [
            Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True).transform(*point)
            for point in item["geometry"]["coordinates"][0]
        ]

        def inside(x: float, y: float) -> bool:
            hit = False
            for (x1, y1), (x2, y2) in zip(projected, projected[1:]):
                if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
                    hit = not hit
            return hit

        col0, row0 = WINDOW_ORIGIN
        coverage = np.array(
            [
                [
                    inside(199980 + (col0 + c + 0.5) * 10, 3400020 - (row0 + r + 0.5) * 10)
                    for c in range(WINDOW_SIZE[1])
                ]
                for r in range(WINDOW_SIZE[0])
            ],
            dtype=np.uint8,
        )
        bands["coverage"] = coverage
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **bands)
        data = buffer.getvalue()
        fixture_file = f"{period}.npz"
        prepared[fixture_file] = data
        manifest["scenes"][period] = {
            "item_id": item["id"],
            "acquisition_datetime": props["datetime"],
            "acquisition_date": props["datetime"][:10],
            "collection": item["collection"],
            "aoi_bounds_wgs84": [114.3, 30.5, 114.45, 30.62],
            "cloud_cover": props["eo:cloud_cover"],
            "crs": f"EPSG:{props['proj:epsg']}",
            "dimensions": list(WINDOW_SIZE),
            "resolution_m": 10.0,
            "window_bounds_utm50n": [241180.0, 3387560.0, 241420.0, 3387800.0],
            "fixture_file": fixture_file,
            "fixture_sha256": hashlib.sha256(data).hexdigest(),
            "coverage_pixels": int(coverage.sum()),
            "asset_identity": identities,
            "stac_item_sha256": hashlib.sha256(
                (ROOT / f"{period}.item.json").read_bytes()
            ).hexdigest(),
        }
    output.mkdir()
    for name, data in prepared.items():
        (output / name).write_bytes(data)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "manifest.sha256").write_text(
        hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest() + "\n", encoding="ascii"
    )
    for period in ("period_a", "period_b"):
        shutil.copy2(ROOT / f"{period}.item.json", output / f"{period}.item.json")
    print(f"Prepared {len(prepared)} fixtures; downloaded {downloaded} bytes")


if __name__ == "__main__":
    main()
