from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from runtime import (
    ExecutionResult,
    VerifierModel,
    VerifierNode,
    VerifierOutputInvalidError,
    VerifierRequest,
)
from runtime.planner import PlannerTaskInput
from schema import PlanStep

TASK_INPUT = {"title": "Prepare the report", "description": "Use the supplied task details."}
STEP = {"position": 1, "instruction": "Inspect the task result"}
EXECUTION_RESULT = {
    "step_position": 1,
    "success": True,
    "output": "The task result was produced.",
    "error_code": None,
    "error_message": None,
}
VALID_PASS = {"verdict": "PASS", "reason": "The result satisfies the task.", "evidence": []}
VALID_FAIL = {
    "verdict": "FAIL",
    "reason": "The result is incomplete.",
    "evidence": ["The required detail is missing."],
}


class RecordingModel:
    def __init__(self, outputs: Sequence[object]) -> None:
        self.outputs = list(outputs)
        self.requests: list[VerifierRequest] = []

    async def __call__(self, request: VerifierRequest) -> object:
        self.requests.append(request)
        return self.outputs.pop(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("expected", [VALID_PASS, VALID_FAIL])
async def test_valid_output_is_validated_and_uses_one_model_call(
    expected: dict[str, object],
) -> None:
    model = RecordingModel([expected])

    result = await VerifierNode(model)(TASK_INPUT, STEP, EXECUTION_RESULT)

    assert result.model_dump(mode="json") == expected
    assert len(model.requests) == 1
    request = model.requests[0]
    assert request.repair is None
    assert request.task_input.model_dump() == TASK_INPUT
    assert request.step.model_dump() == STEP
    assert request.execution_result.model_dump() == EXECUTION_RESULT
    assert isinstance(request.task_input, PlannerTaskInput)
    assert isinstance(request.step, PlanStep)
    assert isinstance(request.execution_result, ExecutionResult)


@pytest.mark.asyncio
@pytest.mark.parametrize("repaired", [VALID_PASS, VALID_FAIL])
async def test_invalid_initial_output_gets_one_repair_and_returns_valid_result(
    repaired: dict[str, object],
) -> None:
    invalid_output = {"verdict": "RETRY", "reason": "Unsupported verdict", "evidence": []}
    model = RecordingModel([invalid_output, repaired])

    result = await VerifierNode(model)(TASK_INPUT, STEP, EXECUTION_RESULT)

    assert result.model_dump(mode="json") == repaired
    assert len(model.requests) == 2
    assert model.requests[0].repair is None
    assert model.requests[1].repair is not None
    assert model.requests[1].repair.original_output == invalid_output
    assert "RETRY" not in model.requests[1].repair.validation_error
    assert "verdict" in model.requests[1].repair.validation_error


@pytest.mark.asyncio
async def test_invalid_repair_raises_exact_terminal_error_without_third_call() -> None:
    invalid_output = {"verdict": "RETRY", "reason": "Unsupported verdict", "evidence": []}
    invalid_repair = {"verdict": "FAIL", "evidence": [], "secret": "provider-token"}
    model = RecordingModel([invalid_output, invalid_repair])

    with pytest.raises(VerifierOutputInvalidError) as error:
        await VerifierNode(model)(TASK_INPUT, STEP, EXECUTION_RESULT)

    assert error.value.code == "verifier_output_invalid"
    assert error.value.attempts == 2
    assert str(error.value) == "verifier output remained invalid after one repair attempt"
    assert "provider-token" not in str(error.value)
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_malformed_output_is_never_treated_as_fail() -> None:
    model = RecordingModel(
        [
            {"verdict": "FAIL", "evidence": []},
            {"verdict": "FAIL", "reason": "", "evidence": []},
        ]
    )

    with pytest.raises(VerifierOutputInvalidError):
        await VerifierNode(model)(TASK_INPUT, STEP, EXECUTION_RESULT)

    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_runtime_input_rejects_authority_fields_before_model_call() -> None:
    model = RecordingModel([VALID_PASS])

    with pytest.raises(ValidationError):
        await VerifierNode(model)(
            {**TASK_INPUT, "organization_id": "org-1"}, STEP, EXECUTION_RESULT
        )

    assert model.requests == []


@pytest.mark.asyncio
async def test_minimal_fake_satisfies_verifier_model_protocol() -> None:
    class FakeVerifier:
        async def __call__(self, request: VerifierRequest) -> object:
            return {
                "verdict": "PASS",
                "reason": f"Step {request.step.position} passed.",
                "evidence": [],
            }

    model: VerifierModel = FakeVerifier()
    result = await VerifierNode(model)(TASK_INPUT, STEP, EXECUTION_RESULT)

    assert result.verdict == "PASS"
