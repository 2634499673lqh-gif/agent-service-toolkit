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


def trusted_landsat_map_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Build map geometry only from verifier-bound dynamic result metadata."""
    provenance = metadata.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("dynamic provenance is missing")
    if provenance.get("aoi_id") != "jianghan_district_420103":
        raise ValueError("dynamic AOI identity is invalid")
    if provenance.get("target_crs") != "EPSG:32649":
        raise ValueError("dynamic CRS is invalid")
    grid = provenance.get("target_grid")
    dimensions = provenance.get("target_dimensions")
    bounds = provenance.get("aoi_bbox")
    if not isinstance(grid, dict) or not isinstance(dimensions, list) or len(dimensions) != 2:
        raise ValueError("dynamic grid metadata is incomplete")
    if not isinstance(bounds, list) or len(bounds) != 4:
        raise ValueError("dynamic AOI bounds are incomplete")
    transform = grid.get("transform")
    if (
        not isinstance(transform, list)
        or len(transform) != 6
        or not all(isinstance(value, (int, float)) for value in transform)
        or not all(isinstance(value, int) and value > 0 for value in dimensions)
    ):
        raise ValueError("dynamic native grid metadata is incomplete")
    height, width = dimensions
    corners = [
        (0, 0),
        (width, 0),
        (width, height),
        (0, height),
    ]
    native_corners = [
        (
            float(transform[0] * column + transform[1] * row + transform[2]),
            float(transform[3] * column + transform[4] * row + transform[5]),
        )
        for column, row in corners
    ]
    native_bounds = [
        min(point[0] for point in native_corners),
        min(point[1] for point in native_corners),
        max(point[0] for point in native_corners),
        max(point[1] for point in native_corners),
    ]
    scenes = provenance.get("scene_provenance")
    if not isinstance(scenes, dict) or not all(key in scenes for key in ("period_a", "period_b")):
        raise ValueError("dynamic scene metadata is incomplete")
    identities = {}
    for period, value in scenes.items():
        if not isinstance(value, dict) or not value.get("scene_ids"):
            raise ValueError("dynamic scene identity is incomplete")
        identities[period] = ",".join(str(item) for item in value["scene_ids"])
    return {
        "fixture_version": "landsat-c2-l2-dynamic-v1",
        "crs": "EPSG:32649",
        "aoi_bounds_wgs84": [float(value) for value in bounds],
        "dimensions": [int(value) for value in dimensions],
        "transform": list(grid["transform"]),
        "periods": {
            "period_a": {
                "scene_identity": identities["period_a"],
                "native_bounds": native_bounds,
                "raster_dimensions": dimensions,
            },
            "period_b": {
                "scene_identity": identities["period_b"],
                "native_bounds": native_bounds,
                "raster_dimensions": dimensions,
            },
        },
    }


__all__ = ["trusted_map_metadata", "trusted_landsat_map_metadata"]
