import asyncio
from uuid import uuid4

import pytest

from service.runtime_worker import ClaimedTaskRun, TaskRunWorker


def _claimed() -> ClaimedTaskRun:
    return ClaimedTaskRun(
        organization_id=uuid4(),
        task_id=uuid4(),
        task_run_id=uuid4(),
        worker_attempts=1,
    )


@pytest.mark.asyncio
async def test_worker_run_once_executes_claimed_run_and_releases_lease(monkeypatch) -> None:
    worker = TaskRunWorker(
        object(),
        poll_interval=0.01,
        lease_seconds=1.0,
        max_attempts=3,
        worker_id="test-worker",
    )
    claimed = _claimed()
    events: list[str] = []

    async def claim():
        return claimed

    async def execute(_claimed):
        events.append("execute")

    async def heartbeat(_run_id):
        events.append("heartbeat")

    async def release(_run_id):
        events.append("release")

    monkeypatch.setattr(worker, "_claim", claim)
    monkeypatch.setattr(worker, "_execute", execute)
    monkeypatch.setattr(worker, "_heartbeat", heartbeat)
    monkeypatch.setattr(worker, "_release", release)

    assert await worker.run_once() is True
    assert events[0] == "execute"
    assert events[-1] == "release"


@pytest.mark.asyncio
async def test_worker_run_once_returns_false_when_queue_is_empty(monkeypatch) -> None:
    worker = TaskRunWorker(
        object(),
        poll_interval=0.01,
        lease_seconds=1.0,
        max_attempts=3,
        worker_id="test-worker",
    )

    async def claim():
        return None

    monkeypatch.setattr(worker, "_claim", claim)
    assert await worker.run_once() is False


@pytest.mark.asyncio
async def test_worker_close_stops_background_loop() -> None:
    worker = TaskRunWorker(
        object(),
        poll_interval=0.01,
        lease_seconds=1.0,
        max_attempts=3,
        worker_id="test-worker",
    )
    worker._run_loop = lambda: asyncio.sleep(60)  # type: ignore[method-assign]
    await worker.start()
    await worker.close()
    assert worker._task is None
