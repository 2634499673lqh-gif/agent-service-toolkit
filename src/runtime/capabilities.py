"""Concrete deterministic capabilities for the Phase 5 boundary."""

from schema.planner import PlanStep

from .capability import CapabilityMetadata
from .executor import ExecutionResult

_FIXTURE_OUTPUT = "deterministic-read-only-fixture:v1"


class DeterministicFixtureCapability:
    """Return one fixed, bounded result without reading capability context."""

    metadata = CapabilityMetadata(
        name="deterministic_fixture",
        description="Returns a fixed deterministic read-only fixture.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: object) -> ExecutionResult:  # noqa: ARG002
        """Return the same bounded fixture for every validated step."""

        return ExecutionResult(
            step_position=step.position,
            success=True,
            output=_FIXTURE_OUTPUT,
        )


__all__ = ["DeterministicFixtureCapability"]
