"""Real PostgreSQL/LangGraph evidence for runtime approval boundaries."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import func, inspect, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import (
    AgentRun,
    Approval,
    ApprovalActionState,
    ApprovalStatus,
    Membership,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    ToolCall,
    User,
)
from runtime import (
    AgentState,
    CapabilityDispatcher,
    CapabilityMetadata,
    DeterministicExecutor,
    ExecutionResult,
    PlannerNode,
    VerifierNode,
    build_runtime_graph,
)
from schema.planner import Plan
from service.approval_service import (
    ApprovalConflictError,
    ApprovalProposal,
    ApprovalRunNotActiveError,
    ApprovalService,
    ApprovedActionService,
)
from service.logging import reset_request_id, set_request_id
from service.session import CurrentPrincipal
from service.task_lifecycle import TaskLifecycleService
from service.task_runtime import (
    RuntimeApprovalResult,
    RuntimeExecutionResult,
    TaskRuntimeCheckpointError,
    TaskRuntimeConflictError,
    TaskRuntimeNotFoundError,
    TaskRuntimeService,
)

pytestmark = pytest.mark.postgres


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_t050_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    isolated_url = base.set(database=name).render_as_string(hide_password=False)
    async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
        config.attributes["database_url"] = isolated_url
        await asyncio.to_thread(command.upgrade, config, "head")
        yield isolated_url
    finally:
        async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
            await connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest_asyncio.fixture
async def runtime_environment() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], AsyncPostgresSaver]
]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        psycopg_url = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        try:
            async with AsyncConnectionPool(
                psycopg_url,
                min_size=1,
                max_size=4,
                kwargs={"autocommit": True, "row_factory": dict_row},
                check=AsyncConnectionPool.check_connection,
            ) as pool:
                saver = AsyncPostgresSaver(pool)  # type: ignore[bad-argument-type]
                await saver.setup()
                yield async_sessionmaker(engine, expire_on_commit=False), saver
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def seeded_task(
    runtime_environment: tuple[async_sessionmaker[AsyncSession], AsyncPostgresSaver],
) -> tuple[async_sessionmaker[AsyncSession], AsyncPostgresSaver, UUID, UUID, UUID]:
    session_factory, saver = runtime_environment
    organization_id, user_id, task_id = uuid4(), uuid4(), uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(Organization(id=organization_id, name=f"t050-{organization_id.hex}"))
            session.add(
                User(
                    id=user_id,
                    email=f"{user_id.hex}@example.com",
                    password_hash="x" * 20,
                )
            )
            await session.flush()
            session.add(
                Membership(user_id=user_id, organization_id=organization_id, role=Role.OWNER)
            )
            session.add(
                Task(
                    id=task_id,
                    organization_id=organization_id,
                    created_by_user_id=user_id,
                    title="T050 runtime",
                    description="checkpoint test",
                )
            )
    return session_factory, saver, organization_id, task_id, user_id


async def _principal_for(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: UUID,
    organization_id: UUID,
) -> CurrentPrincipal:
    async with session_factory() as session:
        membership = await session.scalar(
            select(Membership).where(
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
            )
        )
        assert membership is not None
        principal = CurrentPrincipal(
            user_id=user_id,
            membership_id=membership.id,
            organization_id=organization_id,
            role=membership.role,
            session_id=uuid4(),
        )
        await session.rollback()
        return principal


class _RiskCapability:
    def __init__(self, risk_level: str) -> None:
        self.metadata = CapabilityMetadata(
            name="deterministic_fixture",
            risk_level=risk_level,
            read_only=True,
            deterministic=True,
            side_effect_free=True,
        )
        self.calls = 0

    async def execute(self, step, context) -> ExecutionResult:  # noqa: ARG002
        self.calls += 1
        return ExecutionResult(
            step_position=step.position,
            success=True,
            output="risk-routed-fixture:v1",
        )


class _ApprovedActionFailurePlanner:
    async def __call__(self, request) -> object:  # noqa: ARG002
        return {
            "steps": [
                {
                    "position": 1,
                    "instruction": "[t084-mock-failure]",
                }
            ]
        }


def _risk_dispatcher(risk_level: str) -> tuple[CapabilityDispatcher, _RiskCapability]:
    capability = _RiskCapability(risk_level)
    return CapabilityDispatcher({"deterministic_fixture": capability}), capability


async def _wait_for_l2(seeded_task, *, planner=None):
    session_factory, saver, organization_id, task_id, user_id = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    principal = await _principal_for(session_factory, user_id, organization_id)
    dispatcher, capability = _risk_dispatcher("L2")
    runtime_service = TaskRuntimeService(
        saver,
        planner=planner,
        capability_dispatcher=dispatcher,
    )
    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )
    assert isinstance(result, RuntimeApprovalResult)
    return (
        session_factory,
        saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        result,
    )


async def _start_run(
    session_factory: async_sessionmaker[AsyncSession], task_id: UUID, organization_id: UUID
) -> UUID:
    async with session_factory() as session:
        run = await TaskLifecycleService(session).start_task(task_id, organization_id)
        return run.id


async def _begin_run(
    session_factory: async_sessionmaker[AsyncSession], task_id: UUID, organization_id: UUID
) -> UUID:
    async with session_factory() as session:
        run = await TaskLifecycleService(session).start_task(task_id, organization_id)
        run_id = run.id
    async with session_factory() as session:
        await TaskLifecycleService(session).begin_run(task_id, organization_id)
    return run_id


async def _read_run(
    session_factory: async_sessionmaker[AsyncSession], task_id: UUID, run_id: UUID
) -> tuple[TaskStatus, TaskRunStatus]:
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        run = await session.get_one(TaskRun, run_id)
        return task.status, run.status


async def _run_count(session_factory: async_sessionmaker[AsyncSession], task_id: UUID) -> int:
    async with session_factory() as session:
        count = await session.scalar(select(func.count()).where(TaskRun.task_id == task_id))
        return int(count or 0)


async def _seed_running_resume_checkpoint(
    session_factory: async_sessionmaker[AsyncSession],
    saver: AsyncPostgresSaver,
    task_id: UUID,
    task_run_id: UUID,
) -> str:
    """Create two durable progress points and leave the latest before success."""

    thread_id = f"taskpilot-run:{task_run_id}"
    config = {"configurable": {"thread_id": thread_id}}
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        assert task is not None
        plan = Plan.model_validate(
            {
                "steps": [
                    {"position": 1, "instruction": "Inspect the durable checkpoint"},
                    {"position": 2, "instruction": "Finish from the checkpoint"},
                ]
            }
        )
    initial = AgentState.initial(
        task_id=task_id,
        task_run_id=task_run_id,
        title=task.title,
        description=task.description,
    ).model_copy(update={"plan": plan, "retry_count": 1, "replan_count": 1})

    step_graph = build_runtime_graph(saver, interrupt_before=["verifier"])
    await step_graph.ainvoke(initial.checkpoint_data(), config=config)
    early_checkpoint = await saver.aget_tuple(config)
    assert early_checkpoint is not None
    await step_graph.ainvoke(None, config=config)
    later_checkpoint = await saver.aget_tuple(config)
    assert later_checkpoint is not None

    pause_graph = build_runtime_graph(saver, interrupt_before=["succeed"])
    await pause_graph.ainvoke(None, config=config)
    latest_checkpoint = await saver.aget_tuple(config)
    assert latest_checkpoint is not None

    inspector = TaskRuntimeService(saver)
    early_state = inspector._state_from_checkpoint(
        early_checkpoint, thread_id, task_id=task_id, task_run_id=task_run_id
    )
    later_state = inspector._state_from_checkpoint(
        later_checkpoint, thread_id, task_id=task_id, task_run_id=task_run_id
    )
    latest_state = inspector._state_from_checkpoint(
        latest_checkpoint, thread_id, task_id=task_id, task_run_id=task_run_id
    )
    assert early_state.plan_position == 0
    assert later_state.plan_position == 1
    assert latest_state.plan_position == 1
    assert latest_state.retry_count == 1
    assert latest_state.replan_count == 1
    assert latest_state.terminal_outcome is None
    return thread_id


class _CancelAfterGraphResume:
    """Simulate a worker interruption after graph checkpointing, before T035."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def ainvoke(self, input_data: Any, *, config: dict[str, Any]) -> Any:
        await self.graph.ainvoke(input_data, config=config)
        raise asyncio.CancelledError("simulated worker interruption before lifecycle commit")


