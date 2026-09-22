"""Bounded retry budget for Phase 4 T048."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .failure import FailureClassification, RuntimeFailure

RETRY_BUDGET = 1
RetryCount = Annotated[int, Field(ge=0, le=RETRY_BUDGET)]


class RetryDecision(BaseModel):
    """Checkpoint-safe result of consuming the single retry budget."""

    model_config = ConfigDict(extra="forbid")

    classification: FailureClassification
    retry_count: RetryCount
    retry_allowed: bool

    @model_validator(mode="after")
    def retry_allowed_matches_classification(self) -> "RetryDecision":
        if self.retry_allowed != (self.classification == "RETRY"):
            raise ValueError("retry_allowed must match the effective classification")
        return self


def consume_retry(failure: RuntimeFailure, retry_count: int) -> RetryDecision:
    """Apply the bounded retry budget without executing or reclassifying work."""

    if not 0 <= retry_count <= RETRY_BUDGET:
        raise ValueError(f"retry_count must be between 0 and {RETRY_BUDGET}")

    if failure.classification == "RETRY" and retry_count < RETRY_BUDGET:
        return RetryDecision(
            classification="RETRY",
            retry_count=retry_count + 1,
            retry_allowed=True,
        )

    if failure.classification == "RETRY":
        return RetryDecision(
            classification="TERMINAL",
            retry_count=retry_count,
            retry_allowed=False,
        )

    return RetryDecision(
        classification=failure.classification,
        retry_count=retry_count,
        retry_allowed=False,
    )


__all__ = ["RETRY_BUDGET", "RetryDecision", "consume_retry"]
