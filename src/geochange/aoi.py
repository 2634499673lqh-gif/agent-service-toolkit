import hashlib
import json
import math
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AOI(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_key: str
    canonical_name: str
    bbox: tuple[float, float, float, float]
    geometry: dict[str, object]
    crs: str = "EPSG:4326"
    source: str = "taskpilot.geochange.catalog.v1"

    def _validate_bbox(self) -> None:
        west, south, east, north = self.bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("AOI bbox is invalid")
        if (east - west) * (north - south) > 1.0:
            raise ValueError("AOI is larger than the bounded MVP area")


class TrustedAOI(AOI):
    """Immutable server-owned AOI metadata for the V0.3 Landsat path."""

    aoi_id: str = Field(min_length=1, max_length=80)
    admin_code: str = Field(min_length=1, max_length=32)
    source_version: int = Field(ge=1)
    source_url: str = Field(min_length=1, max_length=500)
    acquired_at: str = Field(min_length=1, max_length=40)
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: str = Field(min_length=1, max_length=120)
    attribution: str = Field(min_length=1, max_length=240)
    area_m2: float = Field(gt=0)


_TRUSTED_AOI_ID = "jianghan_district_420103"
# This identity is deliberately kept in code, outside the editable GeoJSON.  A
# caller who edits the payload and recomputes its embedded ``source_sha256``
# still cannot make it match the approved snapshot.
_TRUSTED_AOI_EXPECTED_SHA256 = "11633b0f428a884c414c90dd4c7c94d14f7ed903fa954aae666eae35984f8c25"
_TRUSTED_AOI_EXPECTED_SOURCE = "OpenStreetMap relation 3077256"
_TRUSTED_AOI_EXPECTED_SOURCE_URL = "https://api.openstreetmap.org/api/0.6/relation/3077256/21.json"
_TRUSTED_AOI_EXPECTED_LICENSE = "ODbL 1.0"
_TRUSTED_AOI_EXPECTED_ATTRIBUTION = "© OpenStreetMap contributors"
_TRUSTED_AOI_EXPECTED_ADMIN_CODE = "420103"
_TRUSTED_AOI_EXPECTED_VERSION = 21
_TRUSTED_AOI_EXPECTED_CRS = "EPSG:4326"
_TRUSTED_AOI_AREA_RELATIVE_TOLERANCE = 0.01
# A stable point in the Jianghan pilot boundary.  This catches an apparently
# well-formed but geographically unrelated replacement snapshot.
_TRUSTED_AOI_PILOT_POINT = (114.25121834387474, 30.6053638)
_TRUSTED_AOI_SNAPSHOT_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "data" / "geochange-aoi" / f"{_TRUSTED_AOI_ID}.geojson",
    Path(__file__).resolve().parents[1] / "data" / "geochange-aoi" / f"{_TRUSTED_AOI_ID}.geojson",
    Path.cwd() / "data" / "geochange-aoi" / f"{_TRUSTED_AOI_ID}.geojson",
)


_EAST_LAKE = AOI(
    catalog_key="wuhan_east_lake",
    canonical_name="Wuhan East Lake",
    bbox=(114.30, 30.50, 114.45, 30.62),
    geometry={
        "type": "Polygon",
        "coordinates": [
            [[114.30, 30.50], [114.45, 30.50], [114.45, 30.62], [114.30, 30.62], [114.30, 30.50]]
        ],
    },
)
_EAST_LAKE._validate_bbox()