class _ConcurrentGraphResume:
    """Align independent callers immediately before their graph resumes."""

    def __init__(self, graph: Any, barrier: asyncio.Barrier, thread_ids: list[str]) -> None:
        self.graph = graph
        self.barrier = barrier
        self.thread_ids = thread_ids

    async def ainvoke(self, input_data: Any, *, config: dict[str, Any]) -> Any:
        self.thread_ids.append(config["configurable"]["thread_id"])
        await self.barrier.wait()
        return await self.graph.ainvoke(input_data, config=config)


@pytest.mark.asyncio
async def test_postgres_pending_runtime_creates_checkpoint_and_succeeds(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)

    async with session_factory() as session:
        result = await TaskRuntimeService(saver).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    assert await _run_count(session_factory, task_id) == 1
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": f"taskpilot-run:{run_id}"}})
    assert checkpoint is not None


@pytest.mark.asyncio
async def test_postgres_runtime_persists_t093_correlation_chain(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    request_id = uuid4()
    request_token = set_request_id(str(request_id))
    try:
        async with session_factory() as session:
            result = await TaskRuntimeService(saver).execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
            )
    finally:
        reset_request_id(request_token)

    assert result.terminal_outcome == "SUCCEEDED"
    async with session_factory() as session:
        agent_runs = list(
            await session.scalars(select(AgentRun).where(AgentRun.task_run_id == run_id))
        )
        tool_calls = list(
            await session.scalars(
                select(ToolCall)
                .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
                .where(AgentRun.task_run_id == run_id)
            )
        )

    assert len(agent_runs) == len(tool_calls) == 1
    agent_run = agent_runs[0]
    tool_call = tool_calls[0]
    assert agent_run.request_id == request_id
    assert agent_run.task_run_id == run_id
    assert (agent_run.replan_count, agent_run.step_position, agent_run.retry_count) == (0, 0, 0)
    assert agent_run.agent_name == "executor"
    assert tool_call.agent_run_id == agent_run.id
    assert tool_call.call_index == 0
    assert not hasattr(agent_run, "task_id")
    assert not hasattr(tool_call, "organization_id")


