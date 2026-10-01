"""Opt-in preparation only: reproduce the frozen four-band crop, never used by runtime.

Run with numpy==1.26.4, tifffile==2023.4.12 and imagecodecs installed in a
separate preparation environment. Does not change repository dependencies.
"""

import argparse
import hashlib
import io
import json
import urllib.request
from pathlib import Path

import numpy as np
import tifffile

ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-v1"
MAX_DOWNLOAD_BYTES = 20_000_000


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output.resolve()
    if output.exists():
        raise ValueError("output must be a new directory")
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    downloaded = 0

    def read(url: str, start: int | None = None, size: int = 100_000) -> bytes:
        nonlocal downloaded
        headers = {} if start is None else {"Range": f"bytes={start}-{start + size - 1}"}
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            if start is not None and response.status != 206:
                raise ValueError("source must honor bounded Range reads")
            data = response.read(size + 1)
        downloaded += len(data)
        if len(data) > size or downloaded > MAX_DOWNLOAD_BYTES:
            raise ValueError("preparation download bound exceeded")
        return data

    prepared = {}
    for period, scene in manifest["scenes"].items():
        item = json.loads(read(scene["stac_item_url"]))
        if (item["id"], item["collection"], item["properties"]["datetime"],
            item["properties"]["eo:cloud_cover"]) != (
                scene["item_id"], scene["collection"], scene["acquisition_datetime"],
                scene["cloud_cover"]):
            raise ValueError("STAC scene metadata changed")
        arrays = {}
        for band in ("red", "nir"):
            asset = scene[f"{band}_asset_identity"]
            source = item["assets"][band]
            if source["href"] != asset["href"]:
                raise ValueError("STAC asset identity changed")
            radiometry = source["raster:bands"][0]
            if any(radiometry[key] != asset[key] for key in ("scale", "offset", "nodata")):
                raise ValueError("source radiometry changed")
            with urllib.request.urlopen(urllib.request.Request(asset["href"], method="HEAD"),
                                        timeout=30) as response:
                if (response.headers["ETag"].strip('"'), int(response.headers["Content-Length"])) != (
                        asset["etag"], asset["content_length"]):
                    raise ValueError("source COG identity changed")
            header = read(asset["href"], 0, 16_384)
            with tifffile.TiffFile(io.BytesIO(header)) as tiff:
                page = tiff.pages[0]
                if (list(page.shape) != asset["source_shape"]
                    or list(page.tags["ModelTiepointTag"].value) != asset["source_tiepoint"]
                    or page.tags["ModelPixelScaleTag"].value[0] != scene["resolution_m"]):
                    raise ValueError("source raster geometry changed")
                tile_width = page.tags["TileWidth"].value
                tile_height = page.tags["TileLength"].value
                col, row = asset["window_pixel_origin"]
                tile_index = (row // tile_height) * ((page.shape[1] + tile_width - 1) // tile_width) + col // tile_width
                if tile_index != asset["tile_index"]:
                    raise ValueError("source tile geometry changed")
                count = page.databytecounts[tile_index]
                if count > 4_000_000:
                    raise ValueError("source tile exceeds preparation bound")
                data = read(asset["href"], page.dataoffsets[tile_index], count)
                decoded = page.decode(data, tile_index)[0]
                if decoded is None:
                    raise ValueError("source tile is empty")
                height, width = scene["dimensions"]
                crop = decoded[0, row % tile_height:row % tile_height + height,
                               col % tile_width:col % tile_width + width, 0]
                if list(crop.shape) != scene["dimensions"] or crop.dtype != np.uint16:
                    raise ValueError("source crop is invalid")
                arrays[band] = crop
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        data = buffer.getvalue()
        if hashlib.sha256(data).hexdigest() != scene["fixture_sha256"]:
            raise ValueError("prepared checksum differs; use the documented preparation versions")
        prepared[scene["fixture_file"]] = data
    output.mkdir()
    for name, data in prepared.items():
        (output / name).write_bytes(data)
    print(f"Reproduced {len(prepared)} fixtures; downloaded {downloaded} bytes")


if __name__ == "__main__":
    main()
