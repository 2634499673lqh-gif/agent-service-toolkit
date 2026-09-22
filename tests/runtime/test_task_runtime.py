"""Deterministic T050 runtime and checkpoint/resume coverage."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Task, TaskRun, TaskRunStatus, TaskStatus
from runtime import (
    AgentState,
    CapabilityDispatcher,
    CapabilityMetadata,
    ContextEnvelope,
    DeterministicExecutor,
    ExecutionResult,
    PlannerNode,
    PlannerRequest,
    RuntimeFailure,
    VerifierNode,
    VerifierRequest,
    build_runtime_graph,
)
from service.task_lifecycle import TaskLifecycleConflictError
from service.task_runtime import (
    TaskRuntimeConflictError,
    TaskRuntimeNotFoundError,
    TaskRuntimeService,
)

ORG = UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
THREAD_ID = f"taskpilot-run:{RUN_ID}"


class FakeRuns:
    def __init__(self, task: Task, run: TaskRun) -> None:
        self.task = task
        self.run = run

    async def get_task_and_run_in_principal_tenant(self, task_id, run_id, organization_id):
        if (
            task_id != self.task.id
            or run_id != self.run.id
            or organization_id != self.task.organization_id
        ):
            return None
        return self.task, self.run


def _business_pair(status: TaskRunStatus) -> tuple[Task, TaskRun]:
    task = Task(
        id=TASK_ID,
        organization_id=ORG,
        created_by_user_id=USER_ID,
        title="Prepare report",
        description="Use the supplied details.",
        status=TaskStatus.QUEUED if status is TaskRunStatus.PENDING else TaskStatus.RUNNING,
    )
    run = TaskRun(id=RUN_ID, task_id=TASK_ID, run_number=1, status=status)
    return task, run


def _session() -> AsyncSession:
    session = Mock(spec=AsyncSession)
    session.rollback = AsyncMock()
    return session


def _lifecycle_for(run: TaskRun, task: Task) -> Mock:
    lifecycle = Mock()

    async def begin(*_args):
        run.status = TaskRunStatus.RUNNING
        task.status = TaskStatus.RUNNING

    async def succeed(*_args):
        run.status = TaskRunStatus.SUCCEEDED
        task.status = TaskStatus.SUCCEEDED

    async def fail(*_args):
        run.status = TaskRunStatus.FAILED
        task.status = TaskStatus.FAILED

    lifecycle.begin_run = AsyncMock(side_effect=begin)
    lifecycle.succeed_run = AsyncMock(side_effect=succeed)
    lifecycle.fail_run = AsyncMock(side_effect=fail)
    return lifecycle


def test_agent_state_is_json_safe_and_contains_no_authority_fields() -> None:
    state = AgentState.initial(
        task_id=TASK_ID,
        task_run_id=RUN_ID,
        title="Task",
        description=None,
    )
    encoded = json.dumps(state.checkpoint_data())
    assert str(TASK_ID) in encoded
    with pytest.raises(ValueError):
        AgentState.model_validate(state.checkpoint_data() | {"organization_id": str(ORG)})


@pytest.mark.asyncio
async def test_default_graph_dispatches_fixture_with_bounded_checkpoint_context() -> None:
    saver = MemorySaver()
    initial = AgentState.initial(
        task_id=TASK_ID,
        task_run_id=RUN_ID,
        title="Task",
        description="Use the selected details.",
    )
    graph = build_runtime_graph(saver, interrupt_before=["verifier"])

    await graph.ainvoke(
        initial.checkpoint_data(), config={"configurable": {"thread_id": THREAD_ID}}
    )

    checkpoint = saver.get_tuple({"configurable": {"thread_id": THREAD_ID}})
    assert checkpoint is not None
    state = TaskRuntimeService(saver)._state_from_checkpoint(
        checkpoint,
        THREAD_ID,
        task_id=TASK_ID,
        task_run_id=RUN_ID,
    )
    assert state.execution_result == ExecutionResult(
        step_position=1,
        success=True,
        output="deterministic-read-only-fixture:v1",
    )
    assert state.capability_context is not None
    assert state.plan is not None
    assert state.capability_context.current_step == state.plan.steps[0]
    assert state.capability_context.task_input == state.task_input
    assert state.capability_context.sources == ()
    assert "organization_id" not in state.capability_context.model_dump_json()


class _RetryThenSucceedCapability:
    metadata = CapabilityMetadata(
        name="deterministic_fixture",
        description="A test-only deterministic read-only capability.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    def __init__(self) -> None:
        self.contexts: list[ContextEnvelope] = []

    async def execute(self, step, context: ContextEnvelope) -> ExecutionResult | RuntimeFailure:
        self.contexts.append(context)
        if len(self.contexts) == 1:
            return RuntimeFailure(
                classification="TERMINAL",
                code="deterministic_execution_failed",
                sanitized_message="retryable test failure",
            )
        return ExecutionResult(step_position=step.position, success=True, output="capability-ok")


@pytest.mark.asyncio
async def test_capability_retry_uses_dispatcher_context_and_same_task_run() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    capability = _RetryThenSucceedCapability()
    saver = MemorySaver()
    graph = build_runtime_graph(
        saver,
        capability_dispatcher=CapabilityDispatcher({"deterministic_fixture": capability}),
    )
    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.task_run_id == RUN_ID
    assert result.state.retry_count == 1
    assert len(capability.contexts) == 2
    assert all(not hasattr(context, "organization_id") for context in capability.contexts)
    assert lifecycle.begin_run.await_count == 1
    assert lifecycle.succeed_run.await_count == 1


class _ReplanThenSucceedCapability:
    metadata = _RetryThenSucceedCapability.metadata

    def __init__(self) -> None:
        self.contexts: list[ContextEnvelope] = []

    async def execute(self, step, context: ContextEnvelope) -> ExecutionResult | RuntimeFailure:
        self.contexts.append(context)
        if len(self.contexts) == 1:
            return RuntimeFailure(
                classification="TERMINAL",
                code="recoverable_plan_inadequacy",
                sanitized_message="replacement plan required",
            )
        return ExecutionResult(step_position=step.position, success=True, output="replanned-ok")


@pytest.mark.asyncio
async def test_capability_replan_clears_then_rebuilds_context_without_new_run() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    planner_outputs = [
        {"steps": [{"position": 1, "instruction": "Initial capability step"}]},
        {"steps": [{"position": 1, "instruction": "Replacement capability step"}]},
    ]

    async def planner_model(_request: PlannerRequest) -> object:
        return planner_outputs.pop(0)

    capability = _ReplanThenSucceedCapability()
    saver = MemorySaver()
    graph = build_runtime_graph(
        saver,
        planner=PlannerNode(planner_model),
        capability_dispatcher=CapabilityDispatcher({"deterministic_fixture": capability}),
    )
    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.task_run_id == RUN_ID
    assert result.state.replan_count == 1
    assert [context.current_step.instruction for context in capability.contexts] == [
        "Initial capability step",
        "Replacement capability step",
    ]
    assert lifecycle.begin_run.await_count == 1
    assert lifecycle.succeed_run.await_count == 1


@pytest.mark.asyncio
async def test_pending_run_starts_once_checkpoints_and_succeeds() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    saver = MemorySaver()
    service = TaskRuntimeService(saver)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    lifecycle.begin_run.assert_awaited_once_with(TASK_ID, ORG)
    lifecycle.succeed_run.assert_awaited_once_with(TASK_ID, ORG)
    assert result.terminal_outcome == "SUCCEEDED"
    assert result.task_run_status is TaskRunStatus.SUCCEEDED
    assert result.state.terminal_outcome == "SUCCEEDED"
    assert saver.get_tuple({"configurable": {"thread_id": THREAD_ID}}) is not None


@pytest.mark.asyncio
async def test_fail_once_consumes_retry_without_creating_a_new_run() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    saver = MemorySaver()
    graph = build_runtime_graph(saver, executor=DeterministicExecutor(failure_mode="fail_once"))
    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.state.retry_count == 1
    assert lifecycle.succeed_run.await_count == 1
    assert lifecycle.fail_run.await_count == 0


@pytest.mark.asyncio
async def test_always_fail_exhausts_retry_and_fails_the_same_run() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    saver = MemorySaver()
    graph = build_runtime_graph(saver, executor=DeterministicExecutor(failure_mode="always_fail"))
    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "FAILED"
    assert result.state.retry_count == 1
    assert result.state.failure is not None
    assert result.state.failure.code == "retry_budget_exhausted"
    assert lifecycle.fail_run.await_count == 1


@pytest.mark.asyncio
async def test_verifier_failure_uses_one_replan_and_preserves_retry_count() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    saver = MemorySaver()
    planner_outputs = [
        {"steps": [{"position": 1, "instruction": "Initial plan"}]},
        {"steps": [{"position": 1, "instruction": "Replacement plan"}]},
    ]
    verifier_outputs = [
        {"verdict": "FAIL", "reason": "The plan is inadequate.", "evidence": []},
        {"verdict": "PASS", "reason": "The replacement passed.", "evidence": []},
    ]

    async def planner_model(_request: PlannerRequest) -> object:
        return planner_outputs.pop(0)

    async def verifier_model(_request: VerifierRequest) -> object:
        return verifier_outputs.pop(0)

    graph = build_runtime_graph(
        saver,
        planner=PlannerNode(planner_model),
        executor=DeterministicExecutor(),
        verifier=VerifierNode(verifier_model),
    )
    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)
    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.state.replan_count == 1
    assert result.state.retry_count == 0
    assert result.state.plan is not None
    assert result.state.plan.steps[0].instruction == "Replacement plan"


@pytest.mark.asyncio
async def test_corrupt_checkpoint_fails_closed_through_lifecycle() -> None:
    task, run = _business_pair(TaskRunStatus.RUNNING)

    class CorruptSaver(MemorySaver):
        async def aget_tuple(self, _config):
            return SimpleNamespace(
                config={"configurable": {"thread_id": THREAD_ID}},
                checkpoint={"channel_values": {"task_id": str(TASK_ID)}},
            )

    saver = CorruptSaver()
    service = TaskRuntimeService(saver)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)
    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.state.failure is not None
    assert result.state.failure.code == "checkpoint_corrupt"
    lifecycle.fail_run.assert_awaited_once_with(TASK_ID, ORG)


@pytest.mark.asyncio
async def test_late_completion_fails_closed_after_competing_terminal_transition() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)
    saver = MemorySaver()
    service = TaskRuntimeService(saver)
    fake_runs = FakeRuns(task, run)
    lifecycle = Mock()
    lifecycle.begin_run = AsyncMock()
    lifecycle.succeed_run = AsyncMock(side_effect=TaskLifecycleConflictError("cancelled first"))
    lifecycle.fail_run = AsyncMock()
    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        with pytest.raises(TaskRuntimeConflictError, match="another lifecycle"):
            await service.execute_run(
                _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
            )


@pytest.mark.asyncio
async def test_running_run_resumes_latest_checkpoint_without_restarting() -> None:
    task, run = _business_pair(TaskRunStatus.RUNNING)
    saver = MemorySaver()
    planner_calls: list[PlannerRequest] = []
    verifier_calls: list[VerifierRequest] = []

    async def planner_model(request: PlannerRequest) -> object:
        planner_calls.append(request)
        return {"steps": [{"position": 1, "instruction": "Inspect the task"}]}

    async def verifier_model(request: VerifierRequest) -> object:
        verifier_calls.append(request)
        return {"verdict": "PASS", "reason": "The step passed.", "evidence": []}

    planner = PlannerNode(planner_model)
    verifier = VerifierNode(verifier_model)
    executor = DeterministicExecutor()
    graph = build_runtime_graph(
        saver,
        planner=planner,
        executor=executor,
        verifier=verifier,
        interrupt_before=["verifier"],
    )
    initial = AgentState.initial(
        task_id=TASK_ID,
        task_run_id=RUN_ID,
        title=task.title,
        description=task.description,
    ).model_copy(update={"retry_count": 1, "replan_count": 1})
    await graph.ainvoke(
        initial.checkpoint_data(), config={"configurable": {"thread_id": THREAD_ID}}
    )

    service = TaskRuntimeService(saver, graph=graph)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)
    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.state.retry_count == 1
    assert result.state.replan_count == 1
    assert len(planner_calls) == 1
    assert len(verifier_calls) == 1


@pytest.mark.asyncio
async def test_running_missing_checkpoint_fails_closed_without_restart() -> None:
    task, run = _business_pair(TaskRunStatus.RUNNING)
    saver = MemorySaver()
    service = TaskRuntimeService(saver)
    fake_runs = FakeRuns(task, run)
    lifecycle = _lifecycle_for(run, task)
    session = _session()

    with (
        patch("service.task_runtime.TaskRunRepository", return_value=fake_runs),
        patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
    ):
        result = await service.execute_run(
            session, organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
        )

    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "checkpoint_missing"
    assert lifecycle.fail_run.await_count == 1
    assert saver.get_tuple({"configurable": {"thread_id": THREAD_ID}}) is None


@pytest.mark.asyncio
async def test_terminal_run_does_not_read_or_resume_checkpoint() -> None:
    task, run = _business_pair(TaskRunStatus.SUCCEEDED)

    class BombSaver(MemorySaver):
        async def aget_tuple(self, _config):
            raise AssertionError("terminal runs must not read checkpoints")

    service = TaskRuntimeService(BombSaver())
    fake_runs = FakeRuns(task, run)
    with patch("service.task_runtime.TaskRunRepository", return_value=fake_runs):
        with pytest.raises(TaskRuntimeConflictError, match="terminal"):
            await service.execute_run(
                _session(), organization_id=ORG, task_id=TASK_ID, task_run_id=RUN_ID
            )


@pytest.mark.asyncio
async def test_tenant_mismatch_is_rejected_before_checkpoint_lookup() -> None:
    task, run = _business_pair(TaskRunStatus.PENDING)

    class BombSaver(MemorySaver):
        async def aget_tuple(self, _config):
            raise AssertionError("tenant validation must precede checkpoint lookup")

    service = TaskRuntimeService(BombSaver())
    fake_runs = FakeRuns(task, run)
    with patch("service.task_runtime.TaskRunRepository", return_value=fake_runs):
        with pytest.raises(TaskRuntimeNotFoundError):
            await service.execute_run(
                _session(),
                organization_id=UUID("99999999-9999-4999-8999-999999999999"),
                task_id=TASK_ID,
                task_run_id=RUN_ID,
            )


def test_checkpoint_state_rejects_authority_and_stale_identity() -> None:
    service = TaskRuntimeService(MemorySaver())
    base_checkpoint = {
        "config": {"configurable": {"thread_id": THREAD_ID}},
        "checkpoint": {
            "channel_values": AgentState.initial(
                task_id=TASK_ID,
                task_run_id=RUN_ID,
                title="Task",
                description=None,
            ).checkpoint_data()
            | {"organization_id": str(ORG)},
        },
    }
    with pytest.raises(ValueError, match="authority"):
        service._state_from_checkpoint(SimpleNamespace(**base_checkpoint), THREAD_ID)

    stale = SimpleNamespace(
        config={
            "configurable": {"thread_id": "taskpilot-run:99999999-9999-4999-8999-999999999999"}
        },
        checkpoint=base_checkpoint["checkpoint"],
    )
    with pytest.raises(ValueError, match="stale"):
        service._state_from_checkpoint(stale, THREAD_ID)