@pytest.mark.asyncio
@pytest.mark.parametrize("risk_level", ["L0", "L1"])
async def test_postgres_l0_l1_actions_remain_automatically_allowed(seeded_task, risk_level) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    dispatcher, capability = _risk_dispatcher(risk_level)

    async with session_factory() as session:
        result = await TaskRuntimeService(
            saver,
            capability_dispatcher=dispatcher,
        ).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "SUCCEEDED"
    assert capability.calls == 1
    async with session_factory() as session:
        assert await session.scalar(select(Approval.id)) is None


@pytest.mark.asyncio
async def test_postgres_l2_wait_checkpoint_and_approved_resume(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, user_id = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    principal = await _principal_for(session_factory, user_id, organization_id)
    dispatcher, capability = _risk_dispatcher("L2")
    runtime_service = TaskRuntimeService(saver, capability_dispatcher=dispatcher)

    async with session_factory() as session:
        waiting = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(waiting, RuntimeApprovalResult)
    assert waiting.status == "WAITING_APPROVAL"
    assert waiting.task_run_status is TaskRunStatus.RUNNING
    assert set(waiting.model_dump()) == {
        "task_id",
        "task_run_id",
        "checkpoint_thread_id",
        "task_run_status",
        "status",
        "approval_id",
    }
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )

    thread_id = f"taskpilot-run:{run_id}"
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert checkpoint is not None
    saved_state = runtime_service._state_from_checkpoint(
        checkpoint,
        thread_id,
        task_id=task_id,
        task_run_id=run_id,
    )
    assert saved_state.pending_approval is not None
    reference = checkpoint.checkpoint["channel_values"]["pending_approval"]
    assert set(reference) == {"approval_id", "replan_count", "step_position"}
    assert reference == {
        "approval_id": str(waiting.approval_id),
        "replan_count": 0,
        "step_position": 0,
    }
    assert "status" not in reference
    assert "proposed_action" not in reference
    assert checkpoint.checkpoint["channel_values"]["capability_context"] is None
    assert "membership_id" not in checkpoint.checkpoint["channel_values"]
    assert "organization_id" not in checkpoint.checkpoint["channel_values"]

    async with session_factory() as session:
        approvals = await ApprovalService(session).list_for_task_run(
            principal,
            task_id=task_id,
            task_run_id=run_id,
        )
    assert len(approvals) == 1
    approval = approvals[0]
    assert approval.id == waiting.approval_id
    assert approval.status is ApprovalStatus.PENDING
    assert (approval.task_run_id, approval.replan_count, approval.step_position) == (
        run_id,
        0,
        0,
    )

    async with session_factory() as session:
        repeated_wait = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )
    assert isinstance(repeated_wait, RuntimeApprovalResult)
    assert repeated_wait.status == "WAITING_APPROVAL"
    assert repeated_wait.approval_id == waiting.approval_id

    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
    async with session_factory() as session:
        completed = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(completed, RuntimeExecutionResult)
    assert completed.terminal_outcome == "SUCCEEDED"
    assert completed.state.execution_result == ExecutionResult(
        step_position=1,
        success=True,
        output="approved-mock-action-completed:v1",
    )
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
    assert approval.action_state is ApprovalActionState.COMPLETED
    assert approval.outcome == completed.state.execution_result.model_dump(mode="json")
    assert approval.action_finished_at is not None
    checkpoint = await saver.aget_tuple({"configurable": {"thread_id": f"taskpilot-run:{run_id}"}})
    assert checkpoint is not None
    checkpoint_state = runtime_service._state_from_checkpoint(
        checkpoint,
        f"taskpilot-run:{run_id}",
        task_id=task_id,
        task_run_id=run_id,
    )
    assert checkpoint_state.pending_approval is not None
    assert checkpoint_state.pending_approval.approval_id == waiting.approval_id
    assert checkpoint_state.execution_result == completed.state.execution_result


