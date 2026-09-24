"""Internal TaskPilot runtime service and checkpoint boundary for T050."""

import inspect
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import ApprovalStatus, TaskRunStatus
from persistence.repositories import TaskRunRepository
from runtime.capabilities import DeterministicFixtureCapability
from runtime.capability import CapabilityDispatcher, CapabilityMetadata
from runtime.context import ContextBuilder, ContextEnvelope
from runtime.executor import ExecutionResult, Executor
from runtime.failure import FailureClassifier
from runtime.graph import RuntimeGraphContext, build_runtime_graph
from runtime.planner import PlannerNode
from runtime.risk import RiskRoute, classify_action
from runtime.state import AgentState, PendingApprovalReference
from runtime.verifier import VerifierNode
from service.approval_service import (
    ApprovalError,
    ApprovalProposal,
    ApprovalRunNotActiveError,
    ApprovalService,
    ApprovedActionBusyError,
    ApprovedActionService,
)
from service.session import CurrentPrincipal
from service.task_lifecycle import (
    TaskLifecycleError,
    TaskLifecycleService,
)

CHECKPOINT_THREAD_PREFIX = "taskpilot-run:"
RuntimeOutcome = Literal["SUCCEEDED", "FAILED"]
RuntimeApprovalStatus = Literal["WAITING_APPROVAL", "APPROVED_ACTION_READY"]


class TaskRuntimeError(Exception):
    """Base class for internal runtime boundary failures."""


class TaskRuntimeNotFoundError(TaskRuntimeError):
    """The Task/TaskRun pair is not visible in the supplied tenant scope."""


class TaskRuntimeConflictError(TaskRuntimeError):
    """The requested run cannot be started, resumed, or completed safely."""


class TaskRuntimeCheckpointError(TaskRuntimeError):
    """An Approval committed, but its independent checkpoint needs replay."""


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


class RuntimeApprovalResult(BaseModel):
    """Minimal nonterminal result for an approval wait or approved boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: UUID
    task_run_id: UUID
    checkpoint_thread_id: str
    task_run_status: Literal[TaskRunStatus.RUNNING] = TaskRunStatus.RUNNING
    status: RuntimeApprovalStatus
    approval_id: UUID

    @property
    def outcome(self) -> RuntimeApprovalStatus:
        """Alias for callers that use the terminal result's outcome property."""

        return self.status


