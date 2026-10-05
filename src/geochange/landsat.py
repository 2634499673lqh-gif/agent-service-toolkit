"""Trusted, bounded Landsat preparation primitives for TaskPilot V0.3.

This module deliberately stops at prepared, pair-aligned reflectance arrays.  It
does not calculate NDVI or create product artifacts; that boundary belongs to
Implementation B.
"""

from __future__ import annotations

import hashlib
import math
import multiprocessing as mp
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .aoi import TrustedAOI

LANDSAT_STAC_ENDPOINT = "https://planetarycomputer.microsoft.com/api/stac/v1"
LANDSAT_COLLECTION = "landsat-c2-l2"
LANDSAT_PROVIDER = "microsoft-planetary-computer"
SAS_SIGN_ENDPOINT = "https://planetarycomputer.microsoft.com/api/sas/v1/sign"
CONTRACT_VERSION = "v0.3-preparation-1"
SUPPORTED_PLATFORMS = frozenset({"landsat-8", "landsat-9", "LANDSAT_8", "LANDSAT_9"})
_ASSET_BASENAMES = {
    "red": re.compile(r"(?:^|[_-])SR_B4(?:\.TIF)?$", re.IGNORECASE),
    "nir08": re.compile(r"(?:^|[_-])SR_B5(?:\.TIF)?$", re.IGNORECASE),
    "qa_pixel": re.compile(r"(?:^|[_-])QA_PIXEL(?:\.TIF)?$", re.IGNORECASE),
    "qa_radsat": re.compile(r"(?:^|[_-])QA_RADSAT(?:\.TIF)?$", re.IGNORECASE),
    "qa_aerosol": re.compile(r"(?:^|[_-])SR_QA_AEROSOL(?:\.TIF)?$", re.IGNORECASE),
}
_PHYSICAL_BANDS = {
    "red": "SR_B4",
    "nir08": "SR_B5",
    "qa_pixel": "QA_PIXEL",
    "qa_radsat": "QA_RADSAT",
    "qa_aerosol": "SR_QA_AEROSOL",
}
# Planetary Computer's Landsat accounts.  Keep this explicit so a signed URL
# cannot redirect a read to an arbitrary Azure storage account.
_TRUSTED_LANDSAT_STORAGE_HOSTS = frozenset(
    {
        "landsateuwest.blob.core.windows.net",
    }
)
_REMOTE_OPERATION_SEMAPHORE = threading.BoundedSemaphore(2)
_TOTAL_ARRAY_GDAL_BUDGET = 128 * 1024 * 1024
_GDAL_CACHE_BYTES = 32 * 1024 * 1024


class MonthlyPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    period_id: Literal["a", "b"]
    start_utc: datetime
    end_utc: datetime

    @model_validator(mode="after")
    def validate_calendar_month(self) -> MonthlyPeriod:
        start = _as_utc(self.start_utc)
        end = _as_utc(self.end_utc)
        if start != self.start_utc or end != self.end_utc:
            raise ValueError("monthly periods must use UTC timestamps")
        if start.day != 1 or start.hour or start.minute or start.second or start.microsecond:
            raise ValueError("monthly period must start at UTC month boundary")
        if end != _next_month(start) or end <= start:
            raise ValueError("monthly period must end at the next UTC month boundary")
        if start.year not in {2023, 2024, 2025}:
            raise ValueError("monthly period year is outside the V0.3 bound")
        return self


class PeriodPair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    period_a: MonthlyPeriod
    period_b: MonthlyPeriod

    @model_validator(mode="after")
    def validate_pair(self) -> PeriodPair:
        if self.period_a.period_id != "a" or self.period_b.period_id != "b":
            raise ValueError("period ids must be a and b")
        if not (
            self.period_a.end_utc <= self.period_b.start_utc
            or self.period_b.end_utc <= self.period_a.start_utc
        ):
            raise ValueError("periods must not overlap")
        return self


class DiscoveryLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_candidates: int = Field(default=20, ge=1, le=20)
    max_selected_scenes: int = Field(default=3, ge=1, le=3)
    max_target_pixels: int = Field(default=500_000, ge=1, le=500_000)
    max_window_pixels: int = Field(default=262_144, ge=1, le=262_144)
    max_concurrent_remote_asset_operations: int = Field(default=2, ge=1, le=2)
    request_deadline_seconds: float = Field(default=180.0, gt=0, le=180.0)
    temporary_disk_bytes: int = Field(default=128 * 1024 * 1024, ge=1)
    # 96 MiB array budget + 32 MiB GDAL cache = frozen 128 MiB total.
    array_cache_bytes: int = Field(default=96 * 1024 * 1024, ge=1)
    artifact_bytes: int = Field(default=16 * 1024 * 1024, ge=1)


class SelectionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_selected_scenes: int = Field(default=3, ge=1, le=3)
    single_scene_intersection_ratio: float = Field(default=0.99, ge=0, le=1)


class AssetIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=80)
    physical_band: str = Field(min_length=1, max_length=40)
    identity_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    href: str = Field(min_length=1, max_length=2000)
    dtype: str | None = Field(default=None, max_length=32)
    scale: float | None = None
    offset: float | None = None
    nodata: int | float | None = None
    # STAC projection evidence is retained separately from the metadata
    # reported by the opened COG.  The latter is checked against these values
    # before any pixels are accepted.
    expected_crs: str | None = Field(default=None, max_length=80)
    expected_transform: tuple[float, float, float, float, float, float] | None = None
    expected_width: int | None = Field(default=None, gt=0)
    expected_height: int | None = Field(default=None, gt=0)