@pytest.mark.asyncio
async def test_postgres_concurrent_approved_runtime_resume_has_one_winner(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )

    async def resume() -> RuntimeExecutionResult:
        async with session_factory() as session:
            result = await runtime_service.execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=principal,
            )
        assert isinstance(result, RuntimeExecutionResult)
        return result

    results = await asyncio.gather(resume(), resume(), return_exceptions=True)
    completed = [
        result
        for result in results
        if isinstance(result, RuntimeExecutionResult) and result.terminal_outcome == "SUCCEEDED"
    ]
    conflicts = [result for result in results if isinstance(result, TaskRuntimeConflictError)]
    assert len(completed) == 1
    assert len(conflicts) == 1
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
    assert approval.action_state is ApprovalActionState.COMPLETED
    assert approval.outcome == completed[0].state.execution_result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_postgres_approved_action_replay_returns_one_committed_outcome(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        _capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
        approval = await session.get_one(Approval, waiting.approval_id)
        proposal = ApprovalProposal(
            action_name=approval.action_name,
            action_version=approval.action_version,
            proposed_action=approval.proposed_action,
        )

    async with session_factory() as session:
        first = await ApprovedActionService(session).execute(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            replan_count=0,
            step_position=0,
            proposal=proposal,
        )
    async with session_factory() as session:
        replay = await ApprovedActionService(session).execute(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            replan_count=0,
            step_position=0,
            proposal=proposal,
        )

    assert first == replay
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
    assert approval.action_state is ApprovalActionState.COMPLETED
    assert approval.outcome == replay.model_dump(mode="json")


@pytest.mark.asyncio
async def test_postgres_approved_action_transaction_failure_rolls_back_and_replays(
    seeded_task,
    monkeypatch,
) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        _capability,
        _runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        proposal = ApprovalProposal(
            action_name=approval.action_name,
            action_version=approval.action_version,
            proposed_action=approval.proposed_action,
        )
        original_commit = session.commit

        async def fail_before_commit() -> None:
            raise OSError("simulated approved-action transaction failure")

        monkeypatch.setattr(session, "commit", fail_before_commit)
        with pytest.raises(OSError, match="transaction failure"):
            await ApprovedActionService(session).execute(
                principal,
                task_id=task_id,
                task_run_id=run_id,
                approval_id=waiting.approval_id,
                replan_count=0,
                step_position=0,
                proposal=proposal,
            )
        monkeypatch.setattr(session, "commit", original_commit)

    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        assert approval.action_state is ApprovalActionState.AVAILABLE
        assert approval.outcome is None
        assert approval.action_finished_at is None
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )

    async with session_factory() as session:
        replay = await ApprovedActionService(session).execute(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            replan_count=0,
            step_position=0,
            proposal=proposal,
        )
    assert replay.success is True
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        assert approval.action_state is ApprovalActionState.COMPLETED
        assert approval.outcome == replay.model_dump(mode="json")
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1


@pytest.mark.asyncio
async def test_postgres_failed_approved_action_replay_returns_persisted_failure(
    seeded_task,
    monkeypatch,
) -> None:
    (
        session_factory,
        _saver,
        _organization_id,
        task_id,
        run_id,
        principal,
        capability,
        _runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task, planner=PlannerNode(_ApprovedActionFailurePlanner()))
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        proposal = ApprovalProposal(
            action_name=approval.action_name,
            action_version=approval.action_version,
            proposed_action=approval.proposed_action,
        )
        first = await ApprovedActionService(session).execute(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            replan_count=0,
            step_position=0,
            proposal=proposal,
        )
    assert first.success is False
    assert capability.calls == 0

    def should_not_recalculate(cls, proposal, step_position):  # noqa: ARG001
        raise AssertionError("FAILED action was recalculated")

    monkeypatch.setattr(ApprovedActionService, "_mock_outcome", classmethod(should_not_recalculate))
    async with session_factory() as session:
        replay = await ApprovedActionService(session).execute(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            replan_count=0,
            step_position=0,
            proposal=proposal,
        )
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        assert approval.action_state is ApprovalActionState.FAILED
        assert approval.outcome == first.model_dump(mode="json")
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1
    assert replay == first


@pytest.mark.asyncio
async def test_postgres_approved_action_failure_is_durable_and_terminal(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(
        seeded_task,
        planner=PlannerNode(_ApprovedActionFailurePlanner()),
    )
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.execution_result is not None
    assert result.state.execution_result.success is False
    assert result.state.execution_result.error_code == "approved_mock_failure"
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
    assert approval.action_state is ApprovalActionState.FAILED
    assert approval.outcome == result.state.execution_result.model_dump(mode="json")


@pytest.mark.asyncio
async def test_postgres_approved_action_rejects_mismatch_and_cancelled_run(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        _capability,
        _runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )
        approval = await session.get_one(Approval, waiting.approval_id)
        proposal = ApprovalProposal(
            action_name=approval.action_name,
            action_version=approval.action_version,
            proposed_action=approval.proposed_action | {"tampered": True},
        )

    with pytest.raises(ApprovalConflictError):
        async with session_factory() as session:
            await ApprovedActionService(session).execute(
                principal,
                task_id=task_id,
                task_run_id=run_id,
                approval_id=waiting.approval_id,
                replan_count=0,
                step_position=0,
                proposal=proposal,
            )
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
    assert approval.action_state is ApprovalActionState.AVAILABLE
    assert approval.outcome is None

    async with session_factory() as session:
        await TaskLifecycleService(session).cancel_task(task_id, organization_id)
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
        original = ApprovalProposal(
            action_name=approval.action_name,
            action_version=approval.action_version,
            proposed_action=approval.proposed_action,
        )
    with pytest.raises(ApprovalRunNotActiveError):
        async with session_factory() as session:
            await ApprovedActionService(session).execute(
                principal,
                task_id=task_id,
                task_run_id=run_id,
                approval_id=waiting.approval_id,
                replan_count=0,
                step_position=0,
                proposal=original,
            )
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.CANCELLED,
        TaskRunStatus.CANCELLED,
    )


