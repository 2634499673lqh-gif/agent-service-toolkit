"""Frozen, offline Sentinel-2 crop. Caller data never selects files or URLs."""

import hashlib
import json
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import numpy as np

from .aoi import resolve_aoi
from .models import GeoChangeTask
from .raster import VegetationChange, compute_vegetation_change

_MODULE_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_ROOT = (
    _MODULE_ROOT.parent if _MODULE_ROOT.name == "src" else _MODULE_ROOT
) / "data/geochange-fixtures/real-sentinel2-v1"
MANIFEST_SHA256 = "875e22bd8c188e0c0eaf1a1f7c28eef8687454ff62dd4442ce29d4adf0b36b12"
FIXTURE_VERSION = "geochange.real-sentinel2.v1"
EXECUTION_MODE = "CACHED_REAL_SENTINEL2_RASTER"
MAX_FIXTURE_BYTES = 1_000_000


def load_manifest(root: Path = FIXTURE_ROOT) -> dict[str, Any]:
    path = root / "manifest.json"
    if not path.is_file() or path.stat().st_size > 32_768:
        raise ValueError("fixture manifest missing or oversized")
    data = path.read_bytes()
    # Pin the full binding contract, including AOI, geometry and asset radiometry.
    # A modified manifest cannot bless a modified raster by replacing its digest.
    if hashlib.sha256(data).hexdigest() != MANIFEST_SHA256:
        raise ValueError("fixture manifest integrity mismatch")
    return json.loads(data)


def scene_evidence(task: GeoChangeTask, *, root: Path = FIXTURE_ROOT) -> dict[str, str]:
    manifest = load_manifest(root)
    evidence = {"fixture_manifest": MANIFEST_SHA256}
    for period in ("a", "b"):
        scene = manifest["scenes"][f"period_{period}"]
        selected = task.period_a if period == "a" else task.period_b
        if not selected.start.isoformat() <= scene["acquisition_date"] <= selected.end.isoformat():
            raise ValueError("no cached fixture for the validated period")
        if scene["aoi_bounds_wgs84"] != list(resolve_aoi(task.aoi_key).bbox):
            raise ValueError("fixture AOI mismatch")
        fields = {
            "item_id": scene["item_id"],
            "date": scene["acquisition_date"],
            "collection": scene["collection"],
            "cloud_cover": str(scene["cloud_cover"]),
            # Compact exact asset identities; the pinned manifest carries the full URLs.
            "red": hashlib.sha256(scene["red_asset_identity"]["href"].encode()).hexdigest(),
            "nir": hashlib.sha256(scene["nir_asset_identity"]["href"].encode()).hexdigest(),
        }
        evidence.update({f"period_{period}_{key}": value for key, value in fields.items()})
    return evidence


def validate_binding(
    task: GeoChangeTask, evidence: dict[str, str], *, root: Path = FIXTURE_ROOT
) -> None:
    if evidence != scene_evidence(task, root=root):
        raise ValueError("metadata and raster fixture binding mismatch")


def compute_cached_change(
    task: GeoChangeTask,
    evidence: dict[str, str],
    *,
    artifact_dir: str | Path | None = None,
    root: Path = FIXTURE_ROOT,
) -> VegetationChange:
    validate_binding(task, evidence, root=root)
    manifest = load_manifest(root)
    arrays = []
    total_bytes = 0
    for period in ("a", "b"):
        scene = manifest["scenes"][f"period_{period}"]
        if scene["cloud_cover"] > task.cloud_threshold:
            raise ValueError("fixture exceeds the validated cloud threshold")
        path = root / scene["fixture_file"]
        size = path.stat().st_size
        total_bytes += size
        if total_bytes > MAX_FIXTURE_BYTES:
            raise ValueError("fixture exceeds the repository size bound")
        if hashlib.sha256(path.read_bytes()).hexdigest() != scene["fixture_sha256"]:
            raise ValueError("fixture raster checksum mismatch")
        with ZipFile(path) as archive:
            if set(archive.namelist()) != {"red.npy", "nir.npy"} or any(
                member.file_size > 64 * 64 * 2 + 1024 for member in archive.infolist()
            ):
                raise ValueError("fixture archive is invalid")
        with np.load(path, allow_pickle=False) as bundle:
            for band in ("red", "nir"):
                pixels = bundle[band]
                if list(pixels.shape) != scene["dimensions"] or pixels.dtype != np.uint16:
                    raise ValueError("fixture raster dimensions or type mismatch")
                asset = scene[f"{band}_asset_identity"]
                reflectance = pixels.astype(np.float32) * asset["scale"] + asset["offset"]
                # Preserve nodata and reject negative reflectance; neither is vegetation evidence.
                reflectance[(pixels == asset["nodata"]) | (reflectance < 0)] = np.nan
                arrays.append(reflectance)
    return compute_vegetation_change(
        *arrays,
        pixel_area_m2=manifest["scenes"]["period_a"]["resolution_m"] ** 2,
        artifact_dir=artifact_dir,
    )
