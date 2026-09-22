"""Bounded runtime failure classification for Phase 4 T047."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FailureClassification = Literal["RETRY", "REPLAN", "TERMINAL"]
FailureCode = Annotated[str, Field(min_length=1, max_length=64)]
SanitizedFailureMessage = Annotated[str, Field(max_length=500)]

_RETRYABLE_EXECUTION_CODE = "deterministic_execution_failed"
_REPLAN_CODES = frozenset(
    {
        "recoverable_plan_inadequacy",
        "recoverable_verifier_inadequacy",
    }
)


class RuntimeFailure(BaseModel):
    """Normalized, JSON-serializable failure data with no runtime authority."""

    model_config = ConfigDict(extra="forbid")

    classification: FailureClassification
    code: FailureCode
    sanitized_message: SanitizedFailureMessage | None = None

    @field_validator("code")
    @classmethod
    def code_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("code must not be blank")
        return value


class FailureClassifier:
    """Pure, fail-closed mapping from normalized failure code to classification."""

    def classify(self, code: str, sanitized_message: str | None = None) -> RuntimeFailure:
        """Return a bounded failure; unknown codes are terminal by default."""

        if code == _RETRYABLE_EXECUTION_CODE:
            classification: FailureClassification = "RETRY"
        elif code in _REPLAN_CODES:
            classification = "REPLAN"
        else:
            classification = "TERMINAL"

        return RuntimeFailure(
            classification=classification,
            code=code,
            sanitized_message=sanitized_message,
        )


__all__ = ["FailureClassification", "FailureClassifier", "RuntimeFailure"]