@pytest.mark.asyncio
async def test_postgres_l2_foreign_tenant_cannot_resume_checkpoint(seeded_task) -> None:
    (
        session_factory,
        _saver,
        _organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    foreign_organization_id = uuid4()
    foreign_principal = CurrentPrincipal(
        user_id=uuid4(),
        membership_id=uuid4(),
        organization_id=foreign_organization_id,
        role=Role.OWNER,
        session_id=uuid4(),
    )

    with pytest.raises(TaskRuntimeNotFoundError):
        async with session_factory() as session:
            await runtime_service.execute_run(
                session,
                organization_id=foreign_organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=foreign_principal,
            )

    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )
    async with session_factory() as session:
        approvals = await ApprovalService(session).list_for_task_run(
            principal,
            task_id=task_id,
            task_run_id=run_id,
        )
    assert [approval.id for approval in approvals] == [waiting.approval_id]


@pytest.mark.asyncio
async def test_postgres_l2_resume_revalidates_active_membership(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        _waiting,
    ) = await _wait_for_l2(seeded_task)
    stale_principal = CurrentPrincipal(
        user_id=principal.user_id,
        membership_id=uuid4(),
        organization_id=principal.organization_id,
        role=principal.role,
        session_id=principal.session_id,
    )

    with pytest.raises(TaskRuntimeConflictError, match="principal"):
        async with session_factory() as session:
            await runtime_service.execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=stale_principal,
            )

    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )


@pytest.mark.asyncio
async def test_postgres_l3_is_blocked_without_approval_or_dispatch(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    dispatcher, capability = _risk_dispatcher("L3")

    async with session_factory() as session:
        result = await TaskRuntimeService(
            saver,
            capability_dispatcher=dispatcher,
        ).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "risk_level_blocked"
    assert capability.calls == 0
    async with session_factory() as session:
        assert await session.scalar(select(Approval.id)) is None


@pytest.mark.asyncio
async def test_postgres_rejected_approval_fails_through_t035(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).reject(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
            reason="Not approved for this task.",
        )

    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "approval_rejected"
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["approval_id", "step_position"])
async def test_postgres_mismatched_approval_checkpoint_fails_closed(seeded_task, mismatch) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    config = {"configurable": {"thread_id": f"taskpilot-run:{run_id}"}}
    reference = {
        "approval_id": str(uuid4() if mismatch == "approval_id" else waiting.approval_id),
        "replan_count": 0,
        "step_position": 1 if mismatch == "step_position" else 0,
    }
    await runtime_service.graph.aupdate_state(
        config,
        {"pending_approval": reference},
    )

    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "approval_checkpoint_invalid"
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )
    async with session_factory() as session:
        approval = await session.get(Approval, waiting.approval_id)
    assert approval is not None
    assert approval.status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_postgres_resume_rejects_changed_runtime_action_proposal(seeded_task) -> None:
    (
        session_factory,
        saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    approval_id = waiting.approval_id
    thread_id = f"taskpilot-run:{run_id}"
    config = {"configurable": {"thread_id": thread_id}}

    checkpoint = await saver.aget_tuple(config)
    assert checkpoint is not None
    checkpoint_state = runtime_service._state_from_checkpoint(
        checkpoint,
        thread_id,
        task_id=task_id,
        task_run_id=run_id,
    )
    assert checkpoint_state.plan is not None
    current_step = checkpoint_state.plan.steps[checkpoint_state.plan_position]
    changed_step = current_step.model_copy(
        update={"instruction": "Changed action proposal after approval creation."}
    )
    changed_steps = list(checkpoint_state.plan.steps)
    changed_steps[checkpoint_state.plan_position] = changed_step
    changed_plan = checkpoint_state.plan.model_copy(update={"steps": changed_steps})
    await runtime_service.graph.aupdate_state(
        config,
        {"plan": changed_plan.model_dump(mode="json")},
    )
    changed_checkpoint = await saver.aget_tuple(config)
    assert changed_checkpoint is not None
    resumed_state = runtime_service._state_from_checkpoint(
        changed_checkpoint,
        thread_id,
        task_id=task_id,
        task_run_id=run_id,
    )
    assert resumed_state.plan is not None
    assert (
        resumed_state.plan.steps[resumed_state.plan_position].instruction
        == changed_step.instruction
    )

    async with session_factory() as session:
        approval_before = await session.get_one(Approval, approval_id)
        approval_snapshot = (
            approval_before.proposed_action,
            approval_before.status,
            approval_before.action_state,
            approval_before.requester_membership_id,
            approval_before.decider_membership_id,
            approval_before.decided_at,
            approval_before.created_at,
            approval_before.updated_at,
            approval_before.outcome,
            approval_before.action_finished_at,
        )
        task_before = await session.get_one(Task, task_id)
        task_details = (task_before.title, task_before.description)
    assert approval_snapshot[0]["current_step"]["instruction"] == current_step.instruction
    assert approval_snapshot[0]["current_step"]["instruction"] != changed_step.instruction

    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "approval_checkpoint_invalid"
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )

    async with session_factory() as session:
        approval_after = await session.get_one(Approval, approval_id)
        assert (
            approval_after.proposed_action,
            approval_after.status,
            approval_after.action_state,
            approval_after.requester_membership_id,
            approval_after.decider_membership_id,
            approval_after.decided_at,
            approval_after.created_at,
            approval_after.updated_at,
            approval_after.outcome,
            approval_after.action_finished_at,
        ) == approval_snapshot
        assert await session.scalar(select(func.count()).select_from(Approval)) == 1
        task_after = await session.get_one(Task, task_id)
        assert (task_after.title, task_after.description) == task_details
    assert await _run_count(session_factory, task_id) == 1


