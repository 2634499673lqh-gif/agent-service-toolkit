"""Internal TaskPilot runtime service and checkpoint boundary for T050."""

import inspect
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import TaskRunStatus
from persistence.repositories import TaskRunRepository
from runtime.capability import CapabilityDispatcher
from runtime.context import ContextEnvelope
from runtime.executor import Executor
from runtime.failure import FailureClassifier
from runtime.graph import build_runtime_graph
from runtime.planner import PlannerNode
from runtime.state import AgentState
from runtime.verifier import VerifierNode
from service.task_lifecycle import (
    TaskLifecycleError,
    TaskLifecycleService,
)

CHECKPOINT_THREAD_PREFIX = "taskpilot-run:"
RuntimeOutcome = Literal["SUCCEEDED", "FAILED"]


class TaskRuntimeError(Exception):
    """Base class for internal runtime boundary failures."""


class TaskRuntimeNotFoundError(TaskRuntimeError):
    """The Task/TaskRun pair is not visible in the supplied tenant scope."""


class TaskRuntimeConflictError(TaskRuntimeError):
    """The requested run cannot be started, resumed, or completed safely."""


class RuntimeExecutionResult(BaseModel):
    """Terminal result returned after one runtime invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    task_run_id: UUID
    checkpoint_thread_id: str
    terminal_outcome: RuntimeOutcome
    task_run_status: TaskRunStatus
    state: AgentState

    @property
    def outcome(self) -> RuntimeOutcome:
        """Short alias for callers that do not need the contract field name."""

        return self.terminal_outcome


def checkpoint_thread_id(task_run_id: UUID | str) -> str:
    """Derive the canonical correlation/resume identity from a validated run."""

    try:
        canonical_id = str(UUID(str(task_run_id)))
    except (TypeError, ValueError):
        raise ValueError("task_run_id must be a valid UUID") from None
    return f"{CHECKPOINT_THREAD_PREFIX}{canonical_id}"


class TaskRuntimeService:
    """Run one tenant-validated TaskRun through the bounded capability graph.

    The checkpointer is supplied by the existing LangGraph adapter and owns
    its own persistence.  The supplied business session is used only for
    tenant validation and discrete T035 lifecycle transactions; it is never
    held across graph execution.
    """

    def __init__(
        self,
        checkpointer: Any,
        *,
        planner: PlannerNode | None = None,
        executor: Executor | None = None,
        capability_dispatcher: CapabilityDispatcher[ContextEnvelope] | None = None,
        verifier: VerifierNode | None = None,
        classifier: FailureClassifier | None = None,
        graph: Any | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.failure_classifier = classifier or FailureClassifier()
        self.graph = graph or build_runtime_graph(
            checkpointer,
            planner=planner,
            executor=executor,
            capability_dispatcher=capability_dispatcher,
            verifier=verifier,
            classifier=self.failure_classifier,
        )

    async def execute_run(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
    ) -> RuntimeExecutionResult:
        """Start or resume exactly the requested tenant-scoped TaskRun."""

        runs = TaskRunRepository(session)
        pair = await runs.get_task_and_run_in_principal_tenant(
            task_id, task_run_id, organization_id
        )
        if pair is None:
            await session.rollback()
            raise TaskRuntimeNotFoundError("task run is not visible in the organization")

        task, task_run = pair
        task_snapshot = (task.title, task.description)
        run_status = task_run.status
        await session.rollback()

        if run_status in {
            TaskRunStatus.SUCCEEDED,
            TaskRunStatus.FAILED,
            TaskRunStatus.CANCELLED,
        }:
            raise TaskRuntimeConflictError("terminal task run cannot be resumed")

        thread_id = checkpoint_thread_id(task_run_id)
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint = await self._latest_checkpoint(config)

        if run_status is TaskRunStatus.PENDING:
            if checkpoint is not None:
                raise TaskRuntimeConflictError("pending task run already has a checkpoint")
            try:
                await TaskLifecycleService(session).begin_run(task_id, organization_id)
            except TaskLifecycleError as error:
                raise TaskRuntimeConflictError("task run could not begin") from error
            initial_state = AgentState.initial(
                task_id=task_id,
                task_run_id=task_run_id,
                title=task_snapshot[0],
                description=task_snapshot[1],
            )
            try:
                raw_state = await self.graph.ainvoke(initial_state.checkpoint_data(), config=config)
            except Exception:
                failed_state = self._failed_state(initial_state, "runtime_graph_failed")
                return await self._finish(
                    session,
                    runs,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    state=failed_state,
                )
        elif run_status is TaskRunStatus.RUNNING:
            if checkpoint is None:
                failed_state = self._failed_state(
                    AgentState.initial(
                        task_id=task_id,
                        task_run_id=task_run_id,
                        title=task_snapshot[0],
                        description=task_snapshot[1],
                    ),
                    "checkpoint_missing",
                )
                return await self._finish(
                    session,
                    runs,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    state=failed_state,
                )
            try:
                checkpoint_state = self._state_from_checkpoint(
                    checkpoint, thread_id, task_id=task_id, task_run_id=task_run_id
                )
            except ValueError:
                failed_state = self._failed_state(
                    AgentState.initial(
                        task_id=task_id,
                        task_run_id=task_run_id,
                        title=task_snapshot[0],
                        description=task_snapshot[1],
                    ),
                    "checkpoint_corrupt",
                )
                return await self._finish(
                    session,
                    runs,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    state=failed_state,
                )
            try:
                # A non-None input would intentionally restart the graph from
                # the beginning.  The latest checkpoint is the only input.
                raw_state = await self.graph.ainvoke(None, config=config)
            except Exception:
                failed_state = self._failed_state(checkpoint_state, "runtime_graph_failed")
                return await self._finish(
                    session,
                    runs,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    state=failed_state,
                )
        else:  # pragma: no cover - the enum is exhaustive, this is fail-closed
            raise TaskRuntimeConflictError("unsupported task run state")

        try:
            state = AgentState.model_validate(raw_state)
        except Exception:
            state = self._failed_state(
                AgentState.initial(
                    task_id=task_id,
                    task_run_id=task_run_id,
                    title=task_snapshot[0],
                    description=task_snapshot[1],
                ),
                "runtime_state_invalid",
            )

        if state.task_id != str(task_id) or state.task_run_id != str(task_run_id):
            state = self._failed_state(state, "runtime_state_identity_mismatch")
        elif state.terminal_outcome is None:
            state = self._failed_state(state, "runtime_incomplete")
        return await self._finish(
            session,
            runs,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=task_run_id,
            thread_id=thread_id,
            state=state,
        )

    async def _latest_checkpoint(self, config: dict[str, dict[str, str]]) -> Any | None:
        getter = getattr(self.checkpointer, "aget_tuple", None)
        if getter is None:
            getter = getattr(self.checkpointer, "get_tuple", None)
        if getter is None:
            raise TaskRuntimeConflictError("runtime checkpointer has no checkpoint reader")
        result = getter(config)
        if inspect.isawaitable(result):
            return await result
        return result

    def _state_from_checkpoint(
        self,
        checkpoint: Any,
        thread_id: str,
        *,
        task_id: UUID | str | None = None,
        task_run_id: UUID | str | None = None,
    ) -> AgentState:
        if not isinstance(checkpoint, Mapping):
            checkpoint_config = getattr(checkpoint, "config", None)
            checkpoint_data = getattr(checkpoint, "checkpoint", None)
        else:
            checkpoint_config = checkpoint.get("config")
            checkpoint_data = checkpoint.get("checkpoint")
        if not isinstance(checkpoint_config, Mapping) or not isinstance(checkpoint_data, Mapping):
            raise ValueError("checkpoint tuple is malformed")
        configurable = checkpoint_config.get("configurable")
        if not isinstance(configurable, Mapping) or configurable.get("thread_id") != thread_id:
            raise ValueError("checkpoint identity is stale")
        channel_values = checkpoint_data.get("channel_values")
        if not isinstance(channel_values, Mapping):
            raise ValueError("checkpoint state is missing")
        forbidden = {
            "organization_id",
            "user_id",
            "membership_id",
            "role",
            "secret",
            "session",
            "repository",
        }
        if forbidden.intersection(channel_values):
            raise ValueError("checkpoint contains authority data")
        state_fields = set(AgentState.model_fields)
        framework_fields = {
            key for key in channel_values if key.startswith("__") or key.startswith("branch:")
        }
        if set(channel_values) - state_fields - framework_fields:
            raise ValueError("checkpoint contains unsupported state fields")
        raw_state = {key: value for key, value in channel_values.items() if key in state_fields}
        if not {"task_id", "task_run_id", "task_input"}.issubset(raw_state):
            raise ValueError("checkpoint state is incomplete")
        state = AgentState.model_validate(raw_state)
        if task_id is not None and state.task_id != str(UUID(str(task_id))):
            raise ValueError("checkpoint task identity is stale")
        if task_run_id is not None and state.task_run_id != str(UUID(str(task_run_id))):
            raise ValueError("checkpoint run identity is stale")
        return state

    def _failed_state(self, state: AgentState, code: str) -> AgentState:
        failure = self.failure_classifier.classify(code)
        return state.model_copy(update={"failure": failure, "terminal_outcome": "FAILED"})

    async def _finish(
        self,
        session: AsyncSession,
        runs: TaskRunRepository,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        thread_id: str,
        state: AgentState,
    ) -> RuntimeExecutionResult:
        outcome = state.terminal_outcome
        if outcome not in {"SUCCEEDED", "FAILED"}:
            state = self._failed_state(state, "runtime_incomplete")
            outcome = "FAILED"
        try:
            lifecycle = TaskLifecycleService(session)
            if outcome == "SUCCEEDED":
                await lifecycle.succeed_run(task_id, organization_id)
                expected_status = TaskRunStatus.SUCCEEDED
            else:
                await lifecycle.fail_run(task_id, organization_id)
                expected_status = TaskRunStatus.FAILED
        except TaskLifecycleError as error:
            await session.rollback()
            raise TaskRuntimeConflictError(
                "another lifecycle transition already owns the task run terminal state"
            ) from error

        final_pair = await runs.get_task_and_run_in_principal_tenant(
            task_id, task_run_id, organization_id
        )
        final_status = None if final_pair is None else final_pair[1].status
        await session.rollback()
        if final_status is not expected_status:
            raise TaskRuntimeConflictError("terminal task run state changed before completion")
        return RuntimeExecutionResult(
            task_id=task_id,
            task_run_id=task_run_id,
            checkpoint_thread_id=thread_id,
            terminal_outcome=outcome,
            task_run_status=expected_status,
            state=state,
        )


__all__ = [
    "CHECKPOINT_THREAD_PREFIX",
    "RuntimeExecutionResult",
    "TaskRuntimeConflictError",
    "TaskRuntimeError",
    "TaskRuntimeNotFoundError",
    "TaskRuntimeService",
    "checkpoint_thread_id",
]
