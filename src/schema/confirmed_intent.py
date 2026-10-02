"""Validated execution intent, persisted only by server confirmation."""

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


class ConfirmedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["vegetation_change", "water_change"]
    indicator: Literal["NDVI", "NDWI"]
    analysis_area: Literal["武汉东湖", "wuhan_east_lake"]
    period_a: Period
    period_b: Period
    parameters: IntentParameters | WaterIntentParameters = Field(default_factory=IntentParameters)

    @model_validator(mode="before")
    @classmethod
    def parse_parameters_for_indicator(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        raw = values.get("parameters")
        if isinstance(raw, (IntentParameters, WaterIntentParameters)):
            return values
        if values.get("analysis_type") == "water_change":
            values["parameters"] = WaterIntentParameters.model_validate(raw or {})
        else:
            values["parameters"] = IntentParameters.model_validate(raw or {})
        return values

    @model_validator(mode="after")
    def comparison_is_ordered(self) -> "ConfirmedIntent":
        if self.period_a.end >= self.period_b.start:
            raise ValueError("comparison periods must be distinct, ordered and non-overlapping")
        if len(json.dumps(self.model_dump(mode="json")).encode("utf-8")) > 4096:
            raise ValueError("confirmed intent is too large")
        if (self.analysis_type, self.indicator) == ("vegetation_change", "NDVI"):
            if not isinstance(self.parameters, IntentParameters):
                raise ValueError("NDVI intent parameters are invalid")
        elif (self.analysis_type, self.indicator) == ("water_change", "NDWI"):
            if not isinstance(self.parameters, WaterIntentParameters):
                raise ValueError("NDWI intent parameters are invalid")
        else:
            raise ValueError("analysis type and indicator do not match")
        return self

    def runtime_task(self) -> GeoChangeTask:
        values: dict[str, object] = {
            "analysis_type": self.analysis_type,
            "indicator": self.indicator,
            "aoi_key": "wuhan_east_lake",
            "period_a": self.period_a,
            "period_b": self.period_b,
            "cloud_threshold": self.parameters.cloud_threshold,
            "cloud_threshold_source": "user_text",
        }
        if isinstance(self.parameters, IntentParameters):
            values.update(
                decline_threshold=self.parameters.decline_threshold,
                decline_threshold_source="user_text",
            )
        else:
            values.update(decline_threshold=None, decline_threshold_source=None)
        return GeoChangeTask(**cast(Any, values))
