"""Runtime verifier result schema for Phase 4 T045."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

VerificationVerdict = Literal["PASS", "FAIL"]
VerifierText = Annotated[str, Field(max_length=500)]


class VerificationResult(BaseModel):
    """Validated, JSON-serializable verifier output with no authority fields."""

    model_config = ConfigDict(extra="forbid")

    verdict: VerificationVerdict
    reason: VerifierText
    evidence: list[VerifierText] = Field(max_length=8)

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value


__all__ = ["VerificationResult", "VerificationVerdict"]