@pytest.mark.asyncio
async def test_postgres_malformed_approval_checkpoint_fails_closed(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    config = {"configurable": {"thread_id": f"taskpilot-run:{run_id}"}}
    await runtime_service.graph.aupdate_state(
        config,
        {
            "pending_approval": {
                "approval_id": str(waiting.approval_id),
                "replan_count": 0,
                "step_position": 0,
                "status": "APPROVED",
            }
        },
    )

    async with session_factory() as session:
        result = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(result, RuntimeExecutionResult)
    assert result.terminal_outcome == "FAILED"
    assert result.state.failure is not None
    assert result.state.failure.code == "checkpoint_corrupt"
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )


@pytest.mark.asyncio
async def test_postgres_cancelled_run_wins_over_pending_approval(seeded_task) -> None:
    (
        session_factory,
        _saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        _waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await TaskLifecycleService(session).cancel_task(task_id, organization_id)

    with pytest.raises(TaskRuntimeConflictError, match="terminal"):
        async with session_factory() as session:
            await runtime_service.execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=principal,
            )

    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.CANCELLED,
        TaskRunStatus.CANCELLED,
    )


@pytest.mark.asyncio
async def test_postgres_l2_requires_principal_before_beginning_run(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _user_id = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    dispatcher, capability = _risk_dispatcher("L2")

    with pytest.raises(TaskRuntimeConflictError, match="principal"):
        async with session_factory() as session:
            await TaskRuntimeService(
                saver,
                capability_dispatcher=dispatcher,
            ).execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
            )

    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.QUEUED,
        TaskRunStatus.PENDING,
    )
    async with session_factory() as session:
        assert await session.scalar(select(Approval.id)) is None


@pytest.mark.asyncio
async def test_postgres_checkpoint_write_failure_replays_same_approval(
    seeded_task,
    monkeypatch,
) -> None:
    session_factory, saver, organization_id, task_id, user_id = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    principal = await _principal_for(session_factory, user_id, organization_id)
    dispatcher, capability = _risk_dispatcher("L2")

    original_aput = saver.aput
    failure_state = {"failed": False}

    async def fail_once_on_approval_reference(config, checkpoint, metadata, new_versions):
        channel_values = checkpoint.get("channel_values", {})
        if not failure_state["failed"] and channel_values.get("pending_approval") is not None:
            failure_state["failed"] = True
            raise OSError("simulated checkpoint write failure")
        return await original_aput(config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(saver, "aput", fail_once_on_approval_reference)
    runtime_service = TaskRuntimeService(
        saver,
        capability_dispatcher=dispatcher,
    )

    with pytest.raises(TaskRuntimeCheckpointError, match="replayed"):
        async with session_factory() as session:
            await runtime_service.execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=principal,
            )

    assert failure_state["failed"]
    assert capability.calls == 0
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )
    async with session_factory() as session:
        approvals = await ApprovalService(session).list_for_task_run(
            principal,
            task_id=task_id,
            task_run_id=run_id,
        )
    assert len(approvals) == 1
    original_approval_id = approvals[0].id

    async with session_factory() as session:
        replay = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )

    assert isinstance(replay, RuntimeApprovalResult)
    assert replay.status == "WAITING_APPROVAL"
    assert replay.approval_id == original_approval_id
    assert capability.calls == 0
    async with session_factory() as session:
        approvals = await ApprovalService(session).list_for_task_run(
            principal,
            task_id=task_id,
            task_run_id=run_id,
        )
    assert len(approvals) == 1


