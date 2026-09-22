"""Typed, explicit in-process capability dispatch for Phase 5 T061."""

from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from schema.planner import PlanStep

from .executor import ExecutionResult
from .failure import FailureClassifier, RuntimeFailure

_MAX_NAME_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 200
_MAX_ERROR_CODE_LENGTH = 64
_MAX_ERROR_MESSAGE_LENGTH = 500

_CAPABILITY_UNKNOWN = "capability_unknown"
_CAPABILITY_METADATA_INVALID = "capability_metadata_invalid"
_CAPABILITY_OUTPUT_INVALID = "capability_output_invalid"
_CAPABILITY_EXECUTION_FAILED = "capability_execution_failed"

_SAFE_FAILURE_MESSAGES = {
    _CAPABILITY_UNKNOWN: "The requested capability is not available.",
    _CAPABILITY_METADATA_INVALID: "Capability metadata is invalid.",
    _CAPABILITY_OUTPUT_INVALID: "Capability output is invalid.",
    _CAPABILITY_EXECUTION_FAILED: "Capability execution failed.",
}


class CapabilityMetadata(BaseModel):
    """Bounded metadata required by every Phase 5 capability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: StrictStr = Field(min_length=1, max_length=_MAX_NAME_LENGTH)
    description: StrictStr = Field(default="", max_length=_MAX_DESCRIPTION_LENGTH)
    read_only: Literal[True]
    deterministic: Literal[True]
    side_effect_free: Literal[True]

    @field_validator("name", "description")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        if not value.strip() and value != "":
            raise ValueError("metadata text must not be blank")
        if value != value.strip():
            raise ValueError("metadata text must not have surrounding whitespace")
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("metadata text must not contain control characters")
        return value


class Capability[CapabilityContextT](Protocol):
    """One typed capability; the concrete ContextEnvelope belongs to T063."""

    metadata: CapabilityMetadata

    async def execute(
        self,
        step: PlanStep,
        context: CapabilityContextT,
    ) -> ExecutionResult | RuntimeFailure: ...


class CapabilityDispatcher[CapabilityContextT]:
    """Dispatch through an explicit mapping and normalize untrusted output.

    The mapping is copied at construction and is not a registry: no discovery,
    dynamic loading, registration, or external provider lookup is performed.
    """

    def __init__(
        self,
        capabilities: Mapping[str, Capability[CapabilityContextT]],
        *,
        classifier: FailureClassifier | None = None,
    ) -> None:
        self._capabilities = dict(capabilities)
        self._classifier = classifier or FailureClassifier()

    async def dispatch(
        self,
        name: str,
        step: PlanStep,
        context: CapabilityContextT,
    ) -> ExecutionResult | RuntimeFailure:
        """Invoke one explicitly selected capability with bounded inputs."""

        if not _is_safe_text(name, max_length=_MAX_NAME_LENGTH, require_non_blank=True):
            return _terminal_failure(_CAPABILITY_UNKNOWN)

        capability = self._capabilities.get(name)
        if capability is None:
            return _terminal_failure(_CAPABILITY_UNKNOWN)

        try:
            metadata = CapabilityMetadata.model_validate(capability.metadata)
        except Exception:
            return _terminal_failure(_CAPABILITY_METADATA_INVALID)
        if metadata.name != name:
            return _terminal_failure(_CAPABILITY_METADATA_INVALID)

        try:
            validated_step = PlanStep.model_validate(step)
        except Exception:
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)

        try:
            raw_result = await capability.execute(validated_step, context)
        except Exception:
            return _terminal_failure(_CAPABILITY_EXECUTION_FAILED)

        return self._normalize_result(raw_result, validated_step)

    def _normalize_result(
        self,
        raw_result: object,
        step: PlanStep,
    ) -> ExecutionResult | RuntimeFailure:
        if _looks_like_failure(raw_result):
            return self._normalize_failure(raw_result)

        try:
            result = ExecutionResult.model_validate(raw_result)
        except Exception:
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)

        if result.step_position != step.position:
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)
        if not _is_safe_text(
            result.error_code,
            max_length=_MAX_ERROR_CODE_LENGTH,
            require_non_blank=True,
            allow_none=True,
        ):
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)
        if not _is_safe_text(
            result.error_message,
            max_length=_MAX_ERROR_MESSAGE_LENGTH,
            require_non_blank=False,
            allow_none=True,
        ):
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)
        return result

    def _normalize_failure(self, raw_failure: object) -> RuntimeFailure:
        try:
            failure = RuntimeFailure.model_validate(raw_failure)
        except Exception:
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)

        if not _is_safe_text(
            failure.code,
            max_length=_MAX_ERROR_CODE_LENGTH,
            require_non_blank=True,
        ) or not _is_safe_text(
            failure.sanitized_message,
            max_length=_MAX_ERROR_MESSAGE_LENGTH,
            require_non_blank=False,
            allow_none=True,
        ):
            return _terminal_failure(_CAPABILITY_OUTPUT_INVALID)

        # The returned classification is data, not routing authority.  Reuse
        # the existing fail-closed classifier for the code that was returned.
        return self._classifier.classify(failure.code, failure.sanitized_message)


def _looks_like_failure(value: object) -> bool:
    if isinstance(value, RuntimeFailure):
        return True
    return isinstance(value, Mapping) and (
        "classification" in value or "sanitized_message" in value
    )


def _is_safe_text(
    value: object,
    *,
    max_length: int,
    require_non_blank: bool,
    allow_none: bool = False,
) -> bool:
    if value is None:
        return allow_none
    if not isinstance(value, str) or len(value) > max_length:
        return False
    if require_non_blank and not value.strip():
        return False
    if "traceback (most recent call last)" in value.casefold():
        return False
    return not any(ord(character) < 32 or ord(character) == 127 for character in value)


def _terminal_failure(code: str) -> RuntimeFailure:
    return RuntimeFailure(
        classification="TERMINAL",
        code=code,
        sanitized_message=_SAFE_FAILURE_MESSAGES[code],
    )


__all__ = ["Capability", "CapabilityDispatcher", "CapabilityMetadata"]
