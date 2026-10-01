"""Validated execution intent, persisted only by server confirmation."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from geochange.models import GeoChangeTask, Period


class IntentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: Literal["Sentinel-2"] = "Sentinel-2"
    cloud_threshold: float = Field(default=30.0, ge=0, le=100, allow_inf_nan=False)
    decline_threshold: float = Field(default=-0.2, ge=-1, le=0, allow_inf_nan=False)


class ConfirmedIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["vegetation_change"]
    indicator: Literal["NDVI"]
    analysis_area: Literal["武汉东湖", "wuhan_east_lake"]
    period_a: Period
    period_b: Period
    parameters: IntentParameters = Field(default_factory=IntentParameters)

    @model_validator(mode="after")
    def comparison_is_ordered(self) -> "ConfirmedIntent":
        if self.period_a.end >= self.period_b.start:
            raise ValueError("comparison periods must be distinct, ordered and non-overlapping")
        if len(json.dumps(self.model_dump(mode="json")).encode("utf-8")) > 4096:
            raise ValueError("confirmed intent is too large")
        return self

    def runtime_task(self) -> GeoChangeTask:
        return GeoChangeTask(
            analysis_type=self.analysis_type,
            aoi_key="wuhan_east_lake",
            period_a=self.period_a,
            period_b=self.period_b,
            cloud_threshold=self.parameters.cloud_threshold,
            decline_threshold=self.parameters.decline_threshold,
            cloud_threshold_source="user_text",
            decline_threshold_source="user_text",
        )
