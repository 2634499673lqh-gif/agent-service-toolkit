"""Bounded, schema-validating verifier node for Phase 4 T046."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import ValidationError

from schema.planner import PlanStep
from schema.verifier import VerificationResult

from .executor import ExecutionResult
from .planner import PlannerTaskInput


@dataclass(frozen=True, slots=True)
class VerifierRepairContext:
    """Safe validation context supplied only to the one repair attempt."""

    original_output: object
    validation_error: str


@dataclass(frozen=True, slots=True)
class VerifierRequest:
    """Sanitized runtime context passed to the injected verifier model."""

    task_input: PlannerTaskInput
    step: PlanStep
    execution_result: ExecutionResult
    repair: VerifierRepairContext | None = None


class VerifierModel(Protocol):
    """Narrow async model boundary used by the verifier node."""

    def __call__(self, request: VerifierRequest) -> Awaitable[object]: ...


class VerifierOutputInvalidError(ValueError):
    """Terminal failure after the single permitted verifier repair attempt."""

    code = "verifier_output_invalid"
    attempts = 2

    def __init__(self) -> None:
        super().__init__("verifier output remained invalid after one repair attempt")


class VerifierNode:
    """Call an injected verifier model and return only a validated result."""

    def __init__(
        self, model: VerifierModel | Callable[[VerifierRequest], Awaitable[object]]
    ) -> None:
        self._model = model

    async def __call__(
        self,
        task_input: PlannerTaskInput | Mapping[str, Any],
        step: PlanStep | Mapping[str, Any],
        execution_result: ExecutionResult | Mapping[str, Any],
    ) -> VerificationResult:
        """Verify sanitized execution context with exactly one repair attempt."""

        sanitized_task_input = PlannerTaskInput.model_validate(task_input)
        validated_step = PlanStep.model_validate(step)
        normalized_execution_result = ExecutionResult.model_validate(execution_result)
        initial_request = VerifierRequest(
            task_input=sanitized_task_input,
            step=validated_step,
            execution_result=normalized_execution_result,
        )
        initial_output = await self._model(initial_request)

        try:
            return VerificationResult.model_validate(initial_output)
        except ValidationError as initial_error:
            repair_output = await self._model(
                VerifierRequest(
                    task_input=sanitized_task_input,
                    step=validated_step,
                    execution_result=normalized_execution_result,
                    repair=VerifierRepairContext(
                        original_output=initial_output,
                        validation_error=_safe_validation_summary(initial_error),
                    ),
                )
            )

        try:
            return VerificationResult.model_validate(repair_output)
        except ValidationError:
            raise VerifierOutputInvalidError from None


def _safe_validation_summary(error: ValidationError) -> str:
    """Describe validation locations/types without copying invalid values."""

    details = error.errors(include_url=False, include_context=False, include_input=False)
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc']) or 'root'}: {detail['type']}"
        for detail in details
    )


__all__ = [
    "VerifierModel",
    "VerifierNode",
    "VerifierOutputInvalidError",
    "VerifierRepairContext",
    "VerifierRequest",
]
