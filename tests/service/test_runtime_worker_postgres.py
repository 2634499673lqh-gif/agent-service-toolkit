"""PostgreSQL evidence for durable TaskRun claim and stale-lease recovery."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import Membership, Organization, Role, Task, TaskRun, User
from persistence.repositories import TaskRunRepository
from service.task_lifecycle import TaskLifecycleService

pytestmark = pytest.mark.postgres


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_t122_{uuid4().hex}"
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
async def worker_context() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], UUID, UUID, UUID]
]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        organization_id = uuid4()
        user_id = uuid4()
        task_id = uuid4()
        async with factory() as session:
            async with session.begin():
                session.add(Organization(id=organization_id, name="T122 worker org"))
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id.hex}@example.com",
                        password_hash="worker-test-hash",
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
                        title="T122 worker task",
                    )
                )
        async with factory() as session:
            await TaskLifecycleService(session).start_task(task_id, organization_id)
        try:
            yield factory, organization_id, task_id, user_id
        finally:
            await engine.dispose()


async def _claim(factory: async_sessionmaker[AsyncSession], worker_id: str, *, now) -> UUID | None:
    async with factory() as session:
        claimed = await TaskRunRepository(session).claim_next_for_worker(
            worker_id,
            lease_seconds=30,
            now=now,
        )
        await session.commit()
        return None if claimed is None else claimed[1].id


async def _run_id(factory: async_sessionmaker[AsyncSession], task_id: UUID) -> UUID:
    async with factory() as session:
        run = await session.scalar(select(TaskRun).where(TaskRun.task_id == task_id))
        assert run is not None
        return run.id


@pytest.mark.asyncio
async def test_concurrent_workers_claim_one_run(worker_context) -> None:
    factory, _organization_id, task_id, _user_id = worker_context
    now = datetime.now(UTC)
    first, second = await asyncio.gather(
        _claim(factory, "worker-a", now=now),
        _claim(factory, "worker-b", now=now),
    )
    assert sorted(value for value in (first, second) if value is not None) == [
        await _run_id(factory, task_id)
    ]


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_with_incremented_attempt(worker_context) -> None:
    factory, _organization_id, task_id, _user_id = worker_context
    claimed_at = datetime.now(UTC)
    run_id = await _claim(factory, "worker-a", now=claimed_at)
    assert run_id == await _run_id(factory, task_id)
    reclaimed = await _claim(factory, "worker-b", now=claimed_at + timedelta(seconds=31))
    assert reclaimed == run_id
    async with factory() as session:
        run = await session.get_one(TaskRun, run_id)
        assert run.worker_id == "worker-b"
        assert run.worker_attempts == 2
