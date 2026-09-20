"""Bounded, schema-validating planner node for Phase 4 T042."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from schema.planner import Plan


class PlannerTaskInput(BaseModel):
    """The sanitized task snapshot exposed to the planner model."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value


@dataclass(frozen=True, slots=True)
class PlannerRepairContext:
    """Safe validation context supplied only to the one repair attempt."""

    original_output: object
    validation_error: str


@dataclass(frozen=True, slots=True)
class PlannerRequest:
    """Input passed to the injected planner model."""

    task_input: PlannerTaskInput
    repair: PlannerRepairContext | None = None


class PlannerModel(Protocol):
    """Narrow async model boundary used by the planner node."""

    def __call__(self, request: PlannerRequest) -> Awaitable[object]: ...


class PlannerOutputInvalidError(ValueError):
    """Terminal failure after the single permitted planner repair attempt."""

    code = "planner_output_invalid"
    attempts = 2

    def __init__(self) -> None:
        super().__init__("planner output remained invalid after one repair attempt")


class PlannerNode:
    """Call an injected planner model and return only a validated Plan."""

    def __init__(self, model: PlannerModel | Callable[[PlannerRequest], Awaitable[object]]) -> None:
        self._model = model

    async def __call__(self, task_input: PlannerTaskInput | Mapping[str, Any]) -> Plan:
        """Produce a validated Plan, allowing exactly one structured-output repair."""

        sanitized_task_input = PlannerTaskInput.model_validate(task_input)
        initial_output = await self._model(PlannerRequest(task_input=sanitized_task_input))

        try:
            return Plan.model_validate(initial_output)
        except ValidationError as initial_error:
            repair_output = await self._model(
                PlannerRequest(
                    task_input=sanitized_task_input,
                    repair=PlannerRepairContext(
                        original_output=initial_output,
                        validation_error=_safe_validation_summary(initial_error),
                    ),
                )
            )

        try:
            return Plan.model_validate(repair_output)
        except ValidationError:
            raise PlannerOutputInvalidError from None


def _safe_validation_summary(error: ValidationError) -> str:
    """Describe validation locations/types without copying invalid values."""

    details = error.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc']) or 'root'}: {detail['type']}"
        for detail in details
    )


__all__ = [
    "PlannerModel",
    "PlannerNode",
    "PlannerOutputInvalidError",
    "PlannerRequest",
    "PlannerRepairContext",
    "PlannerTaskInput",
]
