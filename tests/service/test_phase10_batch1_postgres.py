import os
from types import SimpleNamespace

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import make_url, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import async_sessionmaker

from memory.postgres import to_psycopg_pool_url
from persistence.engine import create_async_engine
from service import service

pytestmark = pytest.mark.postgres


def _database_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    return value


@pytest.mark.asyncio
async def test_real_postgres_pool_checkout_timeout_and_recreation() -> None:
    engine = create_async_engine(
        _database_url(),
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.1,
        pool_recycle=60,
    )
    try:
        first = await engine.connect()
        try:
            await first.execute(text("SELECT 1"))
            with pytest.raises(SQLAlchemyTimeoutError):
                await engine.connect()
        finally:
            await first.close()
        await engine.dispose()
        recreated = create_async_engine(
            _database_url(),
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.1,
            pool_recycle=60,
        )
        try:
            async with recreated.connect() as connection:
                result = await connection.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
        finally:
            await recreated.dispose()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_real_postgres_readiness_failure_and_recovery(monkeypatch) -> None:
    url = _database_url()
    engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence_configured", False, raising=False)
    monkeypatch.setattr(service.app.state, "langgraph_persistence", None, raising=False)
    monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", url)
    monkeypatch.setattr(service, "get_business_session_factory", lambda: factory)
    unavailable = None
    recovered = None
    try:
        transport = httpx.ASGITransport(app=service.app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            initial = await client.get("/health/ready")
            assert initial.status_code == 200, initial.text
            await engine.dispose()
            unavailable_url = make_url(url).set(port=1).render_as_string(hide_password=False)
            unavailable = create_async_engine(
                unavailable_url,
                pool_size=1,
                max_overflow=0,
                pool_timeout=0.1,
                connect_args={"connect_timeout": 1},
            )
            monkeypatch.setattr(
                service,
                "get_business_session_factory",
                lambda: async_sessionmaker(unavailable, expire_on_commit=False),
            )
            monkeypatch.setattr(service.settings, "READINESS_DATABASE_TIMEOUT", 0.5)
            failed = await client.get("/health/ready")
            assert failed.status_code == 503
            assert failed.json() == {"detail": "Database is not ready"}
            recovered = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.1)
            monkeypatch.setattr(
                service,
                "get_business_session_factory",
                lambda: async_sessionmaker(recovered, expire_on_commit=False),
            )
            restored = await client.get("/health/ready")
            assert restored.status_code == 200
    finally:
        await engine.dispose()
        if unavailable is not None:
            await unavailable.dispose()
        if recovered is not None:
            await recovered.dispose()


@pytest.mark.asyncio
async def test_real_postgres_configured_langgraph_readiness(monkeypatch) -> None:
    url = _database_url()
    async with (
        AsyncConnectionPool(
            to_psycopg_pool_url(url), min_size=1, max_size=1, timeout=0.5
        ) as checkpoint_pool,
        AsyncConnectionPool(
            to_psycopg_pool_url(url), min_size=1, max_size=1, timeout=0.5
        ) as store_pool,
    ):
        monkeypatch.setattr(service.app.state, "startup_complete", True, raising=False)
        monkeypatch.setattr(
            service.app.state, "langgraph_persistence_configured", True, raising=False
        )
        monkeypatch.setattr(
            service.app.state,
            "langgraph_persistence",
            (SimpleNamespace(conn=checkpoint_pool), SimpleNamespace(conn=store_pool)),
            raising=False,
        )
        monkeypatch.setattr(service.settings, "TASKPILOT_DATABASE_URL", None)
        assert await service.health_ready() == {"status": "ok"}