def _canonical_snapshot_payload(data: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in data.items() if key != "source_sha256"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _ring_points(geometry: dict[str, Any]) -> list[list[float]]:
    """Return every coordinate from every ring in a polygon geometry."""

    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        rings = coordinates
    elif geometry.get("type") == "MultiPolygon":
        rings = [ring for polygon in coordinates or [] for ring in polygon]
    else:
        rings = None
    if (
        not isinstance(rings, list)
        or not rings
        or not all(isinstance(ring, list) for ring in rings)
    ):
        raise ValueError("trusted AOI geometry is not a polygon")
    return [point for ring in rings for point in ring]


def _validate_trusted_geometry(data: dict[str, Any]) -> None:
    geometry = data.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("trusted AOI geometry is missing")
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("trusted AOI geometry type is unsupported")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        raise ValueError("trusted AOI geometry coordinates are missing")
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    if geometry.get("type") == "MultiPolygon" and not all(
        isinstance(polygon, list) and polygon for polygon in polygons
    ):
        raise ValueError("trusted AOI multipolygon is empty")

    all_points: list[list[float]] = []
    for polygon in polygons:
        if not isinstance(polygon, list) or not polygon:
            raise ValueError("trusted AOI polygon rings are missing")
        for ring in polygon:
            if not isinstance(ring, list) or len(ring) < 4 or ring[0] != ring[-1]:
                raise ValueError(
                    "trusted AOI rings must be closed and contain at least four points"
                )
            for point in ring:
                if (
                    not isinstance(point, list)
                    or len(point) != 2
                    or any(
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not math.isfinite(value)
                        for value in point
                    )
                    or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90)
                ):
                    raise ValueError("trusted AOI has invalid coordinates")
            all_points.extend(ring)

    # A longitude span near the date line is never accepted for this pilot AOI.
    longitudes = [point[0] for point in all_points]
    if max(longitudes) - min(longitudes) >= 180:
        raise ValueError("trusted AOI must not cross the antimeridian")

    try:
        from shapely.geometry import shape

        parsed = shape(geometry)
    except (ImportError, TypeError, ValueError) as exc:
        raise ValueError("trusted AOI geometry cannot be parsed") from exc
    if parsed.is_empty or not parsed.is_valid or parsed.geom_type != geometry.get("type"):
        raise ValueError("trusted AOI geometry is topologically invalid")


def _validate_pilot_location(geometry: dict[str, Any]) -> None:
    try:
        from shapely.geometry import Point, shape

        if not shape(geometry).covers(Point(*_TRUSTED_AOI_PILOT_POINT)):
            raise ValueError("trusted AOI does not cover the Jianghan pilot location")
    except ImportError as exc:
        raise ValueError("trusted AOI topology dependency is unavailable") from exc


def _projected_area_m2(geometry: dict[str, Any]) -> float:
    """Compute area independently in Jianghan's UTM zone (EPSG:32649)."""

    try:
        import rasterio
        from rasterio.env import set_proj_data_search_path
        from rasterio.errors import CRSError
        from rasterio.warp import transform_geom
        from shapely.geometry import shape
    except ImportError as exc:
        raise ValueError("trusted AOI area dependencies are unavailable") from exc
    proj_data = Path(rasterio.__file__).resolve().parent / "proj_data"
    set_proj_data_search_path(str(proj_data))
    try:
        with rasterio.Env(PROJ_DATA=str(proj_data), PROJ_LIB=str(proj_data)):
            projected = transform_geom(_TRUSTED_AOI_EXPECTED_CRS, "EPSG:32649", geometry)
        area = float(shape(projected).area)
    except (CRSError, TypeError, ValueError, RuntimeError):
        # Rasterio wheels can be shipped without a discoverable PROJ database
        # (notably on Windows).  Use a bounded local equirectangular projection
        # as a deterministic geodesic-area fallback; its distortion over this
        # 0.08-degree pilot AOI is well below the explicit verification margin.
        coordinates = geometry.get("coordinates")
        points = []

        def collect(node: Any) -> None:
            if (
                isinstance(node, list)
                and len(node) == 2
                and all(isinstance(value, (int, float)) for value in node)
            ):
                points.append(node)
            elif isinstance(node, list):
                for child in node:
                    collect(child)

        collect(coordinates)
        if not points:
            raise ValueError("trusted AOI projected area cannot be computed")
        mean_latitude = math.radians(sum(point[1] for point in points) / len(points))
        earth_radius_m = 6_378_137.0

        def project(node: Any) -> Any:
            if (
                isinstance(node, list)
                and len(node) == 2
                and all(isinstance(value, (int, float)) for value in node)
            ):
                return [
                    earth_radius_m * math.radians(node[0]) * math.cos(mean_latitude),
                    earth_radius_m * math.radians(node[1]),
                ]
            if isinstance(node, list):
                return [project(child) for child in node]
            return node

        projected = {"type": geometry["type"], "coordinates": project(coordinates)}
        area = float(shape(projected).area)
    if not math.isfinite(area) or area <= 0:
        raise ValueError("trusted AOI projected area is invalid")
    return area


