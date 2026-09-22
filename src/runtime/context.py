"""Bounded, checkpoint-safe capability context for Phase 5 T063."""

from collections.abc import Mapping, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationInfo,
    field_validator,
    model_validator,
)

from schema.planner import PlanStep

from .planner import PlannerTaskInput

MAX_CONTEXT_SOURCES = 8
MAX_CONTEXT_BYTES = 8_192
MAX_PROVENANCE_LENGTH = 128
MAX_SELECTION_REASON_LENGTH = 200
MAX_SOURCE_CONTENT_LENGTH = 1_000


class ContextSource(BaseModel):
    """One explicitly selected, bounded source of untrusted context data."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provenance: StrictStr = Field(min_length=1, max_length=MAX_PROVENANCE_LENGTH)
    selection_reason: StrictStr = Field(max_length=MAX_SELECTION_REASON_LENGTH)
    content: StrictStr = Field(max_length=MAX_SOURCE_CONTENT_LENGTH)

    @field_validator("provenance", "selection_reason", "content")
    @classmethod
    def text_must_be_sanitized(cls, value: str, info: ValidationInfo) -> str:
        if info.field_name == "provenance" and not value.strip():
            raise ValueError("provenance must not be blank")
        if info.field_name == "provenance" and value != value.strip():
            raise ValueError("provenance must not have surrounding whitespace")
        if any(
            (ord(character) < 32 and character not in "\t\n\r") or ord(character) == 127
            for character in value
        ):
            raise ValueError(f"{info.field_name} must not contain unsafe control characters")
        if "traceback (most recent call last)" in value.casefold():
            raise ValueError(f"{info.field_name} must not contain a traceback")
        return value


class ContextEnvelope(BaseModel):
    """The complete bounded context passed to one capability dispatch."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_input: PlannerTaskInput
    current_step: PlanStep
    sources: tuple[ContextSource, ...] = Field(
        default_factory=tuple,
        max_length=MAX_CONTEXT_SOURCES,
    )

    @field_validator("sources", mode="before")
    @classmethod
    def sources_must_be_an_ordered_json_sequence(cls, value: object) -> object:
        if not isinstance(value, (list, tuple)):
            raise ValueError("sources must be an ordered JSON sequence")
        return value

    @model_validator(mode="after")
    def serialized_size_must_be_bounded(self) -> "ContextEnvelope":
        serialized_size = len(self.model_dump_json().encode("utf-8"))
        if serialized_size > MAX_CONTEXT_BYTES:
            raise ValueError(f"context envelope exceeds the {MAX_CONTEXT_BYTES}-byte UTF-8 limit")
        return self


class ContextBuilder:
    """Build a deterministic envelope from already selected caller data.

    This builder does not retrieve, rank, or enrich sources.  The supplied
    sequence order is retained, and every value is rebuilt through the bounded
    models before it can enter the envelope.
    """

    def build(
        self,
        task_input: PlannerTaskInput | Mapping[str, object],
        current_step: PlanStep | Mapping[str, object],
        sources: Sequence[ContextSource | Mapping[str, object]] = (),
    ) -> ContextEnvelope:
        if isinstance(sources, (str, bytes, bytearray)) or not isinstance(sources, Sequence):
            raise ValueError("sources must be an ordered sequence")

        validated_task_input = PlannerTaskInput.model_validate(task_input)
        validated_current_step = PlanStep.model_validate(current_step)
        validated_sources = [ContextSource.model_validate(source) for source in sources]

        # Re-validate JSON-shaped snapshots so no caller-owned runtime object is
        # retained by the checkpoint-safe model graph.
        return ContextEnvelope.model_validate(
            {
                "task_input": validated_task_input.model_dump(mode="json"),
                "current_step": validated_current_step.model_dump(mode="json"),
                "sources": [source.model_dump(mode="json") for source in validated_sources],
            }
        )


__all__ = [
    "ContextBuilder",
    "ContextEnvelope",
    "ContextSource",
    "MAX_CONTEXT_BYTES",
    "MAX_CONTEXT_SOURCES",
    "MAX_PROVENANCE_LENGTH",
    "MAX_SELECTION_REASON_LENGTH",
    "MAX_SOURCE_CONTENT_LENGTH",
]
