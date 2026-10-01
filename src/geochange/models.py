from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> "Period":
        if self.end < self.start:
            raise ValueError("period end must not precede start")
        if (self.end - self.start).days > 93:
            raise ValueError("period must be at most 94 days")
        return self


class GeoChangeTask(BaseModel):
    """Only the bounded inputs needed by the vegetation-change MVP."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    analysis_type: Literal["vegetation_change"] = "vegetation_change"
    version: Literal["1"] = "1"
    aoi_key: Literal["wuhan_east_lake"] = "wuhan_east_lake"
    period_a: Period
    period_b: Period
    cloud_threshold: float = Field(default=30.0, ge=0.0, le=100.0)
    decline_threshold: float = Field(default=-0.2, ge=-1.0, le=0.0)
    cloud_threshold_source: Literal["user_text", "server_default"] = "server_default"
    decline_threshold_source: Literal["user_text", "server_default"] = "server_default"
    data_mode: Literal["real_online", "cached_real_metadata", "local_real_raster_fixture"] = (
        "local_real_raster_fixture"
    )


class GeoChangeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["geochange.v1"] = "geochange.v1"
    analysis_type: Literal["vegetation_change"] = "vegetation_change"
    mode: str = Field(max_length=40)
    summary: str = Field(max_length=500)
    metrics: dict[str, float | int | str | bool]
    provenance: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, str] = Field(default_factory=dict)
    verifier_status: Literal["passed", "failed"]