@lru_cache(maxsize=1)
def _load_trusted_aoi_template() -> TrustedAOI:
    """Load and verify the immutable Jianghan boundary snapshot."""

    snapshot = next(
        (candidate for candidate in _TRUSTED_AOI_SNAPSHOT_CANDIDATES if candidate.is_file()),
        None,
    )
    if snapshot is None or snapshot.stat().st_size > 512_000:
        raise ValueError("trusted AOI snapshot is missing or oversized")
    try:
        data = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("trusted AOI snapshot is malformed") from exc
    if not isinstance(data, dict):
        raise ValueError("trusted AOI snapshot is malformed")
    expected_hash = data.get("source_sha256")
    if expected_hash != _TRUSTED_AOI_EXPECTED_SHA256:
        raise ValueError("trusted AOI snapshot is not the approved Jianghan identity")
    actual_hash = hashlib.sha256(_canonical_snapshot_payload(data)).hexdigest()
    if actual_hash != _TRUSTED_AOI_EXPECTED_SHA256:
        raise ValueError("trusted AOI snapshot integrity mismatch")
    if data.get("schema_version") != "taskpilot.aoi.v1":
        raise ValueError("trusted AOI snapshot schema is unsupported")
    if (
        data.get("aoi_id") != _TRUSTED_AOI_ID
        or data.get("admin_code") != _TRUSTED_AOI_EXPECTED_ADMIN_CODE
    ):
        raise ValueError("trusted AOI identity mismatch")
    if data.get("crs") != _TRUSTED_AOI_EXPECTED_CRS:
        raise ValueError("trusted AOI CRS must be EPSG:4326")
    if data.get("source_version") != _TRUSTED_AOI_EXPECTED_VERSION:
        raise ValueError("trusted AOI source version is not approved")
    if data.get("source") != _TRUSTED_AOI_EXPECTED_SOURCE:
        raise ValueError("trusted AOI source provenance is not approved")
    if data.get("source_url") != _TRUSTED_AOI_EXPECTED_SOURCE_URL:
        raise ValueError("trusted AOI source URL is not approved")
    if data.get("license") != _TRUSTED_AOI_EXPECTED_LICENSE:
        raise ValueError("trusted AOI license is not approved")
    if data.get("attribution") != _TRUSTED_AOI_EXPECTED_ATTRIBUTION:
        raise ValueError("trusted AOI attribution is not approved")
    _validate_trusted_geometry(data)
    _validate_pilot_location(data["geometry"])
    bbox = data.get("bbox")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("trusted AOI bbox is missing")
    geometry = data["geometry"]
    points = _ring_points(geometry)
    computed_bbox = [
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    ]
    if any(abs(float(left) - float(right)) > 1e-7 for left, right in zip(bbox, computed_bbox)):
        raise ValueError("trusted AOI bbox does not match geometry")
    area_m2 = float(data.get("area_m2", 0))
    if not 20_000_000 <= area_m2 <= 50_000_000:
        raise ValueError("trusted AOI area is outside the pilot sanity range")
    projected_area_m2 = _projected_area_m2(geometry)
    if abs(projected_area_m2 - area_m2) / projected_area_m2 > _TRUSTED_AOI_AREA_RELATIVE_TOLERANCE:
        raise ValueError("trusted AOI area does not match independently projected geometry")
    aoi = TrustedAOI(
        catalog_key=_TRUSTED_AOI_ID,
        canonical_name=str(data["canonical_name"]),
        bbox=tuple(float(value) for value in bbox),
        geometry=geometry,
        crs=_TRUSTED_AOI_EXPECTED_CRS,
        source=_TRUSTED_AOI_EXPECTED_SOURCE,
        aoi_id=_TRUSTED_AOI_ID,
        admin_code="420103",
        source_version=_TRUSTED_AOI_EXPECTED_VERSION,
        source_url=str(data["source_url"]),
        acquired_at=str(data["acquired_at"]),
        source_hash=expected_hash,
        license=str(data["license"]),
        attribution=_TRUSTED_AOI_EXPECTED_ATTRIBUTION,
        area_m2=area_m2,
    )
    aoi._validate_bbox()
    return aoi


def load_trusted_aoi() -> TrustedAOI:
    """Return an independent copy of the verified server-owned AOI.

    Pydantic's frozen model prevents field reassignment but does not freeze
    nested dictionaries/lists.  Returning a deep copy keeps one caller from
    mutating the cached canonical geometry seen by later requests.
    """

    return deepcopy(_load_trusted_aoi_template())


def resolve_aoi(place: str) -> AOI | TrustedAOI:
    """Resolve only the controlled catalog entry; never geocode arbitrary input."""

    normalized = " ".join(place.casefold().replace("_", " ").split())
    if normalized in {
        _TRUSTED_AOI_ID.replace("_", " "),
        "武汉市江汉区",
        "江汉区",
        "jianghan district wuhan",
    }:
        return load_trusted_aoi()
    if normalized not in {"wuhan east lake", "东湖", "武汉东湖"}:
        raise ValueError("unsupported AOI")
    return _EAST_LAKE


__all__ = ["AOI", "TrustedAOI", "load_trusted_aoi", "resolve_aoi"]
