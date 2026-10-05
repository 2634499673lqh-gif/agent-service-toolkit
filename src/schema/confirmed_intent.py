"""Validated execution intent, persisted only by server confirmation."""

import calendar
import json
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geochange.models import GeoChangeTask, Period


class IntentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["Sentinel-2"] = "Sentinel-2"
    cloud_threshold: float = Field(default=30.0, ge=0, le=100, allow_inf_nan=False)
    decline_threshold: float = Field(default=-0.2, ge=-1, le=0, allow_inf_nan=False)


class WaterIntentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["Sentinel-2"] = "Sentinel-2"
    cloud_threshold: float = Field(default=30.0, ge=0, le=100, allow_inf_nan=False)


class UrbanIntentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["Sentinel-2"] = "Sentinel-2"
    cloud_threshold: float = Field(default=30.0, ge=0, le=100, allow_inf_nan=False)


class LandsatIntentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["Landsat-8/9"] = "Landsat-8/9"
    collection: Literal["landsat-c2-l2"] = "landsat-c2-l2"
    data_mode: Literal["real_stac_landsat_local"] = "real_stac_landsat_local"
    cloud_threshold: float = Field(default=100.0, ge=0, le=100, allow_inf_nan=False)


class ConfirmedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["vegetation_change", "water_change", "urban_change"]
    indicator: Literal["NDVI", "NDWI", "NDBI"]
    analysis_area: Literal[
        "武汉东湖", "wuhan_east_lake", "武汉市江汉区", "jianghan_district_420103"
    ]
    period_a: Period
    period_b: Period
    parameters: (
        IntentParameters | WaterIntentParameters | UrbanIntentParameters | LandsatIntentParameters
    ) = Field(default_factory=IntentParameters)
    data_mode: Literal[
        "real_online",
        "cached_real_metadata",
        "local_real_raster_fixture",
        "real_stac_landsat_local",
    ] = "local_real_raster_fixture"

    @model_validator(mode="before")
    @classmethod
    def parse_parameters_for_indicator(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        raw = values.get("parameters")
        if isinstance(
            raw,
            (
                IntentParameters,
                WaterIntentParameters,
                UrbanIntentParameters,
                LandsatIntentParameters,
            ),
        ):
            return values
        if values.get("analysis_area") in {"武汉市江汉区", "jianghan_district_420103"}:
            values["parameters"] = LandsatIntentParameters.model_validate(raw or {})
            values["data_mode"] = "real_stac_landsat_local"
        elif values.get("analysis_type") == "water_change":
            water_values = dict(raw or {})
            if "source" in water_values and "cloud_threshold" in water_values:
                water_values.pop("decline_threshold", None)
            values["parameters"] = WaterIntentParameters.model_validate(water_values)
        elif values.get("analysis_type") == "urban_change":
            urban_values = dict(raw or {})
            if "source" in urban_values and "cloud_threshold" in urban_values:
                urban_values.pop("decline_threshold", None)
            values["parameters"] = UrbanIntentParameters.model_validate(urban_values)
        else:
            values["parameters"] = IntentParameters.model_validate(raw or {})
        return values

    @model_validator(mode="after")
    def comparison_is_ordered(self) -> "ConfirmedIntent":
        if self.period_a.end >= self.period_b.start:
            raise ValueError("comparison periods must be distinct, ordered and non-overlapping")
        if self.analysis_area in {"武汉市江汉区", "jianghan_district_420103"}:
            for period in (self.period_a, self.period_b):
                if period.start.year not in {2023, 2024, 2025}:
                    raise ValueError("Jianghan Landsat periods must be within 2023-2025")
                if (
                    period.start.day != 1
                    or period.end.day != calendar.monthrange(period.end.year, period.end.month)[1]
                ):
                    raise ValueError("Jianghan Landsat periods must be complete calendar months")
                if period.start.year != period.end.year or period.start.month != period.end.month:
                    raise ValueError("Jianghan Landsat periods must stay within one calendar month")
        if len(json.dumps(self.model_dump(mode="json")).encode("utf-8")) > 4096:
            raise ValueError("confirmed intent is too large")
        if (self.analysis_type, self.indicator) == ("vegetation_change", "NDVI"):
            if self.analysis_area in {"武汉市江汉区", "jianghan_district_420103"}:
                if (
                    not isinstance(self.parameters, LandsatIntentParameters)
                    or self.data_mode != "real_stac_landsat_local"
                ):
                    raise ValueError("Jianghan NDVI intent requires Landsat local execution")
            elif not isinstance(self.parameters, IntentParameters):
                raise ValueError("NDVI intent parameters are invalid")
        elif (self.analysis_type, self.indicator) == ("water_change", "NDWI"):
            if not isinstance(self.parameters, WaterIntentParameters):
                raise ValueError("NDWI intent parameters are invalid")
        elif (self.analysis_type, self.indicator) == ("urban_change", "NDBI"):
            if not isinstance(self.parameters, UrbanIntentParameters):
                raise ValueError("NDBI intent parameters are invalid")
        else:
            raise ValueError("analysis type and indicator do not match")
        return self

    def runtime_task(self) -> GeoChangeTask:
        values: dict[str, object] = {
            "analysis_type": self.analysis_type,
            "indicator": self.indicator,
            "aoi_key": "jianghan_district_420103"
            if self.analysis_area in {"武汉市江汉区", "jianghan_district_420103"}
            else "wuhan_east_lake",
            "period_a": self.period_a,
            "period_b": self.period_b,
            "cloud_threshold": self.parameters.cloud_threshold,
            "cloud_threshold_source": "server_default"
            if isinstance(self.parameters, LandsatIntentParameters)
            else "user_text",
            "data_mode": self.data_mode,
        }
        if isinstance(self.parameters, IntentParameters):
            values.update(
                decline_threshold=self.parameters.decline_threshold,
                decline_threshold_source="user_text",
            )
        else:
            values.update(decline_threshold=None, decline_threshold_source=None)
        return GeoChangeTask(**cast(Any, values))