@pytest.mark.asyncio
async def test_postgres_committed_action_survives_checkpoint_failure_and_recovers(
    seeded_task,
    monkeypatch,
) -> None:
    (
        session_factory,
        saver,
        organization_id,
        task_id,
        run_id,
        principal,
        capability,
        runtime_service,
        waiting,
    ) = await _wait_for_l2(seeded_task)
    async with session_factory() as session:
        await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=waiting.approval_id,
        )

    original_aput = saver.aput
    failed = {"value": False}

    async def fail_after_action_commit(config, checkpoint, metadata, new_versions):
        channel_values = checkpoint.get("channel_values", {})
        if not failed["value"] and channel_values.get("execution_result") is not None:
            failed["value"] = True
            raise OSError("simulated post-commit checkpoint failure")
        return await original_aput(config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(saver, "aput", fail_after_action_commit)
    with pytest.raises(TaskRuntimeCheckpointError, match="replayed"):
        async with session_factory() as session:
            await runtime_service.execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
                principal=principal,
            )
    assert failed["value"]
    assert capability.calls == 0

    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        assert approval.action_state is ApprovalActionState.COMPLETED
        committed_outcome = ExecutionResult.model_validate(approval.outcome)
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )

    async with session_factory() as session:
        recovered = await runtime_service.execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
            principal=principal,
        )
    assert isinstance(recovered, RuntimeExecutionResult)
    assert recovered.terminal_outcome == "SUCCEEDED"
    assert recovered.state.execution_result == committed_outcome
    assert recovered.state.verification is not None
    assert capability.calls == 0
    async with session_factory() as session:
        approval = await session.get_one(Approval, waiting.approval_id)
        assert approval is not None
        assert approval.action_state is ApprovalActionState.COMPLETED
        assert approval.outcome == committed_outcome.model_dump(mode="json")
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )


@pytest.mark.asyncio
async def test_postgres_runtime_failure_transitions_the_same_run_to_failed(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)

    async with session_factory() as session:
        result = await TaskRuntimeService(
            saver,
            executor=DeterministicExecutor(failure_mode="always_fail"),
        ).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.terminal_outcome == "FAILED"
    assert result.state.retry_count == 1
    assert result.state.failure is not None
    assert result.state.failure.code == "retry_budget_exhausted"
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )
    assert await _run_count(session_factory, task_id) == 1


@pytest.mark.asyncio
async def test_postgres_running_missing_checkpoint_fails_closed(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _begin_run(session_factory, task_id, organization_id)

    async with session_factory() as session:
        result = await TaskRuntimeService(saver).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.state.failure is not None
    assert result.state.failure.code == "checkpoint_missing"
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )


@pytest.mark.asyncio
async def test_postgres_terminal_run_does_not_resume_or_create_new_run(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)

    async with session_factory() as session:
        await TaskRuntimeService(saver).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    with pytest.raises(TaskRuntimeConflictError, match="terminal"):
        async with session_factory() as session:
            await TaskRuntimeService(saver).execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
            )

    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    assert await _run_count(session_factory, task_id) == 1


@pytest.mark.asyncio
async def test_postgres_resume_uses_latest_checkpoint_and_preserves_counters(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _begin_run(session_factory, task_id, organization_id)
    thread_id = f"taskpilot-run:{run_id}"

    planner_calls = 0
    verifier_calls = 0

    async def planner_model(_request) -> object:
        nonlocal planner_calls
        planner_calls += 1
        return {"steps": [{"position": 1, "instruction": "Inspect"}]}

    async def verifier_model(_request) -> object:
        nonlocal verifier_calls
        verifier_calls += 1
        return {"verdict": "PASS", "reason": "Passed", "evidence": []}

    graph = build_runtime_graph(
        saver,
        planner=PlannerNode(planner_model),
        verifier=VerifierNode(verifier_model),
        interrupt_before=["verifier"],
    )
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        assert task is not None
        initial = AgentState.initial(
            task_id=task_id,
            task_run_id=run_id,
            title=task.title,
            description=task.description,
        ).model_copy(update={"retry_count": 1, "replan_count": 1})
    await graph.ainvoke(
        initial.checkpoint_data(), config={"configurable": {"thread_id": thread_id}}
    )

    async with session_factory() as session:
        result = await TaskRuntimeService(saver, graph=graph).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.state.retry_count == 1
    assert result.state.replan_count == 1
    assert planner_calls == 1
    assert verifier_calls == 1


@pytest.mark.asyncio
async def test_postgres_repeated_running_resume_uses_latest_checkpoint(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _begin_run(session_factory, task_id, organization_id)
    thread_id = await _seed_running_resume_checkpoint(session_factory, saver, task_id, run_id)

    pause_graph = build_runtime_graph(saver, interrupt_before=["succeed"])
    with pytest.raises(asyncio.CancelledError):
        async with session_factory() as session:
            await TaskRuntimeService(
                saver,
                graph=_CancelAfterGraphResume(pause_graph),
            ).execute_run(
                session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
            )

    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.RUNNING,
        TaskRunStatus.RUNNING,
    )
    interrupted_checkpoint = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert interrupted_checkpoint is not None
    interrupted_state = TaskRuntimeService(saver)._state_from_checkpoint(
        interrupted_checkpoint,
        thread_id,
        task_id=task_id,
        task_run_id=run_id,
    )
    assert interrupted_state.plan_position == 1
    assert interrupted_state.retry_count == 1
    assert interrupted_state.replan_count == 1
    assert interrupted_state.terminal_outcome == "SUCCEEDED"

    async with session_factory() as session:
        result = await TaskRuntimeService(saver).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.terminal_outcome == "SUCCEEDED"
    assert result.checkpoint_thread_id == thread_id
    assert result.state.plan_position == 1
    assert result.state.retry_count == 1
    assert result.state.replan_count == 1
    assert result.state.plan is not None
    assert result.state.plan.steps[1].instruction == "Finish from the checkpoint"
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    assert await _run_count(session_factory, task_id) == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_running_resume_preserves_one_terminal_winner(
    seeded_task,
) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _begin_run(session_factory, task_id, organization_id)
    thread_id = await _seed_running_resume_checkpoint(session_factory, saver, task_id, run_id)
    barrier = asyncio.Barrier(2)
    thread_ids: list[str] = []

    async def invoke() -> str:
        graph = build_runtime_graph(saver)
        runtime_service = TaskRuntimeService(
            saver,
            graph=_ConcurrentGraphResume(graph, barrier, thread_ids),
        )
        async with session_factory() as session:
            try:
                await runtime_service.execute_run(
                    session,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=run_id,
                )
                return "success"
            except TaskRuntimeConflictError:
                return "conflict"

    assert sorted(await asyncio.gather(invoke(), invoke())) == ["conflict", "success"]
    assert thread_ids == [thread_id, thread_id]
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )
    assert await _run_count(session_factory, task_id) == 1
    latest = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert latest is not None
    latest_state = TaskRuntimeService(saver)._state_from_checkpoint(
        latest,
        thread_id,
        task_id=task_id,
        task_run_id=run_id,
    )
    assert latest_state.retry_count == 1
    assert latest_state.replan_count == 1


