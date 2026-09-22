"""Real PostgreSQL/LangGraph evidence for the T050 runtime boundary."""

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
    Organization,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    User,
)
from runtime import (
    AgentState,
    DeterministicExecutor,
    PlannerNode,
    VerifierNode,
    build_runtime_graph,
)
from schema.planner import Plan
from service.task_lifecycle import TaskLifecycleService
from service.task_runtime import (
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
                Task(
                    id=task_id,
                    organization_id=organization_id,
                    created_by_user_id=user_id,
                    title="T050 runtime",
                    description="checkpoint test",
                )
            )
    return session_factory, saver, organization_id, task_id, user_id


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
