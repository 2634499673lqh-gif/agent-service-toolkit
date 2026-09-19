"""Real PostgreSQL evidence for the T035 lifecycle service."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url, select, text
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
from service.task_lifecycle import (
    TaskLifecycleConflictError,
    TaskLifecycleInconsistentStateError,
    TaskLifecycleService,
    TaskNotFoundError,
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
    name = f"taskpilot_t035_{uuid4().hex}"
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
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        try:
            yield async_sessionmaker(engine, expire_on_commit=False)
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def seeded_task(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[object, object, object]:
    organization_id = uuid4()
    user_id = uuid4()
    task_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add(Organization(id=organization_id, name=f"t035-{organization_id.hex}"))
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
                    title="T035",
                )
            )
    return organization_id, user_id, task_id


async def _load_task(session_factory: async_sessionmaker[AsyncSession], task_id: object) -> Task:
    async with session_factory() as session:
        return await session.get_one(Task, task_id)


@pytest.mark.asyncio
async def test_postgres_lifecycle_matrix_and_retry_history(session_factory, seeded_task) -> None:
    organization_id, _, task_id = seeded_task
    async with session_factory() as session:
        service = TaskLifecycleService(session)
        first = await service.start_task(task_id, organization_id)
    assert (await _load_task(session_factory, task_id)).status is TaskStatus.QUEUED
    assert first.run_number == 1

    async with session_factory() as session:
        service = TaskLifecycleService(session)
        await service.begin_run(task_id, organization_id)
        await service.fail_run(task_id, organization_id)
    async with session_factory() as session:
        service = TaskLifecycleService(session)
        second = await service.start_task(task_id, organization_id)
    assert second.run_number == 2
    async with session_factory() as session:
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == task_id))).all()
        )
        assert {run.status for run in runs} == {TaskRunStatus.FAILED, TaskRunStatus.PENDING}

    async with session_factory() as session:
        service = TaskLifecycleService(session)
        await service.begin_run(task_id, organization_id)
        await service.succeed_run(task_id, organization_id)
    assert (await _load_task(session_factory, task_id)).status is TaskStatus.SUCCEEDED

    async with session_factory() as session:
        with pytest.raises(TaskLifecycleConflictError):
            await TaskLifecycleService(session).cancel_task(task_id, organization_id)


@pytest.mark.asyncio
async def test_postgres_cancellation_and_inconsistent_state(session_factory, seeded_task) -> None:
    organization_id, _, task_id = seeded_task
    async with session_factory() as session:
        await TaskLifecycleService(session).cancel_task(task_id, organization_id)
    assert (await _load_task(session_factory, task_id)).status is TaskStatus.CANCELLED
    async with session_factory() as session:
        assert await TaskLifecycleService(session).cancel_task(task_id, organization_id)
        with pytest.raises(TaskLifecycleConflictError):
            await TaskLifecycleService(session).start_task(task_id, organization_id)

    organization_id, _, task_id = seeded_task
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        task.status = TaskStatus.QUEUED
        await session.commit()
    async with session_factory() as session:
        with pytest.raises(TaskLifecycleInconsistentStateError):
            await TaskLifecycleService(session).begin_run(task_id, organization_id)


@pytest.mark.asyncio
async def test_postgres_queued_and_running_cancellation_are_persisted(
    session_factory, seeded_task
) -> None:
    organization_id, user_id, task_id = seeded_task
    async with session_factory() as session:
        await TaskLifecycleService(session).start_task(task_id, organization_id)
    async with session_factory() as session:
        await TaskLifecycleService(session).cancel_task(task_id, organization_id)
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == task_id))).all()
        )
        assert task.status is TaskStatus.CANCELLED
        assert len(runs) == 1
        assert runs[0].status is TaskRunStatus.CANCELLED

    second_id = uuid4()
    async with session_factory() as session:
        session.add(
            Task(
                id=second_id,
                organization_id=organization_id,
                created_by_user_id=user_id,
                title="T035 running cancellation",
            )
        )
        await session.commit()
    async with session_factory() as session:
        service = TaskLifecycleService(session)
        await service.start_task(second_id, organization_id)
        await service.begin_run(second_id, organization_id)
        await service.cancel_task(second_id, organization_id)
    async with session_factory() as session:
        task = await session.get_one(Task, second_id)
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == second_id))).all()
        )
        assert task.status is TaskStatus.CANCELLED
        assert len(runs) == 1
        assert runs[0].status is TaskRunStatus.CANCELLED


@pytest.mark.asyncio
async def test_postgres_concurrent_start_has_one_winner(session_factory, seeded_task) -> None:
    organization_id, _, task_id = seeded_task
    reached_boundary = asyncio.Event()
    boundary_count = 0
    boundary_guard = asyncio.Lock()

    async def before_task_lock() -> None:
        nonlocal boundary_count
        async with boundary_guard:
            boundary_count += 1
            if boundary_count == 2:
                reached_boundary.set()
        await reached_boundary.wait()

    async def attempt() -> str:
        async with session_factory() as session:
            try:
                await session.execute(text("SELECT 1"))
                await TaskLifecycleService(session, before_task_lock=before_task_lock).start_task(
                    task_id, organization_id
                )
                return "won"
            except TaskLifecycleConflictError:
                return "conflict"

    results = await asyncio.gather(attempt(), attempt())
    assert sorted(results) == ["conflict", "won"]
    async with session_factory() as session:
        task = await session.get_one(Task, task_id)
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == task_id))).all()
        )
        assert task.status is TaskStatus.QUEUED
        assert len(runs) == 1
        assert runs[0].status is TaskRunStatus.PENDING
        assert runs[0].run_number == 1


@pytest.mark.asyncio
async def test_postgres_foreign_task_is_invisible_to_lifecycle(
    session_factory, seeded_task
) -> None:
    organization_id, _, task_id = seeded_task
    foreign_organization = uuid4()
    async with session_factory() as session:
        with pytest.raises(TaskNotFoundError):
            await TaskLifecycleService(session).start_task(task_id, foreign_organization)
    assert (await _load_task(session_factory, task_id)).status is TaskStatus.DRAFT