@pytest.mark.asyncio
async def test_postgres_concurrent_start_has_one_terminal_lifecycle_winner(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)

    async def invoke() -> str:
        async with session_factory() as session:
            try:
                await TaskRuntimeService(saver).execute_run(
                    session,
                    organization_id=organization_id,
                    task_id=task_id,
                    task_run_id=run_id,
                )
                return "success"
            except TaskRuntimeConflictError:
                return "conflict"

    assert sorted(await asyncio.gather(invoke(), invoke())) == ["conflict", "success"]
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.SUCCEEDED,
        TaskRunStatus.SUCCEEDED,
    )


@pytest.mark.asyncio
async def test_postgres_stale_checkpoint_identity_fails_closed(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _begin_run(session_factory, task_id, organization_id)
    thread_id = f"taskpilot-run:{run_id}"
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        assert task is not None
        stale_state = AgentState.initial(
            task_id=uuid4(),
            task_run_id=run_id,
            title=task.title,
            description=task.description,
        )
    graph = build_runtime_graph(saver, interrupt_before=["verifier"])
    await graph.ainvoke(
        stale_state.checkpoint_data(), config={"configurable": {"thread_id": thread_id}}
    )

    async with session_factory() as session:
        result = await TaskRuntimeService(saver).execute_run(
            session,
            organization_id=organization_id,
            task_id=task_id,
            task_run_id=run_id,
        )

    assert result.state.failure is not None
    assert result.state.failure.code == "checkpoint_corrupt"
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.FAILED,
        TaskRunStatus.FAILED,
    )


@pytest.mark.asyncio
async def test_postgres_tenant_scope_precedes_checkpoint_identity(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    with pytest.raises(TaskRuntimeNotFoundError):
        async with session_factory() as session:
            await TaskRuntimeService(saver).execute_run(
                session,
                organization_id=uuid4(),
                task_id=task_id,
                task_run_id=run_id,
            )
    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.QUEUED,
        TaskRunStatus.PENDING,
    )


@pytest.mark.asyncio
async def test_postgres_cancellation_wins_against_late_runtime_completion(seeded_task) -> None:
    session_factory, saver, organization_id, task_id, _ = seeded_task
    run_id = await _start_run(session_factory, task_id, organization_id)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocking_verifier(_request) -> object:
        entered.set()
        await release.wait()
        return {"verdict": "PASS", "reason": "Passed", "evidence": []}

    graph = build_runtime_graph(saver, verifier=VerifierNode(blocking_verifier))
    runtime_service = TaskRuntimeService(saver, graph=graph)
    async with session_factory() as runtime_session:
        execution = asyncio.create_task(
            runtime_service.execute_run(
                runtime_session,
                organization_id=organization_id,
                task_id=task_id,
                task_run_id=run_id,
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=10)
        async with session_factory() as cancellation_session:
            await TaskLifecycleService(cancellation_session).cancel_task(task_id, organization_id)
        release.set()
        with pytest.raises(TaskRuntimeConflictError):
            await execution

    assert await _read_run(session_factory, task_id, run_id) == (
        TaskStatus.CANCELLED,
        TaskRunStatus.CANCELLED,
    )


@pytest.mark.asyncio
async def test_postgres_langgraph_tables_are_not_taskpilot_migrations(seeded_task) -> None:
    session_factory, _, _, _, _ = seeded_task
    async with session_factory() as session:
        taskpilot_tables = set(
            await session.run_sync(
                lambda sync_session: inspect(sync_session.connection()).get_table_names(
                    schema="taskpilot"
                )
            )
        )
        public_checkpoint = await session.scalar(text("SELECT to_regclass('public.checkpoints')"))
    assert "checkpoints" not in taskpilot_tables
    assert public_checkpoint == "checkpoints"
