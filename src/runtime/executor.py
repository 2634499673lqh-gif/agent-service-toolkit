"""Typed executor boundary and result contract for Phase 4 T043."""

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schema.planner import PlanStep

from .planner import PlannerTaskInput


class ExecutionResult(BaseModel):
    """Normalized, checkpoint-safe result for one PlanStep execution."""

    model_config = ConfigDict(extra="forbid")

    step_position: int = Field(gt=0)
    success: bool
    output: str | None = Field(default=None, max_length=2000)
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=500)

    @field_validator("error_code")
    @classmethod
    def error_code_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("error_code must not be blank")
        return value

    @model_validator(mode="after")
    def enforce_success_and_failure_shape(self) -> "ExecutionResult":
        if self.success:
            if self.output is None:
                raise ValueError("successful execution requires output")
            if self.error_code is not None:
                raise ValueError("successful execution must not contain error_code")
            if self.error_message is not None:
                raise ValueError("successful execution must not contain error_message")
        else:
            if self.output is not None:
                raise ValueError("failed execution must not contain output")
            if self.error_code is None:
                raise ValueError("failed execution requires error_code")
        return self


class Executor(Protocol):
    """Narrow async execution interface consumed by the later runtime stages."""

    async def execute(self, step: PlanStep, task_input: PlannerTaskInput) -> ExecutionResult: ...


__all__ = ["ExecutionResult", "Executor"]
