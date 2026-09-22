from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from runtime import (
    CapabilityDispatcher,
    CapabilityMetadata,
    ExecutionResult,
    RuntimeFailure,
)
from schema import PlanStep

STEP = PlanStep(position=1, instruction="Inspect the supplied task")


@dataclass
class FakeCapability:
    metadata: CapabilityMetadata
    result: object

    def __post_init__(self) -> None:
        self.calls: list[tuple[PlanStep, object]] = []

    async def execute(self, step: PlanStep, context: object) -> object:
        self.calls.append((step, context))
        return self.result


def _metadata(name: str = "inspect") -> CapabilityMetadata:
    return CapabilityMetadata(
        name=name,
        description="Read-only inspection.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )


@pytest.mark.asyncio
async def test_dispatches_explicit_capability_with_validated_step_and_context() -> None:
    context = object()
    capability = FakeCapability(
        _metadata(),
        ExecutionResult(step_position=1, success=True, output="completed"),
    )
    dispatcher = CapabilityDispatcher({"inspect": capability})

    result = await dispatcher.dispatch("inspect", STEP.model_dump(), context)

    assert result == ExecutionResult(step_position=1, success=True, output="completed")
    assert capability.calls == [(STEP, context)]


@pytest.mark.asyncio
async def test_unknown_capability_is_a_terminal_normalized_failure() -> None:
    dispatcher = CapabilityDispatcher({"inspect": FakeCapability(_metadata(), object())})

    result = await dispatcher.dispatch("missing", STEP, object())

    assert isinstance(result, RuntimeFailure)
    assert result.classification == "TERMINAL"
    assert result.code == "capability_unknown"
    assert result.sanitized_message == "The requested capability is not available."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw_result",
    [
        {"step_position": 1, "success": True, "output": "x", "organization_id": "org"},
        {"step_position": 2, "success": True, "output": "wrong step"},
        {"step_position": 1, "success": True, "output": "x" * 2001},
        {
            "step_position": 1,
            "success": False,
            "error_code": "failed",
            "error_message": "bad\nvalue",
        },
        {
            "classification": "TERMINAL",
            "code": "failed",
            "sanitized_message": "Traceback (most recent call last): secret-token",
        },
    ],
)
async def test_malformed_or_unsafe_output_is_terminal_and_bounded(raw_result: object) -> None:
    dispatcher = CapabilityDispatcher({"inspect": FakeCapability(_metadata(), raw_result)})

    result = await dispatcher.dispatch("inspect", STEP, object())

    assert isinstance(result, RuntimeFailure)
    assert result.classification == "TERMINAL"
    assert result.code == "capability_output_invalid"
    assert len(result.sanitized_message or "") <= 500
    assert "organization_id" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_invalid_plan_step_is_rejected_before_capability_execution() -> None:
    capability = FakeCapability(
        _metadata(),
        ExecutionResult(step_position=1, success=True, output="must not run"),
    )
    dispatcher = CapabilityDispatcher({"inspect": capability})

    result = await dispatcher.dispatch(
        "inspect",
        {"position": 0, "instruction": "invalid"},  # type: ignore[arg-type]
        object(),
    )

    assert isinstance(result, RuntimeFailure)
    assert result.classification == "TERMINAL"
    assert result.code == "capability_output_invalid"
    assert capability.calls == []


@pytest.mark.asyncio
async def test_raised_exception_becomes_sanitized_failure_without_traceback() -> None:
    class ExplodingCapability:
        metadata = _metadata()

        async def execute(self, step: PlanStep, context: object) -> object:  # noqa: ARG002
            raise RuntimeError("secret-token\nTraceback (most recent call last)")

    dispatcher = CapabilityDispatcher({"inspect": ExplodingCapability()})

    result = await dispatcher.dispatch("inspect", STEP, object())

    assert isinstance(result, RuntimeFailure)
    assert result.classification == "TERMINAL"
    assert result.code == "capability_execution_failed"
    assert result.sanitized_message == "Capability execution failed."
    assert "secret-token" not in result.model_dump_json()
    assert "Traceback" not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "classification"),
    [
        ("deterministic_execution_failed", "RETRY"),
        ("recoverable_plan_inadequacy", "REPLAN"),
        ("unknown_capability_error", "TERMINAL"),
    ],
)
async def test_failure_classification_reuses_existing_fail_closed_mapping(
    code: str, classification: str
) -> None:
    capability = FakeCapability(
        _metadata(),
        RuntimeFailure(
            classification="TERMINAL",
            code=code,
            sanitized_message="safe failure",
        ),
    )
    dispatcher = CapabilityDispatcher({"inspect": capability})

    result = await dispatcher.dispatch("inspect", STEP, object())

    assert isinstance(result, RuntimeFailure)
    assert result.classification == classification
    assert result.code == code
    assert result.sanitized_message == "safe failure"


def test_metadata_rejects_unbounded_or_non_read_only_capabilities() -> None:
    with pytest.raises(ValidationError):
        _metadata("x" * 65)

    with pytest.raises(ValidationError):
        CapabilityMetadata(
            name="inspect",
            description="safe",
            read_only=False,
            deterministic=True,
            side_effect_free=True,
        )


@pytest.mark.asyncio
async def test_repeated_dispatch_is_deterministic_and_uses_the_same_mapping_entry() -> None:
    capability = FakeCapability(
        _metadata(),
        ExecutionResult(step_position=1, success=True, output="stable"),
    )
    dispatcher = CapabilityDispatcher({"inspect": capability})
    context = {"selected": "bounded"}

    first = await dispatcher.dispatch("inspect", STEP, context)
    second = await dispatcher.dispatch("inspect", STEP, context)

    assert first == second
    assert capability.calls == [(STEP, context), (STEP, context)]
