"""Small capability adapters; deterministic functions remain the authority."""

from typing import Any

from runtime.capability import CapabilityMetadata
from runtime.executor import ExecutionResult
from schema.planner import PlanStep

from .aoi import resolve_aoi


class ResolveAOICapability:
    metadata = CapabilityMetadata(
        name="resolve_aoi", description="Resolve the controlled Wuhan East Lake AOI.",
        read_only=True, deterministic=True, side_effect_free=True,
    )

    async def execute(self, step: PlanStep, context: Any) -> ExecutionResult:
        place = context.task_input.description or context.task_input.title
        aoi = resolve_aoi(place)
        return ExecutionResult(step_position=step.position, success=True, output=aoi.model_dump_json())


class GeoChangeCapabilitySet:
    """Explicit mapping suitable for CapabilityDispatcher injection in tests/runtime."""

    @staticmethod
    def mapping() -> dict[str, object]:
        return {"resolve_aoi": ResolveAOICapability()}


__all__ = ["GeoChangeCapabilitySet", "ResolveAOICapability"]
