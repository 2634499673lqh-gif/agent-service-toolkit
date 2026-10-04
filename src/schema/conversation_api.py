"""Contracts for the product-facing conversational TaskPilot entry point."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from geochange.models import Period


class TaskProposal(BaseModel):
    """Server-readable proposal; it is not an execution command."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1, max_length=2000)
    analysis_area: str | None = Field(default=None, max_length=255)
    analysis_type: str = Field(default="vegetation_change", max_length=64)
    indicator: str = Field(default="NDVI", max_length=64)
    period_a: Period | None = None
    period_b: Period | None = None
    required_parameters: dict[str, str] = Field(default_factory=dict)

    @field_validator("title", "description", "analysis_type", "indicator")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("proposal text must not be blank")
        return value.strip()


class ConversationRequest(BaseModel):
    # Task descriptions are bounded to the same supported size.  Keeping the
    # boundary here prevents a valid conversation request from becoming an
    # unhandled persistence error later in the flow.
    message: str = Field(min_length=1, max_length=2000)
    context: list[dict[str, str]] = Field(default_factory=list, max_length=8)

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class LLMIntent(BaseModel):
    """Untrusted, bounded model output; it is never an execution command."""

    model_config = ConfigDict(extra="ignore")

    intent: Literal["new_analysis", "history", "result", "chat", "clarification", "unsupported"]
    response: str = Field(default="", max_length=1000)
    title: str = Field(default="遥感变化分析", max_length=255)
    description: str = Field(default="", max_length=2000)
    analysis_area: str | None = Field(default=None, max_length=255)
    indicator: Literal["NDVI", "NDWI", "NDBI"] | None = None
    analysis_type: Literal["vegetation_change", "water_change", "urban_change"] | None = None
    period_a: Period | None = None
    period_b: Period | None = None
    required_parameters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_provider_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        intent_aliases = {
            "chitchat": "chat",
            "new_task": "new_analysis",
            "analysis": "new_analysis",
            "history_query": "history",
            "result_query": "result",
        }
        if isinstance(normalized.get("intent"), str):
            normalized["intent"] = intent_aliases.get(normalized["intent"], normalized["intent"])
        if "response" not in normalized and isinstance(normalized.get("reply"), str):
            normalized["response"] = normalized["reply"]
        slots = normalized.get("slots")
        if isinstance(slots, dict):
            for key in (
                "title",
                "description",
                "analysis_area",
                "indicator",
                "analysis_type",
                "period_a",
                "period_b",
                "required_parameters",
            ):
                if key not in normalized and key in slots:
                    normalized[key] = slots[key]
        if "analysis_area" not in normalized and isinstance(normalized.get("location"), str):
            normalized["analysis_area"] = normalized["location"]
        if "indicator" not in normalized and isinstance(normalized.get("index"), str):
            normalized["indicator"] = normalized["index"]
        if "period_a" not in normalized and isinstance(normalized.get("time_ranges"), list):
            ranges = normalized["time_ranges"]
            if len(ranges) >= 2:
                normalized["period_a"], normalized["period_b"] = (
                    {
                        "start": item.get("start", item.get("start_date")),
                        "end": item.get("end", item.get("end_date")),
                    }
                    if isinstance(item, dict)
                    else item
                    for item in ranges[:2]
                )
        temporal = normalized.get("temporal")
        if isinstance(temporal, dict) and "period_a" not in normalized:
            baseline, comparison = temporal.get("baseline"), temporal.get("comparison")
            if (
                isinstance(baseline, str)
                and "/" in baseline
                and isinstance(comparison, str)
                and "/" in comparison
            ):
                a_start, a_end = baseline.split("/", 1)
                b_start, b_end = comparison.split("/", 1)
                normalized["period_a"] = {"start": a_start, "end": a_end}
                normalized["period_b"] = {"start": b_start, "end": b_end}
        if normalized.get("analysis_type") not in {
            "vegetation_change",
            "water_change",
            "urban_change",
        }:
            normalized["analysis_type"] = {
                "NDVI": "vegetation_change",
                "NDWI": "water_change",
                "NDBI": "urban_change",
            }.get(str(normalized.get("indicator")), "vegetation_change")
        normalized.pop("reply", None)
        normalized.pop("sub_intent", None)
        normalized.pop("confidence", None)
        normalized.pop("missing_fields", None)
        normalized.pop("clarification_question", None)
        normalized.pop("clarification", None)
        normalized.pop("parameters", None)
        for key in (
            "location",
            "sensor",
            "index",
            "time_ranges",
            "display_result",
            "clarification_required",
        ):
            normalized.pop(key, None)
        normalized.pop("temporal", None)
        normalized.pop("slots", None)
        return normalized


class ResultInterpretation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    text: str = Field(min_length=1, max_length=2000)
    evidence_status: Literal["verified", "limited", "unavailable"]
    limitations: list[str] = Field(default_factory=list, max_length=6)


class InterpretationRequest(BaseModel):
    question: str = Field(default="", max_length=500)


class ConfirmTaskRequest(BaseModel):
    proposal: TaskProposal


class ConversationTaskSummary(BaseModel):
    task_id: UUID
    title: str
    analysis_type: str | None = None
    status: str
    created_at: datetime
    runs: list[dict[str, object]] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    kind: str
    message: str
    proposal: TaskProposal | None = None
    tasks: list[ConversationTaskSummary] = Field(default_factory=list)
    result: dict[str, object] | None = None
    missing_fields: list[str] = Field(default_factory=list)


__all__ = [
    "ConfirmTaskRequest",
    "ConversationRequest",
    "LLMIntent",
    "ResultInterpretation",
    "InterpretationRequest",
    "ConversationResponse",
    "ConversationTaskSummary",
    "TaskProposal",
]
