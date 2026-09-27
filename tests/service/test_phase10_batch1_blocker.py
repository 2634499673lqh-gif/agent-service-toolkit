import asyncio
import threading
from contextlib import asynccontextmanager, suppress

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from schema.schema import StreamInput
from service import service
from service.runtime_capacity import RuntimeCapacity


@pytest.mark.asyncio
async def test_invoke_endpoint_returns_fixed_429_when_runtime_is_saturated(
    test_client, mock_agent, monkeypatch
):
    capacity = RuntimeCapacity(1)
    assert capacity.try_acquire()
    monkeypatch.setattr(service, "runtime_capacity", capacity)

    response = await asyncio.to_thread(
        test_client.post, "/invoke", json={"message": "blocked while another run executes"}
    )
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    capacity.release()


@pytest.mark.asyncio
async def test_concurrent_invoke_saturation_is_endpoint_visible(
    test_client, mock_agent, monkeypatch
):
    capacity = RuntimeCapacity(1)
    monkeypatch.setattr(service, "runtime_capacity", capacity)
    entered = threading.Event()
    release = threading.Event()

    async def slow_invoke(*_args, **_kwargs):
        entered.set()
        await asyncio.to_thread(release.wait, 2)
        return [("values", {"messages": [AIMessage(content="done")]})]

    mock_agent.ainvoke.side_effect = slow_invoke
    first = asyncio.create_task(
        asyncio.to_thread(test_client.post, "/invoke", json={"message": "first"})
    )
    assert await asyncio.to_thread(entered.wait, 2)
    second = await asyncio.to_thread(test_client.post, "/invoke", json={"message": "second"})
    assert second.status_code == 429
    assert second.headers["retry-after"] == "1"
    release.set()
    assert (await first).status_code == 200


@pytest.mark.asyncio
async def test_runtime_failure_releases_endpoint_capacity(test_client, mock_agent, monkeypatch):
    capacity = RuntimeCapacity(1)
    monkeypatch.setattr(service, "runtime_capacity", capacity)
    mock_agent.ainvoke.side_effect = RuntimeError("bounded test failure")

    failed = await asyncio.to_thread(test_client.post, "/invoke", json={"message": "fails"})
    assert failed.status_code == 500
    # A new request can acquire the slot, proving the failed request released it.
    assert capacity.try_acquire()
    capacity.release()


@pytest.mark.asyncio
async def test_stream_cancellation_releases_endpoint_capacity(monkeypatch):
    capacity = RuntimeCapacity(1)
    monkeypatch.setattr(service, "runtime_capacity", capacity)
    stopped = asyncio.Event()

    async def cancelled_generator(*_args):
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            stopped.set()

    monkeypatch.setattr(service, "message_generator", cancelled_generator)
    response = await service.stream(StreamInput(message="cancel me"))
    task = asyncio.create_task(response.body_iterator.__anext__())
    await asyncio.sleep(0)
    assert capacity.try_acquire() is False
    task.cancel()
    with suppress(asyncio.CancelledError, StopAsyncIteration):
        await task
    assert stopped.is_set()
    assert capacity.try_acquire() is True
    capacity.release()


@pytest.mark.asyncio
async def test_liveness_and_auth_crud_are_not_runtime_capacity_rejections(test_client, monkeypatch):
    capacity = RuntimeCapacity(1)
    assert capacity.try_acquire()
    monkeypatch.setattr(service, "runtime_capacity", capacity)

    from service import auth_dependency

    service.app.dependency_overrides[auth_dependency.get_session_factory] = lambda: _FakeFactory()
    try:
        safe_client = TestClient(service.app, raise_server_exceptions=False)
        live = await asyncio.to_thread(safe_client.get, "/health/live")
        auth = await asyncio.to_thread(safe_client.post, "/api/v1/auth/login", json={})
        crud = await asyncio.to_thread(safe_client.get, "/api/v1/tasks")
        assert live.status_code == 200
        assert auth.status_code == 401
        assert crud.status_code == 401
    finally:
        service.app.dependency_overrides.pop(auth_dependency.get_session_factory, None)
        capacity.release()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, _query):
        return object()

    async def close(self):
        return None

    async def rollback(self):
        return None


class _FakeFactory:
    def __call__(self):
        return _FakeSession()


class _FakePoolConnection:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def execute(self, _query):
        return object()


class _FakePool:
    def connection(self):
        return _FakePoolConnection()


class _FakePersistence:
    def __init__(self):
        self.conn = _FakePool()


class _SlowPoolConnection(_FakePoolConnection):
    async def execute(self, _query):
        await asyncio.sleep(0.05)


class _SlowPool(_FakePool):
    def connection(self):
        return _SlowPoolConnection()


@pytest.mark.asyncio
async def test_readiness_unconfigured_persistence_is_dependency_free(monkeypatch):
    monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence_configured", False, raising=False)
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", None)
    result = await service.health_ready()
    assert result == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_database_failure_and_recovery(monkeypatch):
    monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence_configured", False, raising=False)
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", "postgresql://u:p@localhost/db")

    class DownFactory:
        def __call__(self):
            raise RuntimeError("database down")

    monkeypatch.setattr(service, "get_business_session_factory", DownFactory())
    with pytest.raises(HTTPException) as error:
        await service.health_ready()
    assert error.value.status_code == 503

    monkeypatch.setattr(service, "get_business_session_factory", _FakeFactory())
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", None)
    assert await service.health_ready() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_checks_configured_langgraph_persistence(monkeypatch):
    monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence_configured", True, raising=False)
    monkeypatch.setattr(
        service.app.state,
        "langgraph_persistence",
        (_FakePersistence(), _FakePersistence()),
        raising=False,
    )
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", None)
    assert await service.health_ready() == {"status": "ok"}


@pytest.mark.asyncio
async def test_readiness_reports_startup_failure(monkeypatch):
    monkeypatch.setattr(service.app.state, "startup_complete", False, raising=False)
    with pytest.raises(HTTPException) as error:
        await service.health_ready()
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_lifespan_startup_failure_keeps_readiness_false(monkeypatch):
    @asynccontextmanager
    async def broken_database():
        raise RuntimeError("startup database failure")
        yield

    monkeypatch.setattr(service, "initialize_database", broken_database)
    with pytest.raises(RuntimeError, match="startup database failure"):
        async with service.lifespan(service.app):
            pass
    assert service.app.state.startup_complete is False


@pytest.mark.asyncio
async def test_readiness_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence_configured", True, raising=False)
    slow = _FakePersistence()
    slow.conn = _SlowPool()
    monkeypatch.setattr(service.app.state, "langgraph_persistence", (slow, slow), raising=False)
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", None)
    monkeypatch.setattr(service.settings, "READINESS_DATABASE_TIMEOUT", 0.001)
    with pytest.raises(HTTPException) as error:
        await service.health_ready()
    assert error.value.status_code == 503


def test_pool_checkout_timeout_is_public_503(monkeypatch):
    class TimeoutFactory:
        def __call__(self):
            raise SQLAlchemyTimeoutError("pool checkout timeout")

    from service import auth_dependency

    service.app.dependency_overrides[auth_dependency.get_session_factory] = lambda: TimeoutFactory()
    try:
        client = TestClient(service.app, raise_server_exceptions=False)
        response = client.post("/api/v1/auth/login", json={})
    finally:
        service.app.dependency_overrides.pop(auth_dependency.get_session_factory, None)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
