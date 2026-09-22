"""Typed executor boundary and result contract for Phase 4 T043."""

from typing import Literal, Protocol

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


class DeterministicExecutor:
    """One bounded, side-effect-free execution path for T044."""

    _FAILURE_CODE = "deterministic_execution_failed"
    _FAILURE_MESSAGE = "Deterministic execution failed."

    def __init__(
        self,
        *,
        failure_mode: Literal["none", "fail_once", "always_fail"] = "none",
    ) -> None:
        self._failure_mode = failure_mode
        self._fail_once_used = False

    async def execute(self, step: PlanStep, task_input: PlannerTaskInput) -> ExecutionResult:
        """Return a stable result without invoking external systems."""

        if self._should_fail():
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code=self._FAILURE_CODE,
                error_message=self._FAILURE_MESSAGE,
            )

        output = (
            f"Task: {task_input.title}\n"
            f"Description: {task_input.description or ''}\n"
            f"Step {step.position}: {step.instruction}"
        )[:2000]
        return ExecutionResult(step_position=step.position, success=True, output=output)

    def _should_fail(self) -> bool:
        if self._failure_mode == "always_fail":
            return True
        if self._failure_mode == "fail_once" and not self._fail_once_used:
            self._fail_once_used = True
            return True
        return False


__all__ = ["DeterministicExecutor", "ExecutionResult", "Executor"]