class LandsatScene(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str = Field(min_length=1, max_length=160)
    acquisition_datetime: datetime
    footprint: dict[str, Any]
    intersection_ratio: float = Field(ge=0, le=1)
    cloud_cover: float = Field(ge=0, le=100)
    collection: Literal["landsat-c2-l2"] = LANDSAT_COLLECTION
    platform: str = Field(min_length=1, max_length=40)
    processing_level: str = Field(min_length=1, max_length=80)
    source_crs: str = Field(min_length=1, max_length=80)
    assets: dict[str, AssetIdentity]
    asset_key_map: dict[str, str]
    mtl_href: str | None = Field(default=None, max_length=2000)
    provider: Literal["microsoft-planetary-computer"] = LANDSAT_PROVIDER


class DiscoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aoi_id: str
    provider: Literal["microsoft-planetary-computer"] = LANDSAT_PROVIDER
    collection: Literal["landsat-c2-l2"] = LANDSAT_COLLECTION
    candidates: dict[Literal["a", "b"], list[LandsatScene]]
    query_bbox: tuple[float, float, float, float]
    hard_limits_applied: dict[str, int | float | bool]


class SelectedScenePair(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    period_a: list[LandsatScene]
    period_b: list[LandsatScene]


class TargetGrid(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    crs: str
    transform: tuple[float, float, float, float, float, float]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    resolution_m: float = Field(gt=0)


class Coverage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    aoi_rasterized_pixels: int = Field(ge=0)
    preparation_valid_pixels: int = Field(ge=0)
    preparation_coverage_pct: float = Field(ge=0, le=100)
    scene_count: int = Field(ge=0, le=3)


@dataclass(frozen=True)
class PreparedPeriodDataset:
    period_id: Literal["a", "b"]
    requested_period: MonthlyPeriod
    red_reflectance: np.ndarray
    nir_reflectance: np.ndarray
    preparation_valid_mask: np.ndarray
    aoi_mask: np.ndarray
    source_scene_index: np.ndarray
    target_grid: TargetGrid
    coverage: Coverage
    provenance: dict[str, Any]

    def __post_init__(self) -> None:
        shape = self.red_reflectance.shape
        if self.red_reflectance.dtype != np.float32 or self.nir_reflectance.dtype != np.float32:
            raise ValueError("prepared reflectance must be float32")
        if self.red_reflectance.ndim != 2 or self.nir_reflectance.shape != shape:
            raise ValueError("prepared reflectance grids must match")
        for mask in (self.preparation_valid_mask, self.aoi_mask):
            if mask.shape != shape or mask.dtype != np.bool_:
                raise ValueError("prepared masks must be boolean and aligned")
        if self.source_scene_index.shape != shape or self.source_scene_index.dtype != np.int16:
            raise ValueError("source scene index must be int16 and aligned")
        if shape != (self.target_grid.height, self.target_grid.width):
            raise ValueError("prepared arrays do not match target grid")
        if np.any(self.preparation_valid_mask & ~self.aoi_mask):
            raise ValueError("preparation mask must be contained by AOI mask")
        if np.any(np.isfinite(self.red_reflectance) & ~self.preparation_valid_mask):
            raise ValueError("invalid Red cells must be NaN")
        if np.any(np.isfinite(self.nir_reflectance) & ~self.preparation_valid_mask):
            raise ValueError("invalid NIR cells must be NaN")


@dataclass(frozen=True)
class PreparedPeriodPair:
    period_a: PreparedPeriodDataset
    period_b: PreparedPeriodDataset
    common_preparation_valid_mask: np.ndarray
    pair_grid: TargetGrid
    hard_limits_applied: dict[str, int | float | bool]
    operational_metrics: dict[str, int | float | bool]
    best_effort_warnings: tuple[str, ...]
    contract_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if (
            self.period_a.target_grid != self.pair_grid
            or self.period_b.target_grid != self.pair_grid
        ):
            raise ValueError("period datasets must share one pair-wide grid")
        expected = (
            self.period_a.aoi_mask
            & self.period_b.aoi_mask
            & self.period_a.preparation_valid_mask
            & self.period_b.preparation_valid_mask
        )
        if self.common_preparation_valid_mask.dtype != np.bool_:
            raise ValueError("common preparation mask must be boolean")
        if not np.array_equal(self.common_preparation_valid_mask, expected):
            raise ValueError("common preparation mask is not derived from both periods")


class PreparationFailure(RuntimeError):
    """Bounded failure that is safe to expose to the runtime boundary."""

    def __init__(
        self,
        code: str,
        *,
        stage: str,
        retryable: bool = False,
        counts: Mapping[str, int | float] | None = None,
        sanitized_diagnostics: Mapping[str, str] | None = None,
    ) -> None:
        self.code = code
        self.stage = stage
        self.retryable = retryable
        self.counts = dict(counts or {})
        self.sanitized_diagnostics = {
            str(key): str(value)[:200] for key, value in (sanitized_diagnostics or {}).items()
        }
        super().__init__(f"{code} at {stage}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "stage": self.stage,
            "retryable": self.retryable,
            "counts": dict(self.counts),
            "sanitized_diagnostics": dict(self.sanitized_diagnostics),
        }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value.astimezone(UTC)


def _next_month(value: datetime) -> datetime:
    year = value.year + (1 if value.month == 12 else 0)
    month = 1 if value.month == 12 else value.month + 1
    return value.replace(year=year, month=month, day=1)


def default_period_pair() -> PeriodPair:
    return PeriodPair(
        period_a=MonthlyPeriod(
            period_id="a",
            start_utc=datetime(2023, 7, 1, tzinfo=UTC),
            end_utc=datetime(2023, 8, 1, tzinfo=UTC),
        ),
        period_b=MonthlyPeriod(
            period_id="b",
            start_utc=datetime(2024, 7, 1, tzinfo=UTC),
            end_utc=datetime(2024, 8, 1, tzinfo=UTC),
        ),
    )


def _provider_host_allowed(url: str, *, allow_stac: bool = False) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return False
    if allow_stac and host == "planetarycomputer.microsoft.com":
        return True
    return host in _TRUSTED_LANDSAT_STORAGE_HOSTS


def _reject_query_credentials(url: str) -> None:
    query = parse_qs(urlparse(url).query)
    if any(key.casefold() in {"sig", "se", "sp", "sv", "st", "spr", "sr"} for key in query):
        raise PreparationFailure("security_rejected", stage="asset_validation")


def _remaining_seconds(deadline_monotonic: float | None, default: float = 30.0) -> float:
    """Return a positive bounded timeout for one provider operation."""
    if deadline_monotonic is None:
        return default
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise PreparationFailure("timeout", stage="provider", retryable=True)
    return max(0.001, min(default, remaining))


def _operation_deadline(limits: DiscoveryLimits, deadline_monotonic: float | None) -> float:
    deadline = (
        time.monotonic() + limits.request_deadline_seconds
        if deadline_monotonic is None
        else deadline_monotonic
    )
    if deadline <= time.monotonic():
        raise PreparationFailure("timeout", stage="deadline", retryable=True)
    return deadline


def _http_get(
    client: httpx.Client,
    url: str,
    *,
    deadline_monotonic: float | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Issue a GET with the shared deadline (and retain compatibility with fakes)."""
    timeout = _remaining_seconds(deadline_monotonic)
    try:
        response = client.get(url, timeout=timeout, **kwargs)
    except TypeError:
        # Small test fakes often expose only ``get(url, **kwargs)``.  Their
        # bounded behavior remains controlled by the caller's deadline checks.
        response = client.get(url, **kwargs)
    if response.is_redirect or response.history:
        raise PreparationFailure("security_rejected", stage="provider_redirect")
    return response


def _identity_hash(href: str) -> str:
    parsed = urlparse(href)
    return hashlib.sha256(f"{parsed.hostname}{parsed.path}".encode()).hexdigest()


def _asset_basename(href: str) -> str:
    return Path(urlparse(href).path).name.upper()


def _projection_crs(value: Any) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return f"EPSG:{value}"
    if isinstance(value, str) and value.strip():
        candidate = value.strip()
        if candidate.upper().startswith("EPSG:"):
            return candidate.upper()
    return None


def _projection_evidence(
    raw_asset: Mapping[str, Any], properties: Mapping[str, Any]
) -> tuple[
    str | None, tuple[float, float, float, float, float, float] | None, int | None, int | None
]:
    """Read asset-level STAC projection fields, falling back to item fields.

    STAC's projection extension permits item-level values when every asset in
    the item shares the same grid.  We preserve missing optional values as
    ``None``; no geometry, dimensions, or transform is fabricated here.
    """

    def value(key: str) -> Any:
        return raw_asset[key] if key in raw_asset else properties.get(key)

    crs = _projection_crs(value("proj:epsg")) or _projection_crs(value("proj:code"))
    raw_transform = value("proj:transform")
    transform: tuple[float, float, float, float, float, float] | None = None
    if raw_transform is not None:
        if (
            not isinstance(raw_transform, (list, tuple))
            or len(raw_transform) != 6
            or not all(
                isinstance(item, (int, float)) and math.isfinite(float(item))
                for item in raw_transform
            )
        ):
            raise PreparationFailure("invalid_raster_metadata", stage="asset_mapping")
        transform = tuple(float(item) for item in raw_transform)  # type: ignore[assignment]
    raw_shape = value("proj:shape")
    width: int | None = None
    height: int | None = None
    if raw_shape is not None:
        if (
            not isinstance(raw_shape, (list, tuple))
            or len(raw_shape) != 2
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item <= 0
                for item in raw_shape
            )
        ):
            raise PreparationFailure("invalid_raster_metadata", stage="asset_mapping")
        height, width = int(raw_shape[0]), int(raw_shape[1])
    raw_width = value("proj:width")
    raw_height = value("proj:height")
    if raw_width is not None or raw_height is not None:
        if (
            isinstance(raw_width, bool)
            or isinstance(raw_height, bool)
            or not isinstance(raw_width, int)
            or not isinstance(raw_height, int)
            or raw_width <= 0
            or raw_height <= 0
        ):
            raise PreparationFailure("invalid_raster_metadata", stage="asset_mapping")
        if width is not None and (width != raw_width or height != raw_height):
            raise PreparationFailure("invalid_raster_metadata", stage="asset_mapping")
        width, height = int(raw_width), int(raw_height)
    return crs, transform, width, height


def _asset_mapping(
    feature: Mapping[str, Any],
) -> tuple[dict[str, AssetIdentity], dict[str, str]]:
    assets = feature.get("assets")
    if not isinstance(assets, Mapping):
        raise PreparationFailure("missing_asset", stage="asset_mapping")
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
    result: dict[str, AssetIdentity] = {}
    key_map: dict[str, str] = {}
    for role, pattern in _ASSET_BASENAMES.items():
        matches: list[tuple[str, Mapping[str, Any]]] = []
        for key, raw in assets.items():
            if not isinstance(key, str) or not isinstance(raw, Mapping):
                continue
            href = raw.get("href")
            if isinstance(href, str) and pattern.search(_asset_basename(href).replace(".TIF", "")):
                matches.append((key, raw))
        if not matches and role in assets and isinstance(assets[role], Mapping):
            raw = assets[role]
            href = raw.get("href")
            if isinstance(href, str) and pattern.search(_asset_basename(href).replace(".TIF", "")):
                matches = [(role, raw)]
        if role == "qa_aerosol" and not matches:
            continue
        if len(matches) != 1:
            raise PreparationFailure(
                "missing_asset", stage="asset_mapping", sanitized_diagnostics={"role": role}
            )
        key, raw = matches[0]
        href = str(raw["href"])
        if not _provider_host_allowed(href):
            raise PreparationFailure("security_rejected", stage="asset_mapping")
        _reject_query_credentials(href)
        raster_bands = raw.get("raster:bands")
        band_metadata = raster_bands[0] if isinstance(raster_bands, list) and raster_bands else {}
        if not isinstance(band_metadata, Mapping):
            band_metadata = {}
        expected_crs, expected_transform, expected_width, expected_height = _projection_evidence(
            raw, properties
        )
        result[role] = AssetIdentity(
            key=key,
            physical_band=_PHYSICAL_BANDS[role],
            identity_hash=_identity_hash(href),
            href=href,
            dtype=str(band_metadata["data_type"]) if band_metadata.get("data_type") else None,
            scale=float(band_metadata["scale"]) if band_metadata.get("scale") is not None else None,
            offset=float(band_metadata["offset"])
            if band_metadata.get("offset") is not None
            else None,
            nodata=band_metadata.get("nodata"),
            expected_crs=expected_crs,
            expected_transform=expected_transform,
            expected_width=expected_width,
            expected_height=expected_height,
        )
        key_map[_PHYSICAL_BANDS[role]] = key
    for required in ("red", "nir08", "qa_pixel", "qa_radsat"):
        if required not in result:
            raise PreparationFailure("missing_asset", stage="asset_mapping")
    return result, key_map


def _scene_from_feature(feature: Mapping[str, Any], aoi: TrustedAOI) -> LandsatScene:
    properties = feature.get("properties")
    if not isinstance(properties, Mapping):
        raise PreparationFailure("no_candidate", stage="scene_validation")
    item_id = feature.get("id")
    collection = feature.get("collection")
    platform = properties.get("platform") or properties.get("platforms")
    if isinstance(platform, list):
        platform = platform[0] if platform else ""
    processing_level = (
        properties.get("landsat:processing_level")
        or properties.get("processing:level")
        or properties.get("landsat:correction")
    )
    cloud = properties.get("eo:cloud_cover")
    timestamp = properties.get("datetime")
    geometry = feature.get("geometry")
    if not isinstance(item_id, str) or collection != LANDSAT_COLLECTION:
        raise PreparationFailure("no_candidate", stage="scene_validation")
    if not isinstance(platform, str) or platform.casefold() not in {
        p.casefold() for p in SUPPORTED_PLATFORMS
    }:
        raise PreparationFailure("no_candidate", stage="scene_validation")
    if not isinstance(processing_level, str) or "L2" not in processing_level.upper():
        raise PreparationFailure("no_candidate", stage="scene_validation")
    if not isinstance(cloud, (int, float)) or not math.isfinite(float(cloud)):
        raise PreparationFailure("no_candidate", stage="scene_validation")
    if not isinstance(timestamp, str) or not isinstance(geometry, Mapping):
        raise PreparationFailure("no_candidate", stage="scene_validation")
    try:
        acquisition = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise PreparationFailure("no_candidate", stage="scene_validation") from exc
    if not _geometry_intersects(geometry, aoi.geometry):
        raise PreparationFailure("no_candidate", stage="scene_validation")
    assets, key_map = _asset_mapping(feature)
    mtl_href = None
    raw_assets = feature.get("assets")
    if isinstance(raw_assets, Mapping):
        for metadata_key in ("mtl.txt", "mtl.json", "mtl.xml"):
            raw_metadata = raw_assets.get(metadata_key)
            candidate = raw_metadata.get("href") if isinstance(raw_metadata, Mapping) else None
            if isinstance(candidate, str):
                if not _provider_host_allowed(candidate):
                    raise PreparationFailure("security_rejected", stage="asset_mapping")
                _reject_query_credentials(candidate)
                mtl_href = candidate
                break
    item_crs = _projection_crs(properties.get("proj:epsg")) or _projection_crs(
        properties.get("proj:code")
    )
    source_crs = item_crs or assets["red"].expected_crs or ""
    if not source_crs:
        raise PreparationFailure("invalid_raster_metadata", stage="scene_validation")
    ratio = _geometry_intersection_ratio(geometry, aoi.geometry)
    return LandsatScene(
        item_id=item_id,
        acquisition_datetime=acquisition,
        footprint=dict(geometry),
        intersection_ratio=ratio,
        cloud_cover=float(cloud),
        platform=platform,
        processing_level=processing_level,
        source_crs=source_crs,
        assets=assets,
        asset_key_map=key_map,
        mtl_href=mtl_href,
    )


def _geometry_intersects(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    try:
        from shapely.geometry import shape

        return bool(
            shape(left).is_valid and shape(right).is_valid and shape(left).intersects(shape(right))
        )
    except Exception:
        left_coords = _geometry_bbox(left)
        right_coords = _geometry_bbox(right)
        return not (
            left_coords[2] <= right_coords[0]
            or right_coords[2] <= left_coords[0]
            or left_coords[3] <= right_coords[1]
            or right_coords[3] <= left_coords[1]
        )


def _geometry_intersection_ratio(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    try:
        from shapely.geometry import shape

        left_shape = shape(left)
        right_shape = shape(right)
        if not left_shape.is_valid or not right_shape.is_valid or right_shape.area <= 0:
            return 0.0
        return max(
            0.0, min(1.0, float(left_shape.intersection(right_shape).area / right_shape.area))
        )
    except Exception:
        left_bbox = _geometry_bbox(left)
        right_bbox = _geometry_bbox(right)
        overlap = max(
            0.0, min(left_bbox[2], right_bbox[2]) - max(left_bbox[0], right_bbox[0])
        ) * max(0.0, min(left_bbox[3], right_bbox[3]) - max(left_bbox[1], right_bbox[1]))
        area = max(1e-12, (right_bbox[2] - right_bbox[0]) * (right_bbox[3] - right_bbox[1]))
        return max(0.0, min(1.0, overlap / area))


def _geometry_bbox(geometry: Mapping[str, Any]) -> tuple[float, float, float, float]:
    values: list[tuple[float, float]] = []

    def walk(node: Any) -> None:
        if (
            isinstance(node, (list, tuple))
            and len(node) == 2
            and all(isinstance(value, (int, float)) for value in node)
        ):
            values.append((float(node[0]), float(node[1])))
        elif isinstance(node, (list, tuple)):
            for child in node:
                walk(child)

    walk(geometry.get("coordinates", []))
    if not values:
        raise ValueError("geometry has no coordinates")
    return (
        min(value[0] for value in values),
        min(value[1] for value in values),
        max(value[0] for value in values),
        max(value[1] for value in values),
    )


def _period_datetime(period: MonthlyPeriod) -> str:
    return f"{period.start_utc.isoformat().replace('+00:00', 'Z')}/{period.end_utc.isoformat().replace('+00:00', 'Z')}"


def discover_landsat(
    aoi: TrustedAOI,
    periods: PeriodPair,
    limits: DiscoveryLimits | None = None,
    *,
    client: httpx.Client | None = None,
    deadline_monotonic: float | None = None,
) -> DiscoveryReport:
    limits = limits or DiscoveryLimits()
    deadline_monotonic = _operation_deadline(limits, deadline_monotonic)
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    candidates: dict[str, list[LandsatScene]] = {}
    try:
        for period in (periods.period_a, periods.period_b):
            payload: Any = None
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    if time.monotonic() >= deadline_monotonic:
                        raise PreparationFailure("timeout", stage="stac_search", retryable=True)
                    request_json = {
                        "collections": [LANDSAT_COLLECTION],
                        "datetime": _period_datetime(period),
                        "intersects": aoi.geometry,
                        "limit": limits.max_candidates,
                    }
                    with _REMOTE_OPERATION_SEMAPHORE:
                        try:
                            response = http.post(
                                LANDSAT_STAC_ENDPOINT + "/search",
                                json=request_json,
                                timeout=_remaining_seconds(deadline_monotonic),
                            )
                        except TypeError:
                            response = http.post(
                                LANDSAT_STAC_ENDPOINT + "/search", json=request_json
                            )
                    if response.is_redirect or response.history:
                        raise PreparationFailure("security_rejected", stage="stac_redirect")
                    if response.status_code == 429 or response.status_code >= 500:
                        last_error = httpx.HTTPStatusError(
                            f"provider status {response.status_code}",
                            request=response.request,
                            response=response,
                        )
                        if attempt < 2:
                            time.sleep(
                                min(0.1 * (attempt + 1), _remaining_seconds(deadline_monotonic))
                            )
                            continue
                    response.raise_for_status()
                    payload = response.json()
                    break
                except PreparationFailure:
                    raise
                except (httpx.HTTPError, ValueError) as exc:
                    last_error = exc
                    if attempt < 2:
                        time.sleep(min(0.1 * (attempt + 1), _remaining_seconds(deadline_monotonic)))
                        continue
            if payload is None:
                raise PreparationFailure(
                    "provider_unavailable", stage="stac_search", retryable=True
                ) from last_error
            features = payload.get("features") if isinstance(payload, Mapping) else None
            if not isinstance(features, list):
                raise PreparationFailure("provider_unavailable", stage="stac_search")
            valid: list[LandsatScene] = []
            for feature in features[: limits.max_candidates]:
                if not isinstance(feature, Mapping):
                    continue
                try:
                    valid.append(_scene_from_feature(feature, aoi))
                except PreparationFailure:
                    continue
            if not valid:
                raise PreparationFailure("no_candidate", stage="stac_search")
            candidates[period.period_id] = sorted(
                valid,
                key=lambda scene: (
                    -scene.intersection_ratio,
                    scene.cloud_cover,
                    abs((scene.acquisition_datetime - period.start_utc).total_seconds()),
                    scene.item_id,
                ),
            )[: limits.max_candidates]
    finally:
        if own_client:
            http.close()
    return DiscoveryReport(
        aoi_id=aoi.aoi_id,
        candidates={"a": candidates["a"], "b": candidates["b"]},
        query_bbox=tuple(aoi.bbox),
        hard_limits_applied={
            "max_candidates": limits.max_candidates,
            "max_selected_scenes": limits.max_selected_scenes,
            "polygon_intersection": True,
            "bbox_only_coverage": False,
        },
    )


def select_landsat_scenes(
    report: DiscoveryReport,
    policy: SelectionPolicy | None = None,
) -> SelectedScenePair:
    policy = policy or SelectionPolicy()

    def select(items: list[LandsatScene]) -> list[LandsatScene]:
        if not items:
            raise PreparationFailure("no_candidate", stage="scene_selection")
        # Footprint overlap is only a candidate ordering signal.  A clouded
        # scene can cover the whole polygon while contributing zero usable
        # pixels, so quality is decided by the bounded raster preparation loop.
        # Return a deterministic bounded candidate set; that loop stops as soon
        # as the provisional 70% gate is actually met.
        return items[: min(policy.max_selected_scenes, 3)]

    return SelectedScenePair(
        period_a=select(report.candidates["a"]), period_b=select(report.candidates["b"])
    )


def apply_radiometry(
    dn: np.ndarray,
    *,
    scale: float,
    offset: float,
    nodata: int | float | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return calibrated float32 values, valid mask, and out-of-nominal diagnostics."""

    values = np.asarray(dn)
    if values.ndim != 2 or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("Landsat surface reflectance DN must be a two-dimensional integer array")
    valid = np.isfinite(values) & (values >= 1) & (values <= 65455)
    if nodata is not None:
        valid &= values != nodata
    reflectance = values.astype(np.float32) * np.float32(scale) + np.float32(offset)
    reflectance[~valid] = np.nan
    out_of_nominal = valid & ((reflectance < 0) | (reflectance > 1))
    return (
        reflectance.astype(np.float32, copy=False),
        valid.astype(bool),
        out_of_nominal.astype(bool),
    )


def qa_pixel_valid_mask(qa_pixel: np.ndarray) -> np.ndarray:
    values = np.asarray(qa_pixel)
    if values.ndim != 2 or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("QA_PIXEL must be a two-dimensional integer array")
    invalid = (
        ((values & (1 << 0)) != 0)
        | ((values & (1 << 1)) != 0)
        | ((values & (1 << 2)) != 0)
        | ((values & (1 << 3)) != 0)
        | ((values & (1 << 4)) != 0)
        | ((values & (1 << 5)) != 0)
        | (((values >> 8) & 0b11) == 0b11)
        | (((values >> 10) & 0b11) == 0b11)
        | (((values >> 12) & 0b11) == 0b11)
        | (((values >> 14) & 0b11) == 0b11)
    )
    return ~invalid


def qa_radsat_valid_mask(qa_radsat: np.ndarray) -> np.ndarray:
    values = np.asarray(qa_radsat)
    if values.ndim != 2 or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("QA_RADSAT must be a two-dimensional integer array")
    return ((values & (1 << 3)) == 0) & ((values & (1 << 4)) == 0) & ((values & (1 << 11)) == 0)


def qa_aerosol_diagnostics(qa_aerosol: np.ndarray) -> dict[str, int | float]:
    """Return bounded SR_QA_AEROSOL quality counts for provenance.

    Bits are diagnostics only.  In particular, interpolated aerosol is not
    converted to NoData and does not alter the A preparation validity mask.
    """
    values = np.asarray(qa_aerosol)
    if values.ndim != 2 or not np.issubdtype(values.dtype, np.integer):
        raise ValueError("SR_QA_AEROSOL must be a two-dimensional integer array")
    total = int(values.size)
    fill = (values & 1) != 0
    # Collection 2 SR_QA_AEROSOL encodes retrieval validity in bit 1,
    # aerosol level in bits 2-3, and interpolation in bit 5.
    valid_retrieval = ((values >> 1) & 1) != 0
    interpolated = ((values >> 5) & 1) != 0
    level = (values >> 2) & 0b11
    return {
        "pixels": total,
        "fill_pixels": int(fill.sum()),
        "valid_retrieval_pixels": int((valid_retrieval & ~fill).sum()),
        "interpolated_pixels": int((interpolated & ~fill).sum()),
        "aerosol_level_low_medium_high_pixels": int(np.isin(level, (1, 2, 3)).sum()),
    }


def _target_grid(aoi: TrustedAOI, crs: str, limits: DiscoveryLimits) -> TargetGrid:
    try:
        import rasterio
        from rasterio.env import set_proj_data_search_path
        from rasterio.transform import from_origin
        from rasterio.warp import transform_bounds
    except ImportError as exc:
        raise PreparationFailure(
            "invalid_raster_metadata",
            stage="grid",
            sanitized_diagnostics={"reason": "Rasterio is required"},
        ) from exc
    proj_data = Path(rasterio.__file__).resolve().parent / "proj_data"
    set_proj_data_search_path(str(proj_data))
    left, bottom, right, top = transform_bounds("EPSG:4326", crs, *aoi.bbox, densify_pts=21)
    resolution = 30.0
    width = max(1, int(math.ceil((right - left) / resolution)))
    height = max(1, int(math.ceil((top - bottom) / resolution)))
    if width * height > limits.max_target_pixels:
        raise PreparationFailure(
            "read_budget_exceeded", stage="grid", counts={"target_pixels": width * height}
        )
    transform = from_origin(left, top, resolution, resolution)
    return TargetGrid(
        crs=crs,
        transform=tuple(float(value) for value in transform[:6]),
        width=width,
        height=height,
        resolution_m=resolution,
    )


def _rasterio_worker(
    connection: Any,
    signed_href: str,
    target_grid: dict[str, Any],
    resampling_name: str,
    max_window_pixels: int,
    gdal_cache_bytes: int,
) -> None:
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.env import set_proj_data_search_path
        from rasterio.transform import Affine
        from rasterio.warp import reproject, transform_bounds
        from rasterio.windows import from_bounds

        grid_transform = Affine(*target_grid["transform"])
        target_crs = target_grid["crs"]
        proj_data = Path(rasterio.__file__).resolve().parent / "proj_data"
        set_proj_data_search_path(str(proj_data))
        with rasterio.Env(
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
            PROJ_DATA=str(proj_data),
            GDAL_CACHEMAX=max(1, int(gdal_cache_bytes // (1024 * 1024))),
        ):
            with rasterio.open(signed_href) as source:
                if source.count != 1 or source.crs is None or source.transform.is_identity:
                    raise ValueError("source raster metadata is invalid")
                target_bounds = rasterio.transform.array_bounds(
                    target_grid["height"], target_grid["width"], grid_transform
                )
                source_bounds = transform_bounds(
                    target_crs, source.crs, *target_bounds, densify_pts=21
                )
                window = from_bounds(*source_bounds, transform=source.transform)
                window = window.round_offsets().round_lengths()
                if (
                    window.width <= 0
                    or window.height <= 0
                    or window.width * window.height > max_window_pixels
                ):
                    raise ValueError("source window exceeds the bounded limit")
                fill_value = source.nodata if source.nodata is not None else 0
                source_array = source.read(1, window=window, boundless=True, fill_value=fill_value)
                destination_dtype = source_array.dtype
                destination = np.full(
                    (target_grid["height"], target_grid["width"]),
                    fill_value,
                    dtype=destination_dtype,
                )
                reproject(
                    source=source_array,
                    destination=destination,
                    src_transform=source.window_transform(window),
                    src_crs=source.crs,
                    dst_transform=grid_transform,
                    dst_crs=target_crs,
                    resampling=getattr(Resampling, resampling_name),
                    src_nodata=fill_value,
                    dst_nodata=fill_value,
                )
                connection.send(
                    {
                        "array": destination,
                        "dtype": str(source.dtypes[0]),
                        "scale": float(source.scales[0] if source.scales else 1.0),
                        "offset": float(source.offsets[0] if source.offsets else 0.0),
                        "nodata": source.nodata,
                        "crs": source.crs.to_string(),
                        "transform": tuple(float(value) for value in source.transform[:6]),
                        "width": int(source.width),
                        "height": int(source.height),
                        "count": int(source.count),
                    }
                )
    except Exception as exc:
        message = re.sub(r"https?://[^\s]+", "<redacted-url>", str(exc))[:160]
        connection.send({"error": type(exc).__name__, "message": message})
    finally:
        connection.close()


def _cleanup_child(process: Any, parent: Any) -> None:
    """Supervise a raster child on every path: terminate, join, kill, join."""
    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)
    finally:
        try:
            parent.close()
        except Exception:
            pass


def _read_asset(
    href: str,
    target_grid: TargetGrid,
    *,
    resampling: str,
    client: httpx.Client,
    deadline_seconds: float,
    max_window_pixels: int = 262_144,
    deadline_monotonic: float | None = None,
) -> tuple[np.ndarray, dict[str, Any], int]:
    if target_grid.width * target_grid.height > 500_000:
        raise PreparationFailure(
            "read_budget_exceeded",
            stage="target_grid",
            counts={"target_pixels": target_grid.width * target_grid.height},
        )
    if not _provider_host_allowed(href):
        raise PreparationFailure("security_rejected", stage="asset_read")
    _reject_query_credentials(href)
    signed_href: str | None = None
    probe: httpx.Response | None = None
    deadline_monotonic = deadline_monotonic or (time.monotonic() + max(0.001, deadline_seconds))
    # One semaphore covers SAS signing, range probe, and the supervised GDAL
    # child, so concurrent provider work cannot exceed the process contract.
    with _REMOTE_OPERATION_SEMAPHORE:
        for attempt in range(3):
            try:
                signed_response = _http_get(
                    client,
                    SAS_SIGN_ENDPOINT,
                    deadline_monotonic=deadline_monotonic,
                    params={"href": href},
                )
                signed_response.raise_for_status()
                signed_payload = signed_response.json()
                candidate = (
                    signed_payload.get("href") if isinstance(signed_payload, Mapping) else None
                )
                # Revalidate the host after signing, immediately before any
                # range probe and again before handing the URL to GDAL.
                if not isinstance(candidate, str) or not _provider_host_allowed(candidate):
                    raise PreparationFailure("security_rejected", stage="asset_signing")
                _reject_query_credentials(href)
                signed_href = candidate
                probe = _http_get(
                    client,
                    signed_href,
                    deadline_monotonic=deadline_monotonic,
                    headers={"Range": "bytes=0-65535"},
                )
                if probe.status_code in {200, 206}:
                    break
                if probe.status_code not in {403, 429} and probe.status_code < 500:
                    raise PreparationFailure("provider_unavailable", stage="asset_probe")
            except PreparationFailure:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == 2:
                    raise PreparationFailure(
                        "provider_unavailable", stage="asset_signing", retryable=True
                    ) from exc
            if attempt < 2:
                delay = min(0.1 * (attempt + 1), max(0.0, deadline_monotonic - time.monotonic()))
                if delay:
                    time.sleep(delay)
        if signed_href is None or probe is None or probe.status_code not in {200, 206}:
            raise PreparationFailure("provider_unavailable", stage="asset_probe", retryable=True)
        if not _provider_host_allowed(signed_href):
            raise PreparationFailure("security_rejected", stage="asset_read")
        bytes_observed = len(probe.content)
        context = mp.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_rasterio_worker,
            args=(
                child,
                signed_href,
                target_grid.model_dump(mode="json"),
                resampling,
                max_window_pixels,
                _GDAL_CACHE_BYTES,
            ),
        )
        process.start()
        child.close()
        try:
            remaining = max(0.001, deadline_monotonic - time.monotonic())
            if not parent.poll(remaining):
                raise PreparationFailure("timeout", stage="asset_read", retryable=True)
            result = parent.recv()
        finally:
            _cleanup_child(process, parent)
    if not isinstance(result, Mapping) or "array" not in result:
        diagnostics = (
            {
                "reason": str(result.get("error", "child_read_failed"))[:120],
                "detail": str(result.get("message", ""))[:160],
            }
            if isinstance(result, Mapping)
            else {}
        )
        raise PreparationFailure(
            "invalid_raster_metadata", stage="asset_read", sanitized_diagnostics=diagnostics
        )
    return np.asarray(result["array"]), dict(result), bytes_observed


def _rasterize_aoi(aoi: TrustedAOI, target_grid: TargetGrid) -> np.ndarray:
    try:
        import rasterio
        from rasterio.env import set_proj_data_search_path
        from rasterio.features import geometry_mask
        from rasterio.transform import Affine
        from rasterio.warp import transform_geom

        set_proj_data_search_path(str(Path(rasterio.__file__).resolve().parent / "proj_data"))
        projected_geometry = transform_geom("EPSG:4326", target_grid.crs, aoi.geometry)
        return ~geometry_mask(
            [projected_geometry],
            out_shape=(target_grid.height, target_grid.width),
            transform=Affine(*target_grid.transform),
            invert=False,
        )
    except ImportError as exc:
        raise PreparationFailure("invalid_raster_metadata", stage="aoi_rasterize") from exc


def validate_mtl_metadata(payload: Mapping[str, Any]) -> dict[str, float]:
    """Validate the Level-2 MTL calibration values without retaining raw metadata."""

    root: Mapping[str, Any] = payload
    nested = payload.get("LANDSAT_METADATA_FILE")
    if isinstance(nested, Mapping):
        root = nested
    parameters = root.get("LEVEL2_SURFACE_REFLECTANCE_PARAMETERS")
    if not isinstance(parameters, Mapping):
        raise PreparationFailure("invalid_radiometry", stage="mtl_validation")
    values: dict[str, float] = {}
    for key in (
        "REFLECTANCE_MULT_BAND_4",
        "REFLECTANCE_ADD_BAND_4",
        "REFLECTANCE_MULT_BAND_5",
        "REFLECTANCE_ADD_BAND_5",
    ):
        try:
            values[key] = float(parameters[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise PreparationFailure("invalid_radiometry", stage="mtl_validation") from exc
    if values["REFLECTANCE_MULT_BAND_4"] <= 0 or values["REFLECTANCE_MULT_BAND_5"] <= 0:
        raise PreparationFailure("invalid_radiometry", stage="mtl_validation")
    return values


def validate_mtl_text(text: str) -> dict[str, float]:
    values: dict[str, float] = {}
    for key in (
        "REFLECTANCE_MULT_BAND_4",
        "REFLECTANCE_ADD_BAND_4",
        "REFLECTANCE_MULT_BAND_5",
        "REFLECTANCE_ADD_BAND_5",
    ):
        match = re.search(rf"^\s*{key}\s*=\s*([^\s]+)", text, flags=re.MULTILINE)
        if match is None:
            raise PreparationFailure("invalid_radiometry", stage="mtl_validation")
        try:
            values[key] = float(match.group(1))
        except ValueError as exc:
            raise PreparationFailure("invalid_radiometry", stage="mtl_validation") from exc
    if values["REFLECTANCE_MULT_BAND_4"] <= 0 or values["REFLECTANCE_MULT_BAND_5"] <= 0:
        raise PreparationFailure("invalid_radiometry", stage="mtl_validation")
    return values


def _fetch_mtl_values(
    href: str | None,
    *,
    client: httpx.Client,
    deadline_monotonic: float | None = None,
) -> dict[str, float] | None:
    if href is None:
        return None
    if not _provider_host_allowed(href):
        raise PreparationFailure("security_rejected", stage="mtl_validation")
    # MTL hrefs arrive from STAC and are treated as unsigned provider input.
    # A caller-supplied SAS/token query is rejected before it reaches the
    # signing endpoint.  The signed result is allowed to contain its short
    # lived SAS query, but its host is checked again before reading.
    _reject_query_credentials(href)
    response: httpx.Response | None = None
    for attempt in range(3):
        try:
            with _REMOTE_OPERATION_SEMAPHORE:
                signed_response = _http_get(
                    client,
                    SAS_SIGN_ENDPOINT,
                    deadline_monotonic=deadline_monotonic,
                    params={"href": href},
                )
                signed_response.raise_for_status()
                signed_payload = signed_response.json()
                signed_href = (
                    signed_payload.get("href") if isinstance(signed_payload, Mapping) else None
                )
                if not isinstance(signed_href, str) or not _provider_host_allowed(signed_href):
                    raise PreparationFailure("security_rejected", stage="mtl_signing")
                response = _http_get(
                    client,
                    signed_href,
                    deadline_monotonic=deadline_monotonic,
                    headers={"Range": "bytes=0-1048575"},
                )
            if response.status_code in {200, 206, 404}:
                break
            if response.status_code not in {429} and response.status_code < 500:
                break
        except PreparationFailure:
            raise
        except (httpx.HTTPError, ValueError):
            if attempt == 2:
                raise PreparationFailure("provider_unavailable", stage="mtl_read", retryable=True)
        if attempt < 2:
            time.sleep(min(0.1 * (attempt + 1), _remaining_seconds(deadline_monotonic)))
    if response is None:
        raise PreparationFailure("provider_unavailable", stage="mtl_read", retryable=True)
    if response.status_code == 404:
        return None
    if response.status_code not in {200, 206} or len(response.content) > 1_048_576:
        raise PreparationFailure(
            "provider_unavailable",
            stage="mtl_read",
            retryable=response.status_code == 429 or response.status_code >= 500,
        )
    if href.casefold().endswith(".json"):
        try:
            return validate_mtl_metadata(response.json())
        except ValueError as exc:
            raise PreparationFailure("invalid_radiometry", stage="mtl_validation") from exc
    return validate_mtl_text(response.content.decode("utf-8-sig", errors="strict"))


def _validate_expected_asset_metadata(asset: AssetIdentity, metadata: Mapping[str, Any]) -> None:
    """Bind trusted STAC projection evidence to the opened COG metadata."""

    expected_crs = asset.expected_crs
    actual_crs = metadata.get("crs")
    if expected_crs is not None and (actual_crs is None or str(actual_crs) != expected_crs):
        raise PreparationFailure(
            "grid_alignment_failed", stage="expected_crs", sanitized_diagnostics={"role": asset.key}
        )
    expected_transform = asset.expected_transform
    actual_transform = metadata.get("transform")
    if expected_transform is not None:
        if not isinstance(actual_transform, (tuple, list)) or len(actual_transform) != 6:
            raise PreparationFailure("grid_alignment_failed", stage="expected_transform")
        if any(
            not math.isclose(float(expected), float(actual), rel_tol=0, abs_tol=1e-6)
            for expected, actual in zip(expected_transform, actual_transform)
        ):
            raise PreparationFailure("grid_alignment_failed", stage="expected_transform")
    expected_width, expected_height = asset.expected_width, asset.expected_height
    actual_width, actual_height = metadata.get("width"), metadata.get("height")
    if expected_width is not None and expected_width != actual_width:
        raise PreparationFailure("grid_alignment_failed", stage="expected_shape")
    if expected_height is not None and expected_height != actual_height:
        raise PreparationFailure("grid_alignment_failed", stage="expected_shape")
    if asset.dtype is not None and str(metadata.get("dtype")) != asset.dtype:
        raise PreparationFailure("invalid_raster_metadata", stage="expected_dtype")
    if asset.nodata is not None:
        actual_nodata = metadata.get("nodata")
        if actual_nodata is None or not math.isclose(
            float(asset.nodata), float(actual_nodata), rel_tol=0, abs_tol=1e-9
        ):
            raise PreparationFailure("invalid_raster_metadata", stage="expected_nodata")


def _prepare_period(
    period: MonthlyPeriod,
    scenes: list[LandsatScene],
    aoi: TrustedAOI,
    target_grid: TargetGrid,
    limits: DiscoveryLimits,
    client: httpx.Client,
    deadline_monotonic: float,
) -> tuple[PreparedPeriodDataset, int, list[str]]:
    shape = (target_grid.height, target_grid.width)
    pixels = target_grid.width * target_grid.height
    # Retained A/B-safe working estimate: both period outputs (Red + NIR
    # float32, masks and source indices) remain live while the second period
    # is prepared, plus source/QA working buffers and rasterization scratch.
    estimated_array_bytes = pixels * 32
    array_budget = min(limits.array_cache_bytes, _TOTAL_ARRAY_GDAL_BUDGET - _GDAL_CACHE_BYTES)
    if estimated_array_bytes > array_budget:
        raise PreparationFailure(
            "read_budget_exceeded",
            stage="array_cache_preallocation",
            counts={
                "estimated_array_bytes": estimated_array_bytes,
                "array_budget_bytes": array_budget,
            },
        )
    aoi_mask = _rasterize_aoi(aoi, target_grid).astype(bool)
    aoi_pixels = int(aoi_mask.sum())
    if not aoi_pixels:
        raise PreparationFailure("insufficient_preparation_coverage", stage="aoi_rasterize")
    red_out = np.full(shape, np.nan, dtype=np.float32)
    nir_out = np.full(shape, np.nan, dtype=np.float32)
    source_index = np.full(shape, -1, dtype=np.int16)
    filled = np.zeros(shape, dtype=bool)
    warnings: list[str] = []
    bytes_observed = 0
    provenance_scenes: list[dict[str, Any]] = []
    for scene_index, scene in enumerate(scenes):
        if time.monotonic() >= deadline_monotonic:
            raise PreparationFailure("timeout", stage="period_preparation", retryable=True)
        try:
            red_dn, red_meta, red_bytes = _read_asset(
                scene.assets["red"].href,
                target_grid,
                resampling="bilinear",
                client=client,
                deadline_seconds=max(0.1, deadline_monotonic - time.monotonic()),
                max_window_pixels=limits.max_window_pixels,
                deadline_monotonic=deadline_monotonic,
            )
            nir_dn, nir_meta, nir_bytes = _read_asset(
                scene.assets["nir08"].href,
                target_grid,
                resampling="bilinear",
                client=client,
                deadline_seconds=max(0.1, deadline_monotonic - time.monotonic()),
                max_window_pixels=limits.max_window_pixels,
                deadline_monotonic=deadline_monotonic,
            )
            qa_pixel, qa_meta, qa_bytes = _read_asset(
                scene.assets["qa_pixel"].href,
                target_grid,
                resampling="nearest",
                client=client,
                deadline_seconds=max(0.1, deadline_monotonic - time.monotonic()),
                max_window_pixels=limits.max_window_pixels,
                deadline_monotonic=deadline_monotonic,
            )
            qa_radsat, radsat_meta, radsat_bytes = _read_asset(
                scene.assets["qa_radsat"].href,
                target_grid,
                resampling="nearest",
                client=client,
                deadline_seconds=max(0.1, deadline_monotonic - time.monotonic()),
                max_window_pixels=limits.max_window_pixels,
                deadline_monotonic=deadline_monotonic,
            )
            aerosol = None
            aerosol_meta: dict[str, Any] | None = None
            aerosol_bytes = 0
            if "qa_aerosol" in scene.assets:
                aerosol, aerosol_meta, aerosol_bytes = _read_asset(
                    scene.assets["qa_aerosol"].href,
                    target_grid,
                    resampling="nearest",
                    client=client,
                    deadline_seconds=max(0.1, deadline_monotonic - time.monotonic()),
                    max_window_pixels=limits.max_window_pixels,
                    deadline_monotonic=deadline_monotonic,
                )
        except PreparationFailure:
            raise
        except Exception as exc:
            raise PreparationFailure(
                "provider_unavailable", stage="asset_read", retryable=True
            ) from exc
        bytes_observed += red_bytes + nir_bytes + qa_bytes + radsat_bytes + aerosol_bytes
        try:
            mtl_values = _fetch_mtl_values(
                scene.mtl_href, client=client, deadline_monotonic=deadline_monotonic
            )
        except PreparationFailure:
            raise
        if mtl_values is not None:
            if (
                abs(mtl_values["REFLECTANCE_MULT_BAND_4"] - float(scene.assets["red"].scale or 0))
                > 1e-12
                or abs(
                    mtl_values["REFLECTANCE_ADD_BAND_4"] - float(scene.assets["red"].offset or 0)
                )
                > 1e-9
                or abs(
                    mtl_values["REFLECTANCE_MULT_BAND_5"] - float(scene.assets["nir08"].scale or 0)
                )
                > 1e-12
                or abs(
                    mtl_values["REFLECTANCE_ADD_BAND_5"] - float(scene.assets["nir08"].offset or 0)
                )
                > 1e-9
            ):
                raise PreparationFailure("invalid_radiometry", stage="mtl_validation")
        else:
            warnings.append(f"period_{period.period_id}:mtl_asset_unavailable")
        red_asset = scene.assets["red"]
        nir_asset = scene.assets["nir08"]
        for role, array, metadata in (
            ("red", red_dn, red_meta),
            ("nir08", nir_dn, nir_meta),
            ("qa_pixel", qa_pixel, qa_meta),
            ("qa_radsat", qa_radsat, radsat_meta),
        ):
            if array.shape != shape or metadata.get("count", 1) != 1:
                raise PreparationFailure("invalid_raster_metadata", stage="raster_metadata")
            transform = metadata.get("transform")
            if (
                not isinstance(transform, (tuple, list))
                or len(transform) != 6
                or not all(math.isfinite(float(value)) for value in transform)
            ):
                raise PreparationFailure("invalid_raster_metadata", stage="raster_metadata")
            _validate_expected_asset_metadata(scene.assets[role], metadata)
            if metadata.get("crs") is None or str(metadata["crs"]) != str(scene.source_crs):
                raise PreparationFailure("grid_alignment_failed", stage="alignment")
            if not np.issubdtype(array.dtype, np.integer):
                raise PreparationFailure("invalid_raster_metadata", stage="raster_metadata")
        if aerosol is not None:
            if (
                aerosol_meta is None
                or aerosol.shape != shape
                or not np.issubdtype(aerosol.dtype, np.integer)
            ):
                raise PreparationFailure("invalid_raster_metadata", stage="aerosol_metadata")
            if aerosol_meta.get("crs") is None or str(aerosol_meta["crs"]) != str(scene.source_crs):
                raise PreparationFailure("grid_alignment_failed", stage="aerosol_crs")
            _validate_expected_asset_metadata(scene.assets["qa_aerosol"], aerosol_meta)
        if red_meta.get("transform") != nir_meta.get("transform"):
            raise PreparationFailure("grid_alignment_failed", stage="alignment")
        if (red_meta.get("width"), red_meta.get("height")) != (
            nir_meta.get("width"),
            nir_meta.get("height"),
        ):
            raise PreparationFailure("grid_alignment_failed", stage="alignment")
        source_dims = (red_meta.get("width"), red_meta.get("height"))
        for metadata in (qa_meta, radsat_meta):
            if metadata.get("width") is not None and metadata.get("height") is not None:
                if (metadata["width"], metadata["height"]) != source_dims:
                    raise PreparationFailure("grid_alignment_failed", stage="alignment")
            if metadata.get("transform") != red_meta.get("transform"):
                raise PreparationFailure("grid_alignment_failed", stage="alignment")
        if aerosol_meta is not None:
            if aerosol_meta.get("transform") != red_meta.get("transform"):
                raise PreparationFailure("grid_alignment_failed", stage="aerosol_alignment")
            if aerosol_meta.get("width") is not None and aerosol_meta.get("height") is not None:
                if (aerosol_meta["width"], aerosol_meta["height"]) != source_dims:
                    raise PreparationFailure("grid_alignment_failed", stage="aerosol_alignment")
        if red_meta.get("width") is not None and red_meta.get("height") is not None:
            if red_meta["width"] <= 0 or red_meta["height"] <= 0:
                raise PreparationFailure("invalid_raster_metadata", stage="raster_metadata")
        for asset, metadata in ((red_asset, red_meta), (nir_asset, nir_meta)):
            # Some Landsat COGs expose raw DN with embedded scale=1/offset=0;
            # physical calibration remains explicitly supplied by trusted
            # STAC/MTL evidence in that case.
            raw_identity = (
                metadata.get("scale") is not None
                and metadata.get("offset") is not None
                and math.isclose(float(metadata["scale"]), 1.0, rel_tol=0, abs_tol=1e-12)
                and math.isclose(float(metadata["offset"]), 0.0, rel_tol=0, abs_tol=1e-12)
            )
            if raw_identity and (
                asset.scale is None
                or asset.offset is None
                or math.isclose(float(asset.scale), 1.0, rel_tol=0, abs_tol=1e-12)
                or math.isclose(float(asset.offset), 0.0, rel_tol=0, abs_tol=1e-12)
            ):
                raise PreparationFailure("invalid_radiometry", stage="mtl_required")
            if (
                asset.scale is not None
                and metadata.get("scale") is not None
                and not math.isclose(float(metadata["scale"]), 1.0, rel_tol=0, abs_tol=1e-12)
            ):
                if not math.isclose(
                    float(asset.scale), float(metadata["scale"]), rel_tol=0, abs_tol=1e-12
                ):
                    raise PreparationFailure("invalid_radiometry", stage="scale")
            if (
                metadata.get("scale") is not None
                and math.isclose(float(metadata["scale"]), 1.0, rel_tol=0, abs_tol=1e-12)
                and mtl_values is None
            ):
                raise PreparationFailure("invalid_radiometry", stage="mtl_required")
            if (
                asset.offset is not None
                and metadata.get("offset") is not None
                and not math.isclose(float(metadata["offset"]), 0.0, rel_tol=0, abs_tol=1e-12)
            ):
                if not math.isclose(
                    float(asset.offset), float(metadata["offset"]), rel_tol=0, abs_tol=1e-9
                ):
                    raise PreparationFailure("invalid_radiometry", stage="offset")
            if (
                metadata.get("offset") is not None
                and math.isclose(float(metadata["offset"]), 0.0, rel_tol=0, abs_tol=1e-12)
                and mtl_values is None
            ):
                raise PreparationFailure("invalid_radiometry", stage="mtl_required")
        if (
            red_meta.get("dtype") != "uint16"
            or nir_meta.get("dtype") != "uint16"
            or red_asset.dtype not in {None, "uint16"}
            or nir_asset.dtype not in {None, "uint16"}
            or red_asset.scale is None
            or red_asset.offset is None
            or nir_asset.scale is None
            or nir_asset.offset is None
        ):
            raise PreparationFailure("invalid_radiometry", stage="radiometry")
        if red_meta.get("crs") != nir_meta.get("crs"):
            raise PreparationFailure("grid_alignment_failed", stage="alignment")
        red, red_valid, red_out_nominal = apply_radiometry(
            red_dn,
            scale=float(red_asset.scale),
            offset=float(red_asset.offset),
            nodata=red_asset.nodata if red_asset.nodata is not None else red_meta.get("nodata", 0),
        )
        nir, nir_valid, nir_out_nominal = apply_radiometry(
            nir_dn,
            scale=float(nir_asset.scale),
            offset=float(nir_asset.offset),
            nodata=nir_asset.nodata if nir_asset.nodata is not None else nir_meta.get("nodata", 0),
        )
        quality = (
            red_valid & nir_valid & qa_pixel_valid_mask(qa_pixel) & qa_radsat_valid_mask(qa_radsat)
        )
        quality &= aoi_mask
        fill = quality & ~filled
        red_out[fill] = red[fill]
        nir_out[fill] = nir[fill]
        source_index[fill] = np.int16(scene_index)
        filled[fill] = True
        if np.any((red_out_nominal | nir_out_nominal) & quality):
            warnings.append(f"period_{period.period_id}:out_of_nominal_sr_range")
        provenance_scenes.append(
            {
                "item_id": scene.item_id,
                "acquisition_date": scene.acquisition_datetime.date().isoformat(),
                "asset_key_map": dict(scene.asset_key_map),
                "asset_identity_hashes": {
                    role: asset.identity_hash for role, asset in scene.assets.items()
                },
                "mtl_validated": mtl_values is not None,
                "aerosol_diagnostics": (
                    {"available": True, **qa_aerosol_diagnostics(aerosol)}
                    if aerosol is not None
                    else {"available": False}
                ),
            }
        )
        # Quality is evidence from the actual AOI pixels.  Do not read more
        # same-month scenes once the provisional gate is met.
        if int(filled.sum()) * 100 >= 70 * aoi_pixels:
            break
    valid_pixels = int(filled.sum())
    array_bytes = sum(array.nbytes for array in (red_out, nir_out, filled, aoi_mask, source_index))
    if array_bytes > array_budget:
        raise PreparationFailure(
            "read_budget_exceeded", stage="array_cache", counts={"array_bytes": array_bytes}
        )
    coverage = Coverage(
        aoi_rasterized_pixels=aoi_pixels,
        preparation_valid_pixels=valid_pixels,
        preparation_coverage_pct=100.0 * valid_pixels / aoi_pixels,
        scene_count=len(provenance_scenes),
    )
    if coverage.preparation_coverage_pct < 70.0:
        raise PreparationFailure(
            "insufficient_preparation_coverage",
            stage="quality_gate",
            counts={"aoi_pixels": aoi_pixels, "preparation_pixels": valid_pixels},
        )
    dataset = PreparedPeriodDataset(
        period_id=period.period_id,
        requested_period=period,
        red_reflectance=red_out,
        nir_reflectance=nir_out,
        preparation_valid_mask=filled,
        aoi_mask=aoi_mask,
        source_scene_index=source_index,
        target_grid=target_grid,
        coverage=coverage,
        provenance={
            "scene_ids": [scene["item_id"] for scene in provenance_scenes],
            "acquisition_dates": [scene["acquisition_date"] for scene in provenance_scenes],
            "asset_key_map": [scene["asset_key_map"] for scene in provenance_scenes],
            "asset_identity_hashes": [
                scene["asset_identity_hashes"] for scene in provenance_scenes
            ],
            "qa_policy": "QA_PIXEL fill/dilated/high-confidence cloud-cirrus-shadow-snow; QA_RADSAT B4/B5/terrain",
            "scale_offset": {
                "red": [float(red_asset.scale), float(red_asset.offset)],
                "nir08": [float(nir_asset.scale), float(nir_asset.offset)],
            },
            "resampling": {"reflectance": "bilinear", "qa": "nearest"},
            "composite_policy": "deterministic priority-fill",
            "source_hashes": [scene["asset_identity_hashes"] for scene in provenance_scenes],
        },
    )
    return dataset, bytes_observed, warnings


def _hard_limits_evidence(limits: DiscoveryLimits) -> dict[str, int | float | bool]:
    """Return the frozen per-run resource contract without a global claim."""

    array_bytes = min(limits.array_cache_bytes, _TOTAL_ARRAY_GDAL_BUDGET - _GDAL_CACHE_BYTES)
    return {
        "max_target_pixels": limits.max_target_pixels,
        "max_window_pixels": limits.max_window_pixels,
        "max_concurrent_remote_asset_operations": limits.max_concurrent_remote_asset_operations,
        "request_deadline_seconds": limits.request_deadline_seconds,
        "temporary_disk_bytes": 0,
        "array_cache_bytes": array_bytes,
        "gdal_cache_bytes": _GDAL_CACHE_BYTES,
        "array_plus_gdal_cache_bytes": array_bytes + _GDAL_CACHE_BYTES,
        "array_gdal_budget_bytes": _TOTAL_ARRAY_GDAL_BUDGET,
        "array_gdal_budget_scope_per_run": True,
        "process_global_array_gdal_cap": False,
        "artifact_bytes": 0,
        "retry_limit": 3,
        "staged_temporary_writes": False,
    }


def prepare_landsat_pair(
    aoi: TrustedAOI,
    periods: PeriodPair,
    selected: SelectedScenePair,
    limits: DiscoveryLimits | None = None,
    *,
    client: httpx.Client | None = None,
    deadline_monotonic: float | None = None,
) -> PreparedPeriodPair:
    limits = limits or DiscoveryLimits()
    started = time.monotonic()
    deadline_monotonic = _operation_deadline(limits, deadline_monotonic)
    own_client = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    try:
        if (
            not selected.period_a
            or not selected.period_b
            or len(selected.period_a) > limits.max_selected_scenes
            or len(selected.period_b) > limits.max_selected_scenes
            or len(selected.period_a) > 3
            or len(selected.period_b) > 3
        ):
            raise PreparationFailure("read_budget_exceeded", stage="scene_selection")
        for period, period_scenes in (
            (periods.period_a, selected.period_a),
            (periods.period_b, selected.period_b),
        ):
            if any(
                not (period.start_utc <= scene.acquisition_datetime < period.end_utc)
                for scene in period_scenes
            ):
                raise PreparationFailure("no_candidate", stage="scene_selection")
        crs = selected.period_a[0].source_crs
        if any(scene.source_crs != crs for scene in selected.period_a + selected.period_b):
            raise PreparationFailure("grid_alignment_failed", stage="grid")
        grid = _target_grid(aoi, crs, limits)
        period_a, bytes_a, warnings_a = _prepare_period(
            periods.period_a, selected.period_a, aoi, grid, limits, http, deadline_monotonic
        )
        period_b, bytes_b, warnings_b = _prepare_period(
            periods.period_b, selected.period_b, aoi, grid, limits, http, deadline_monotonic
        )
    finally:
        if own_client:
            http.close()
    common = period_a.aoi_mask & period_a.preparation_valid_mask & period_b.preparation_valid_mask
    if not np.any(common):
        raise PreparationFailure("no_common_preparation_pixels", stage="quality_gate")
    elapsed_ms = (time.monotonic() - started) * 1000
    warnings = list(dict.fromkeys(warnings_a + warnings_b))
    warnings.append("bytes_observed_is_range_probe_lower_bound")
    if bytes_a + bytes_b > 256 * 1024 * 1024:
        warnings.append("bytes_observed_above_256MiB_warning_threshold")
    return PreparedPeriodPair(
        period_a=period_a,
        period_b=period_b,
        common_preparation_valid_mask=common.astype(bool),
        pair_grid=grid,
        hard_limits_applied=_hard_limits_evidence(limits),
        operational_metrics={
            "bytes_observed": bytes_a + bytes_b,
            "elapsed_ms": round(elapsed_ms, 2),
            "aoi_pixels": period_a.coverage.aoi_rasterized_pixels,
            "common_preparation_pixels": int(common.sum()),
        },
        best_effort_warnings=tuple(warnings),
    )


def prepare_landsat_operation(
    aoi: TrustedAOI,
    periods: PeriodPair,
    limits: DiscoveryLimits | None = None,
    *,
    client: httpx.Client | None = None,
) -> tuple[DiscoveryReport, SelectedScenePair, PreparedPeriodPair]:
    """Run discovery and preparation under one authoritative operation deadline."""

    limits = limits or DiscoveryLimits()
    deadline_monotonic = _operation_deadline(limits, None)
    report = discover_landsat(
        aoi, periods, limits, client=client, deadline_monotonic=deadline_monotonic
    )
    selected = select_landsat_scenes(report)
    prepared = prepare_landsat_pair(
        aoi,
        periods,
        selected,
        limits,
        client=client,
        deadline_monotonic=deadline_monotonic,
    )
    return report, selected, prepared


__all__ = [
    "AssetIdentity",
    "CONTRACT_VERSION",
    "DiscoveryLimits",
    "DiscoveryReport",
    "LANDSAT_COLLECTION",
    "LANDSAT_PROVIDER",
    "LANDSAT_STAC_ENDPOINT",
    "LandsatScene",
    "MonthlyPeriod",
    "PeriodPair",
    "PreparedPeriodDataset",
    "PreparedPeriodPair",
    "PreparationFailure",
    "SelectedScenePair",
    "SelectionPolicy",
    "TargetGrid",
    "apply_radiometry",
    "default_period_pair",
    "discover_landsat",
    "prepare_landsat_pair",
    "prepare_landsat_operation",
    "qa_pixel_valid_mask",
    "qa_aerosol_diagnostics",
    "qa_radsat_valid_mask",
    "select_landsat_scenes",
    "validate_mtl_metadata",
    "validate_mtl_text",
]
