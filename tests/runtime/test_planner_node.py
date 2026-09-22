from collections.abc import Sequence

import pytest

from runtime.planner import (
    PlannerNode,
    PlannerOutputInvalidError,
    PlannerRequest,
)

VALID_PLAN = {"steps": [{"position": 1, "instruction": "Inspect the task"}]}
TASK_INPUT = {"title": "Prepare the report", "description": "Use the supplied task details."}


class RecordingModel:
    def __init__(self, outputs: Sequence[object]) -> None:
        self.outputs = list(outputs)
        self.requests: list[PlannerRequest] = []

    async def __call__(self, request: PlannerRequest) -> object:
        self.requests.append(request)
        return self.outputs.pop(0)


@pytest.mark.asyncio
async def test_valid_output_is_validated_and_uses_one_model_call() -> None:
    model = RecordingModel([VALID_PLAN])

    plan = await PlannerNode(model)(TASK_INPUT)

    assert plan.model_dump(mode="json") == VALID_PLAN
    assert len(model.requests) == 1
    assert model.requests[0].repair is None
    assert model.requests[0].task_input.model_dump() == TASK_INPUT


@pytest.mark.asyncio
async def test_invalid_output_gets_one_repair_attempt() -> None:
    invalid_output = {"steps": [{"position": 2, "instruction": "Invalid ordering"}]}
    model = RecordingModel([invalid_output, VALID_PLAN])

    plan = await PlannerNode(model)(TASK_INPUT)

    assert plan.model_dump(mode="json") == VALID_PLAN
    assert len(model.requests) == 2
    assert model.requests[1].repair is not None
    assert model.requests[1].repair.original_output == invalid_output
    assert model.requests[1].repair.validation_error == "root: value_error"
    assert "Invalid ordering" not in model.requests[1].repair.validation_error


@pytest.mark.asyncio
async def test_invalid_repair_becomes_stable_terminal_failure_without_third_call() -> None:
    invalid_output = {"steps": [{"position": 2, "instruction": "Invalid ordering"}]}
    invalid_repair = {
        "steps": [{"position": 1, "instruction": "permissions"}],
        "permissions": ["run"],
        "secret": "super-secret-token",
    }
    model = RecordingModel([invalid_output, invalid_repair])

    with pytest.raises(PlannerOutputInvalidError) as error:
        await PlannerNode(model)(TASK_INPUT)

    assert error.value.code == "planner_output_invalid"
    assert error.value.attempts == 2
    assert str(error.value) == "planner output remained invalid after one repair attempt"
    assert "super-secret-token" not in str(error.value)
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_t041_validation_remains_authoritative_for_extra_fields_and_ordering() -> None:
    invalid_output = {
        "steps": [{"position": 1, "instruction": "Do the task", "tool_permissions": ["run"]}]
    }
    valid_repair = {"steps": [{"position": 1, "instruction": "Do the task"}]}
    model = RecordingModel([invalid_output, valid_repair])

    plan = await PlannerNode(model)(TASK_INPUT)

    assert plan.steps[0].position == 1
    assert "tool_permissions" not in plan.model_dump()
    assert len(model.requests) == 2


@pytest.mark.asyncio
async def test_authority_fields_are_rejected_from_planner_input_before_model_call() -> None:
    model = RecordingModel([VALID_PLAN])

    with pytest.raises(ValueError):
        await PlannerNode(model)({**TASK_INPUT, "organization_id": "org-1"})

    assert model.requests == []
