import json

import pytest
from pydantic import ValidationError

from schema import Plan, PlanStep


def test_plan_step_accepts_one_valid_step() -> None:
    step = PlanStep(position=1, instruction="Inspect the task input")

    assert step.position == 1
    assert step.instruction == "Inspect the task input"


def test_plan_accepts_multiple_canonical_steps() -> None:
    plan = Plan(
        steps=[
            PlanStep(position=1, instruction="Inspect the task input"),
            PlanStep(position=2, instruction="Prepare the result"),
        ]
    )

    assert [step.position for step in plan.steps] == [1, 2]


def test_plan_accepts_exactly_eight_steps() -> None:
    plan = Plan(
        steps=[PlanStep(position=index, instruction=f"Step {index}") for index in range(1, 9)]
    )

    assert len(plan.steps) == 8


def test_plan_step_accepts_instruction_at_maximum_length() -> None:
    step = PlanStep(position=1, instruction="x" * 500)

    assert len(step.instruction) == 500


def test_plan_json_round_trip_is_json_compatible() -> None:
    plan = Plan(steps=[PlanStep(position=1, instruction="Return the result")])

    payload = plan.model_dump(mode="json")
    restored = Plan.model_validate_json(json.dumps(payload))

    assert payload == {"steps": [{"position": 1, "instruction": "Return the result"}]}
    assert restored == plan


def test_plan_rejects_empty_steps() -> None:
    with pytest.raises(ValidationError):
        Plan(steps=[])


def test_plan_rejects_more_than_eight_steps() -> None:
    with pytest.raises(ValidationError):
        Plan(
            steps=[PlanStep(position=index, instruction=f"Step {index}") for index in range(1, 10)]
        )


@pytest.mark.parametrize("position", [0, -1])
def test_plan_step_rejects_non_positive_position(position: int) -> None:
    with pytest.raises(ValidationError):
        PlanStep(position=position, instruction="Do the step")


@pytest.mark.parametrize(
    ("positions", "case"),
    [
        ([1, 1], "duplicate"),
        ([1, 3], "gap"),
        ([2], "noncanonical start"),
        ([2, 1], "out of order"),
    ],
)
def test_plan_rejects_noncanonical_positions(positions: list[int], case: str) -> None:
    with pytest.raises(ValidationError, match="canonical"):
        Plan(
            steps=[
                PlanStep(position=position, instruction=f"Step {index}")
                for index, position in enumerate(positions, start=1)
            ]
        )


@pytest.mark.parametrize("instruction", ["", "   "])
def test_plan_step_rejects_empty_instruction(instruction: str) -> None:
    with pytest.raises(ValidationError):
        PlanStep(position=1, instruction=instruction)


def test_plan_step_rejects_overlong_instruction() -> None:
    with pytest.raises(ValidationError):
        PlanStep(position=1, instruction="x" * 501)


def test_plan_step_rejects_unexpected_extra_field() -> None:
    with pytest.raises(ValidationError):
        PlanStep(position=1, instruction="Do the step", permissions=["read"])


def test_plan_rejects_unexpected_extra_field() -> None:
    with pytest.raises(ValidationError):
        Plan(steps=[PlanStep(position=1, instruction="Do the step")], organization_id="org-1")
