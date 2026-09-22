"""Runtime planner output schemas for Phase 4 T041."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlanStep(BaseModel):
    """One ordered, runtime-only planner instruction."""

    model_config = ConfigDict(extra="forbid")

    position: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=500)

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("instruction must not be blank")
        return value


class Plan(BaseModel):
    """Validated, ordered runtime plan with no authority or persistence fields."""

    model_config = ConfigDict(extra="forbid")

    steps: list[PlanStep] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def positions_must_be_canonical(self) -> "Plan":
        positions = [step.position for step in self.steps]
        expected = list(range(1, len(self.steps) + 1))
        if positions != expected:
            raise ValueError("step positions must be the canonical sequence 1..N")
        return self


__all__ = ["Plan", "PlanStep"]
