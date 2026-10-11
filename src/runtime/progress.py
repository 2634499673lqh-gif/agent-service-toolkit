"""Non-checkpointed callback contract for server-owned execution progress."""

from typing import Literal, Protocol, runtime_checkable

ProgressPhase = Literal["planning", "preparation", "computation", "validation", "finalization"]
ProgressResetReason = Literal["start", "resume", "retry", "replan"]


@runtime_checkable
class RuntimeProgressSink(Protocol):
    async def start_phase(self, phase: ProgressPhase) -> None: ...

    async def complete_phase(self, phase: ProgressPhase) -> None: ...

    async def fail_phase(self, phase: ProgressPhase) -> None: ...


@runtime_checkable
class RuntimeProgressController(Protocol):
    async def reset_attempt(
        self,
        *,
        reset_reason: ProgressResetReason,
        retry_count: int,
        replan_count: int,
        completed_phases: tuple[ProgressPhase, ...] = (),
    ) -> None: ...

    def current_attempt(self) -> RuntimeProgressSink | None: ...


__all__ = [
    "ProgressPhase",
    "ProgressResetReason",
    "RuntimeProgressController",
    "RuntimeProgressSink",
]
