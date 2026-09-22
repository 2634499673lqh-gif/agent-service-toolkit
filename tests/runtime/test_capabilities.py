import pytest

from runtime import (
    CapabilityDispatcher,
    DeterministicFixtureCapability,
    ExecutionResult,
    RuntimeFailure,
)
from schema import PlanStep

STEP = PlanStep(position=1, instruction="Read-only fixture step")
EXPECTED_OUTPUT = "deterministic-read-only-fixture:v1"


class UnreadableContext:
    def __str__(self) -> str:
        raise AssertionError("capability context must not be read")


@pytest.mark.asyncio
async def test_fixture_capability_returns_exact_bounded_output() -> None:
    capability = DeterministicFixtureCapability()

    result = await capability.execute(STEP, UnreadableContext())

    assert result == ExecutionResult(
        step_position=1,
        success=True,
        output=EXPECTED_OUTPUT,
    )
    assert len(result.output or "") <= 2000
    assert capability.metadata.read_only is True
    assert capability.metadata.deterministic is True
    assert capability.metadata.side_effect_free is True


@pytest.mark.asyncio
async def test_fixture_capability_replays_identically_through_dispatcher() -> None:
    dispatcher = CapabilityDispatcher({"deterministic_fixture": DeterministicFixtureCapability()})
    context = UnreadableContext()

    first = await dispatcher.dispatch("deterministic_fixture", STEP, context)
    second = await dispatcher.dispatch("deterministic_fixture", STEP, context)

    assert (
        first
        == second
        == ExecutionResult(
            step_position=1,
            success=True,
            output=EXPECTED_OUTPUT,
        )
    )


@pytest.mark.asyncio
async def test_dispatcher_rejects_invalid_step_before_fixture_execution() -> None:
    dispatcher = CapabilityDispatcher({"deterministic_fixture": DeterministicFixtureCapability()})

    result = await dispatcher.dispatch(
        "deterministic_fixture",
        {"position": 0, "instruction": "invalid"},  # type: ignore[arg-type]
        UnreadableContext(),
    )

    assert isinstance(result, RuntimeFailure)
    assert result.classification == "TERMINAL"
    assert result.code == "capability_output_invalid"
    assert result.sanitized_message == "Capability output is invalid."
