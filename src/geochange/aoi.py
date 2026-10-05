import hashlib
import json
import math
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
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        rings = coordinates
    elif geometry.get("type") == "MultiPolygon":
        rings = [ring for polygon in coordinates or [] for ring in polygon]
    else:
        rings = None
    if not isinstance(rings, list) or not rings or not isinstance(rings[0], list):
        raise ValueError("trusted AOI geometry is not a polygon")
    return rings[0]


def _validate_trusted_geometry(data: dict[str, Any]) -> None:
    geometry = data.get("geometry")
    if not isinstance(geometry, dict):
        raise ValueError("trusted AOI geometry is missing")
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError("trusted AOI geometry type is unsupported")
    points = _ring_points(geometry)
    if len(points) < 4 or points[0] != points[-1]:
        raise ValueError("trusted AOI outer ring must be closed")
    for point in points:
        if (
            not isinstance(point, list)
            or len(point) != 2
            or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in point)
            or not (-180 <= point[0] <= 180 and -90 <= point[1] <= 90)
        ):
            raise ValueError("trusted AOI has invalid coordinates")
    # A longitude span near the date line is never accepted for this pilot AOI.
    longitudes = [point[0] for point in points]
    if max(longitudes) - min(longitudes) >= 180:
        raise ValueError("trusted AOI must not cross the antimeridian")
    signed_area = sum(
        points[index][0] * points[index + 1][1] - points[index + 1][0] * points[index][1]
        for index in range(len(points) - 1)
    )
    if abs(signed_area) <= 1e-8:
        raise ValueError("trusted AOI geometry is empty")


@lru_cache(maxsize=1)
def load_trusted_aoi() -> TrustedAOI:
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
    if not isinstance(expected_hash, str):
        raise ValueError("trusted AOI snapshot hash is missing")
    actual_hash = hashlib.sha256(_canonical_snapshot_payload(data)).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError("trusted AOI snapshot integrity mismatch")
    if data.get("schema_version") != "taskpilot.aoi.v1":
        raise ValueError("trusted AOI snapshot schema is unsupported")
    if data.get("aoi_id") != _TRUSTED_AOI_ID or data.get("admin_code") != "420103":
        raise ValueError("trusted AOI identity mismatch")
    if data.get("crs") != "EPSG:4326":
        raise ValueError("trusted AOI CRS must be EPSG:4326")
    if data.get("source_version") != 21:
        raise ValueError("trusted AOI source version is not approved")
    _validate_trusted_geometry(data)
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
    aoi = TrustedAOI(
        catalog_key=_TRUSTED_AOI_ID,
        canonical_name=str(data["canonical_name"]),
        bbox=tuple(float(value) for value in bbox),
        geometry=geometry,
        crs="EPSG:4326",
        source="OpenStreetMap relation 3077256",
        aoi_id=_TRUSTED_AOI_ID,
        admin_code="420103",
        source_version=21,
        source_url=str(data["source_url"]),
        acquired_at=str(data["acquired_at"]),
        source_hash=expected_hash,
        license=str(data["license"]),
        attribution=str(data["attribution"]),
        area_m2=area_m2,
    )
    aoi._validate_bbox()
    return aoi


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
