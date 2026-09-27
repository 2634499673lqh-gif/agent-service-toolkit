from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from core.settings import Settings
from persistence import engine as db_engine
from persistence.models import TaskRunStatus, TaskStatus
from service.runtime_capacity import RuntimeCapacity, RuntimeCapacityFull
from service.task_lifecycle import TaskLifecycleConflictError, TaskLifecycleService


def test_business_engine_has_explicit_bounded_pool(monkeypatch) -> None:
    captured = {}

    def fake_create(url, **options):
        captured.update(options)
        return object()

    monkeypatch.setattr(db_engine, "sqlalchemy_create_async_engine", fake_create)
    config = Settings(USE_FAKE_MODEL=True, TASKPILOT_DATABASE_URL="postgresql://u:p@localhost/db")
    result = db_engine.create_async_engine(config=config)
    assert result is not None
    assert captured["pool_pre_ping"] is True
    assert captured["max_overflow"] == (
        config.POSTGRES_MAX_CONNECTIONS_PER_POOL - config.POSTGRES_MIN_CONNECTIONS_PER_POOL
    )
    assert captured["pool_timeout"] == config.POSTGRES_POOL_CHECKOUT_TIMEOUT
    assert captured["pool_recycle"] == config.POSTGRES_POOL_RECYCLE_SECONDS


@pytest.mark.asyncio
async def test_runtime_capacity_saturates_and_releases() -> None:
    capacity = RuntimeCapacity(1)
    assert capacity.try_acquire() is True
    with pytest.raises(RuntimeCapacityFull):
        async with capacity.slot():
            pass
    capacity.release()
    async with capacity.slot():
        pass
    assert capacity.try_acquire() is True
    capacity.release()


@pytest.mark.asyncio
async def test_terminal_transition_rejects_stale_task_run_identity() -> None:
    task_id, org_id = uuid4(), uuid4()
    actual_run_id, stale_run_id = uuid4(), uuid4()
    task = SimpleNamespace(id=task_id, status=TaskStatus.RUNNING)
    run = SimpleNamespace(id=actual_run_id, status=TaskRunStatus.RUNNING)
    session = SimpleNamespace(flush=AsyncMock(), rollback=AsyncMock(), commit=AsyncMock())
    service = TaskLifecycleService(session)  # type: ignore[arg-type]
    service.tasks.get_for_update_in_principal_tenant = AsyncMock(return_value=task)
    service.runs.get_active_for_update = AsyncMock(return_value=run)

    with pytest.raises(TaskLifecycleConflictError, match="identity is stale"):
        await service.succeed_run(task_id, org_id, stale_run_id)
    assert run.status is TaskRunStatus.RUNNING
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_engine_disposal_recreates_factory(monkeypatch) -> None:
    created = []

    class FakeEngine:
        async def dispose(self):
            created.append("disposed")

    monkeypatch.setattr(db_engine, "create_async_engine", lambda: FakeEngine())
    db_engine.get_business_engine.cache_clear()
    db_engine.get_business_session_factory.cache_clear()
    first = db_engine.get_business_session_factory()
    await db_engine.dispose_business_engine()
    second = db_engine.get_business_session_factory()
    assert first is not second
    assert created == ["disposed"]
    db_engine.get_business_session_factory.cache_clear()
    db_engine.get_business_engine.cache_clear()
