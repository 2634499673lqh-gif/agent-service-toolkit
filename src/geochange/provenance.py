"""Server-owned map metadata derived from verified fixture manifests."""

from typing import Any

from .fixture import load_manifest as load_ndvi_manifest
from .ndbi import validate_evidence as validate_ndbi_evidence
from .ndwi import _manifest as load_ndwi_manifest


def _manifest_for(indicator: str) -> tuple[dict[str, Any], str, str]:
    if indicator == "NDVI":
        return load_ndvi_manifest(), "EPSG:32650", "real-sentinel2-v1"
    if indicator == "NDWI":
        return load_ndwi_manifest(), "EPSG:32650", "real-sentinel2-ndwi-v5"
    if indicator == "NDBI":
        return validate_ndbi_evidence(), "EPSG:32650", "real-sentinel2-ndbi-v1"
    raise ValueError("unsupported GeoChange indicator")


def trusted_map_metadata(indicator: str) -> dict[str, Any]:
    """Return only values read after the indicator's manifest/hash verification."""

    manifest, crs, fixture_version = _manifest_for(indicator)
    preparation = manifest.get("preparation", {})
    target_grid = preparation.get("target_grid", {})
    dimensions = target_grid.get("dimensions")
    transform = target_grid.get("transform")
    scenes = manifest.get("scenes")
    if not isinstance(scenes, dict):
        raise ValueError("verified fixture manifest lacks raster geometry")
    if not isinstance(dimensions, list):
        first_scene = scenes.get("period_a", {})
        dimensions = first_scene.get("target_dimensions", first_scene.get("dimensions"))
    if not isinstance(dimensions, list):
        raise ValueError("verified fixture manifest lacks raster geometry")
    period_metadata: dict[str, dict[str, Any]] = {}
    for period in ("period_a", "period_b"):
        scene = scenes.get(period)
        if not isinstance(scene, dict):
            raise ValueError("verified fixture manifest lacks a scene")
        scene_dimensions = scene.get("target_dimensions", scene.get("dimensions", dimensions))
        bounds = scene.get("window_bounds_utm50n")
        if (
            bounds is None
            and isinstance(transform, list)
            and len(transform) >= 6
            and isinstance(scene_dimensions, list)
            and len(scene_dimensions) >= 2
        ):
            width, height = int(scene_dimensions[1]), int(scene_dimensions[0])
            bounds = [
                float(transform[2]),
                float(transform[5] + transform[3] * width + transform[4] * height),
                float(transform[2] + transform[0] * width + transform[1] * height),
                float(transform[5]),
            ]
        if not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError("verified fixture manifest lacks native raster bounds")
        scene_transform = scene.get("target_transform", transform)
        if scene_transform is None:
            resolution = float(scene.get("resolution_m", 10))
            scene_transform = [resolution, 0, bounds[0], 0, -resolution, bounds[3]]
        period_metadata[period] = {
            "scene_identity": scene.get("item_id"),
            "date": scene.get("acquisition_date"),
            "native_crs": scene.get("crs", crs),
            "native_bounds": bounds,
            "raster_dimensions": scene_dimensions,
            "target_transform": scene_transform,
            "fixture_file": scene.get("fixture_file"),
        }
    return {
        "fixture_version": fixture_version,
        "crs": crs,
        "aoi_bounds_wgs84": scenes["period_a"].get("aoi_bounds_wgs84"),
        "dimensions": dimensions,
        "transform": period_metadata["period_a"]["target_transform"],
        "periods": period_metadata,
    }


__all__ = ["trusted_map_metadata"]