RuntimeResult = RuntimeExecutionResult | RuntimeApprovalResult


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
        self.capability_name = (
            "executor_adapter" if executor is not None else "deterministic_fixture"
        )
        if capability_dispatcher is not None:
            self.action_metadata = capability_dispatcher.metadata_for(self.capability_name)
        elif executor is not None:
            self.action_metadata = CapabilityMetadata(
                name=self.capability_name,
                description="Adapts the existing bounded executor for runtime tests.",
                read_only=True,
                deterministic=True,
                side_effect_free=True,
            )
        else:
            self.action_metadata = DeterministicFixtureCapability.metadata
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
        principal: CurrentPrincipal | None = None,
    ) -> RuntimeResult:
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

        if principal is not None and principal.organization_id != organization_id:
            raise TaskRuntimeNotFoundError("task run is not visible in the organization")
        if (
            isinstance(self.action_metadata, CapabilityMetadata)
            and self.action_metadata.risk_level == "L2"
            and principal is None
        ):
            raise TaskRuntimeConflictError("L2 runtime requires a current server principal")

        if run_status in {
            TaskRunStatus.SUCCEEDED,
            TaskRunStatus.FAILED,
            TaskRunStatus.CANCELLED,
        }:
            raise TaskRuntimeConflictError("terminal task run cannot be resumed")

        thread_id = checkpoint_thread_id(task_run_id)
        config = {"configurable": {"thread_id": thread_id}}
        checkpoint = await self._latest_checkpoint(config)
        approval_tracker: dict[str, UUID] = {}
        graph_context = self._runtime_context(
            session,
            principal=principal,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=task_run_id,
            task_snapshot=task_snapshot,
            approval_tracker=approval_tracker,
        )

        async def invoke_graph(input_data: object) -> Any:
            invoke_kwargs: dict[str, Any] = {"config": config}
            if graph_context is not None:
                invoke_kwargs["context"] = graph_context
            return await self.graph.ainvoke(input_data, **invoke_kwargs)

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
                raw_state = await invoke_graph(initial_state.checkpoint_data())
            except Exception:
                if approval_tracker:
                    await session.rollback()
                    raise TaskRuntimeCheckpointError(
                        "Approval committed but the runtime checkpoint must be replayed"
                    ) from None
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
            if checkpoint_state.pending_approval is not None:
                return await self._resolve_pending_approval(
                    session,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    task_snapshot=task_snapshot,
                    state=checkpoint_state,
                    principal=principal,
                    config=config,
                )
            if (
                isinstance(self.action_metadata, CapabilityMetadata)
                and self.action_metadata.risk_level == "L2"
                and checkpoint_state.plan is not None
            ):
                return await self._ensure_l2_checkpoint_reference(
                    session,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    task_snapshot=task_snapshot,
                    state=checkpoint_state,
                    principal=principal,
                    config=config,
                )
            try:
                # A non-None input would intentionally restart the graph from
                # the beginning.  The latest checkpoint is the only input.
                raw_state = await invoke_graph(None)
            except Exception:
                if approval_tracker:
                    await session.rollback()
                    raise TaskRuntimeCheckpointError(
                        "Approval committed but the runtime checkpoint must be replayed"
                    ) from None
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
        elif state.pending_approval is not None:
            return await self._verify_written_approval_checkpoint(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                task_snapshot=task_snapshot,
                state=state,
                principal=principal,
                config=config,
                approval_tracker=approval_tracker,
            )
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

    def _runtime_context(
        self,
        session: AsyncSession,
        *,
        principal: CurrentPrincipal | None,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        task_snapshot: tuple[str, str | None],
        approval_tracker: dict[str, UUID],
    ) -> RuntimeGraphContext | None:
        """Build invocation-only callbacks; no service object enters AgentState."""

        if principal is None:
            return None

        async def create_approval(
            metadata: CapabilityMetadata,
            context: ContextEnvelope,
            state: AgentState,
        ) -> PendingApprovalReference:
            if (
                principal.organization_id != organization_id
                or metadata != self.action_metadata
                or state.task_id != str(task_id)
                or state.task_run_id != str(task_run_id)
            ):
                raise ValueError("runtime approval identity is invalid")
            proposal = self._proposal_for_state(state, task_snapshot)
            if context.model_dump(mode="json") != proposal.proposed_action:
                raise ValueError("runtime action arguments changed")
            approval = await ApprovalService(session).create_or_reuse(
                principal,
                task_id=task_id,
                task_run_id=task_run_id,
                replan_count=state.replan_count,
                step_position=state.plan_position,
                proposal=proposal,
            )
            approval_tracker["approval_id"] = approval.id
            return PendingApprovalReference(
                approval_id=approval.id,
                replan_count=state.replan_count,
                step_position=state.plan_position,
            )

        return RuntimeGraphContext(approval_gate=create_approval)

    def _proposal_for_state(
        self,
        state: AgentState,
        task_snapshot: tuple[str, str | None],
    ) -> ApprovalProposal:
        """Rebuild the current typed action arguments from validated runtime data."""

        if (
            not isinstance(self.action_metadata, CapabilityMetadata)
            or state.terminal_outcome is not None
            or state.failure is not None
            or state.task_input.title != task_snapshot[0]
            or state.task_input.description != task_snapshot[1]
            or state.plan is None
            or not 0 <= state.replan_count <= 1
            or state.plan_position >= len(state.plan.steps)
        ):
            raise ValueError("runtime action state is invalid")
        step = state.plan.steps[state.plan_position]
        if step.position != state.plan_position + 1:
            raise ValueError("runtime plan slot is not canonical")
        context = ContextBuilder().build(
            task_input=state.task_input,
            current_step=step,
        )
        if (
            classify_action(
                self.action_metadata,
                context,
                expected_name=self.capability_name,
            )
            is not RiskRoute.APPROVAL_REQUIRED
        ):
            raise ValueError("runtime action is not a valid L2 action")
        return ApprovalProposal(
            action_name=self.action_metadata.name,
            action_version=self.action_metadata.action_version,
            proposed_action=context.model_dump(mode="json"),
        )

    async def _ensure_l2_checkpoint_reference(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        thread_id: str,
        task_snapshot: tuple[str, str | None],
        state: AgentState,
        principal: CurrentPrincipal | None,
        config: dict[str, dict[str, str]],
    ) -> RuntimeResult:
        """Recover a durable approval when its first checkpoint write was lost."""

        if principal is None or principal.organization_id != organization_id:
            raise TaskRuntimeConflictError("approval resume requires a current tenant principal")
        try:
            proposal = self._proposal_for_state(state, task_snapshot)
        except ValueError:
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=self._failed_state(state, "approval_checkpoint_invalid"),
            )
        try:
            approval = await ApprovalService(session).create_or_reuse(
                principal,
                task_id=task_id,
                task_run_id=task_run_id,
                replan_count=state.replan_count,
                step_position=state.plan_position,
                proposal=proposal,
            )
        except ApprovalError as error:
            await session.rollback()
            raise TaskRuntimeConflictError(
                "approval cannot be recovered for the active runtime"
            ) from error
        reference = PendingApprovalReference(
            approval_id=approval.id,
            replan_count=state.replan_count,
            step_position=state.plan_position,
        )
        try:
            updater = getattr(self.graph, "aupdate_state", None)
            if updater is None:
                raise TaskRuntimeCheckpointError("runtime graph cannot persist approval state")
            await updater(config, {"pending_approval": reference.model_dump(mode="json")})
        except Exception:
            await session.rollback()
            raise TaskRuntimeCheckpointError(
                "Approval committed but the pending reference must be checkpointed"
            ) from None
        return await self._verify_written_approval_checkpoint(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=task_run_id,
            thread_id=thread_id,
            task_snapshot=task_snapshot,
            state=state.model_copy(update={"pending_approval": reference}),
            principal=principal,
            config=config,
            approval_tracker={"approval_id": approval.id},
        )

    async def _resolve_pending_approval(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        thread_id: str,
        task_snapshot: tuple[str, str | None],
        state: AgentState,
        principal: CurrentPrincipal | None,
        config: dict[str, dict[str, str]],
    ) -> RuntimeResult:
        """Resolve a checkpoint hint against locked tenant-scoped business state."""

        reference = state.pending_approval
        if reference is None:  # pragma: no cover - guarded by callers
            raise AssertionError("pending approval reference is required")
        if principal is None or principal.organization_id != organization_id:
            raise TaskRuntimeConflictError("approval resume requires a current tenant principal")
        try:
            proposal = self._proposal_for_state(state, task_snapshot)
        except ValueError:
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=self._failed_state(state, "approval_checkpoint_invalid"),
            )
        if (
            reference.replan_count != state.replan_count
            or reference.step_position != state.plan_position
        ):
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=self._failed_state(state, "approval_checkpoint_invalid"),
            )
        try:
            approval = await ApprovalService(session).validate_runtime_resume(
                principal,
                task_id=task_id,
                task_run_id=task_run_id,
                approval_id=reference.approval_id,
                replan_count=reference.replan_count,
                step_position=reference.step_position,
                proposal=proposal,
            )
        except ApprovalRunNotActiveError as error:
            await session.rollback()
            raise TaskRuntimeConflictError(
                "terminal or cancelled task run cannot resume an approval"
            ) from error
        except ApprovalError as error:
            await session.rollback()
            raise TaskRuntimeConflictError(
                "approval is not available to this runtime principal"
            ) from error
        if approval is None:
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=self._failed_state(state, "approval_checkpoint_invalid"),
            )
        if approval.status is ApprovalStatus.REJECTED:
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=self._failed_state(state, "approval_rejected"),
            )
        if approval.status is ApprovalStatus.PENDING:
            return RuntimeApprovalResult(
                task_id=task_id,
                task_run_id=task_run_id,
                checkpoint_thread_id=thread_id,
                status="WAITING_APPROVAL",
                approval_id=approval.id,
            )

        try:
            outcome = await ApprovedActionService(session).execute(
                principal,
                task_id=task_id,
                task_run_id=task_run_id,
                approval_id=approval.id,
                replan_count=reference.replan_count,
                step_position=reference.step_position,
                proposal=proposal,
            )
        except ApprovedActionBusyError as error:
            raise TaskRuntimeConflictError("approved action is busy") from error
        except ApprovalError as error:
            raise TaskRuntimeConflictError("approved action is not available") from error

        action_state = state.model_copy(
            update={
                "execution_result": outcome,
                "pending_approval": reference,
                "capability_context": None,
                "verification": None,
                "failure": None,
                "terminal_outcome": None,
            }
        )
        await self._persist_approved_action_checkpoint(
            session,
            config=config,
            thread_id=thread_id,
            task_id=task_id,
            task_run_id=task_run_id,
            reference=reference,
            outcome=outcome,
        )
        if not outcome.success:
            failed = action_state.model_copy(
                update={
                    "failure": self.failure_classifier.classify(
                        outcome.error_code or "approved_mock_failure", outcome.error_message
                    ),
                    "terminal_outcome": "FAILED",
                }
            )
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=failed,
            )

        try:
            raw_state = await self.graph.ainvoke(
                action_state.model_copy(update={"pending_approval": None}).model_dump(mode="json"),
                config=config,
                context=self._runtime_context(
                    session,
                    principal=principal,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    task_snapshot=task_snapshot,
                    approval_tracker={},
                ),
            )
            resumed_state = AgentState.model_validate(raw_state)
        except Exception:
            await session.rollback()
            raise TaskRuntimeCheckpointError(
                "approved action committed but the checkpoint must be replayed"
            ) from None
        if resumed_state.task_id != str(task_id) or resumed_state.task_run_id != str(task_run_id):
            raise TaskRuntimeConflictError("approved action checkpoint identity is invalid")
        await self._persist_approved_action_checkpoint(
            session,
            config=config,
            thread_id=thread_id,
            task_id=task_id,
            task_run_id=task_run_id,
            reference=reference,
            outcome=outcome,
        )
        return await self._finish(
            session,
            TaskRunRepository(session),
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=task_run_id,
            thread_id=thread_id,
            state=resumed_state,
        )

    async def _persist_approved_action_checkpoint(
        self,
        session: AsyncSession,
        *,
        config: dict[str, dict[str, str]],
        thread_id: str,
        task_id: UUID,
        task_run_id: UUID,
        reference: PendingApprovalReference,
        outcome: ExecutionResult,
    ) -> None:
        """Cache a business outcome while retaining the lookup reference."""

        updater = getattr(self.graph, "aupdate_state", None)
        if updater is None:
            raise TaskRuntimeCheckpointError(
                "approved action committed but the checkpoint must be replayed"
            )
        try:
            await updater(
                config,
                {
                    "execution_result": outcome.model_dump(mode="json"),
                    "pending_approval": reference.model_dump(mode="json"),
                    "capability_context": None,
                    "verification": None,
                    "failure": None,
                    "terminal_outcome": None,
                },
                as_node="executor",
            )
            latest = await self._latest_checkpoint(config)
            saved_state = self._state_from_checkpoint(
                latest,
                thread_id,
                task_id=task_id,
                task_run_id=task_run_id,
            )
            if saved_state.pending_approval != reference or saved_state.execution_result != outcome:
                raise ValueError("approved action outcome checkpoint did not persist")
        except Exception:
            await session.rollback()
            raise TaskRuntimeCheckpointError(
                "approved action committed but the checkpoint must be replayed"
            ) from None

    async def _verify_written_approval_checkpoint(
        self,
        session: AsyncSession,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        thread_id: str,
        task_snapshot: tuple[str, str | None],
        state: AgentState,
        principal: CurrentPrincipal | None,
        config: dict[str, dict[str, str]],
        approval_tracker: dict[str, UUID],
    ) -> RuntimeResult:
        """Return wait only after the checkpointer stored the exact reference."""

        try:
            latest = await self._latest_checkpoint(config)
        except Exception:
            if approval_tracker:
                await session.rollback()
                raise TaskRuntimeCheckpointError(
                    "Approval committed but the runtime checkpoint must be replayed"
                ) from None
            latest = None
        if latest is None:
            failed = self._failed_state(state, "checkpoint_missing")
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=failed,
            )
        try:
            saved_state = self._state_from_checkpoint(
                latest,
                thread_id,
                task_id=task_id,
                task_run_id=task_run_id,
            )
        except ValueError:
            failed = self._failed_state(state, "checkpoint_corrupt")
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=failed,
            )
        if saved_state.pending_approval is None and approval_tracker:
            await session.rollback()
            raise TaskRuntimeCheckpointError(
                "Approval committed but the pending reference was not checkpointed"
            )
        if saved_state.pending_approval != state.pending_approval:
            failed = self._failed_state(state, "approval_checkpoint_invalid")
            return await self._finish(
                session,
                TaskRunRepository(session),
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=task_run_id,
                thread_id=thread_id,
                state=failed,
            )
        if approval_tracker and state.pending_approval is not None:
            if approval_tracker.get("approval_id") != state.pending_approval.approval_id:
                failed = self._failed_state(state, "approval_checkpoint_invalid")
                return await self._finish(
                    session,
                    TaskRunRepository(session),
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=task_run_id,
                    thread_id=thread_id,
                    state=failed,
                )
        return await self._resolve_pending_approval(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=task_run_id,
            thread_id=thread_id,
            task_snapshot=task_snapshot,
            state=saved_state,
            principal=principal,
            config=config,
        )

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
    "RuntimeApprovalResult",
    "RuntimeExecutionResult",
    "RuntimeResult",
    "TaskRuntimeCheckpointError",
    "TaskRuntimeConflictError",
    "TaskRuntimeError",
    "TaskRuntimeNotFoundError",
    "TaskRuntimeService",
    "checkpoint_thread_id",
]
