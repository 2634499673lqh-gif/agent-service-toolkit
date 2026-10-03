"""Product-facing GeoChange API contracts."""

from typing import Any

from pydantic import BaseModel, Field


class GeoChangeCapability(BaseModel):
    indicator: str
    label: str
    analysis_type: str
    periods: list[str]
    data_source: str
    scientific_limit: str


class GeoChangeCapabilitiesResponse(BaseModel):
    capabilities: list[GeoChangeCapability]
    aoi_label: str
    data_availability: str


class GeoChangeMapResponse(BaseModel):
    indicator: str
    period: dict[str, str]
    aoi_label: str
    data_source: str
    bounds: list[float] | None = Field(default=None, min_length=4, max_length=4)
    crs: str | None = None
    valid_value_summary: dict[str, Any] | None = None
    artifacts: dict[str, str] = {}
    scientific_limit: str
