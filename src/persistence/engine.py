"""Async SQLAlchemy engine and session lifecycle for TaskPilot business data."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any

from pydantic import SecretStr
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine as sqlalchemy_create_async_engine,
)

from core.settings import Settings, settings


def normalize_business_database_url(database_url: str | SecretStr) -> str:
    """Validate and normalize a PostgreSQL URL for psycopg 3 async use.

    Credentials are never included in errors.  ``postgresql://`` is accepted
    as shorthand and converted to SQLAlchemy's explicit async psycopg dialect.
    """

    raw_url = (
        database_url.get_secret_value() if isinstance(database_url, SecretStr) else database_url
    )
    if not raw_url or not raw_url.strip():
        raise ValueError("TASKPILOT_DATABASE_URL must be set for business persistence")
    try:
        parsed = make_url(raw_url)
    except Exception:
        raise ValueError("TASKPILOT_DATABASE_URL is not a valid PostgreSQL URL") from None
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    elif parsed.drivername != "postgresql+psycopg":
        raise ValueError("TaskPilot business persistence requires a PostgreSQL psycopg URL")
    return parsed.render_as_string(hide_password=False)


def _configured_url(config: Settings) -> str:
    if config.TASKPILOT_DATABASE_URL is None:
        raise ValueError("TASKPILOT_DATABASE_URL must be set for business persistence")
    return normalize_business_database_url(config.TASKPILOT_DATABASE_URL)


def create_async_engine(
    database_url: str | SecretStr | None = None,
    *,
    config: Settings = settings,
    **engine_options: Any,
) -> AsyncEngine:
    """Create a business engine; unlike LangGraph, this can never use SQLite."""

    url = (
        normalize_business_database_url(database_url)
        if database_url is not None
        else _configured_url(config)
    )
    options: dict[str, Any] = {
        "pool_pre_ping": True,
        "pool_size": config.POSTGRES_MIN_CONNECTIONS_PER_POOL,
        "max_overflow": max(
            0, config.POSTGRES_MAX_CONNECTIONS_PER_POOL - config.POSTGRES_MIN_CONNECTIONS_PER_POOL
        ),
        "pool_timeout": config.POSTGRES_POOL_CHECKOUT_TIMEOUT,
        "pool_recycle": config.POSTGRES_POOL_RECYCLE_SECONDS,
        "connect_args": {"application_name": config.POSTGRES_APPLICATION_NAME + "-business"},
    }
    options.update(engine_options)
    return sqlalchemy_create_async_engine(url, **options)


@lru_cache(maxsize=1)
def get_business_engine() -> AsyncEngine:
    return create_async_engine()


@lru_cache(maxsize=1)
def get_business_session_factory() -> async_sessionmaker[AsyncSession]:
    return create_session_factory(get_business_engine())


async def dispose_business_engine() -> None:
    """Dispose the process-owned business pool during application shutdown."""
    if get_business_engine.cache_info().currsize:
        engine = get_business_engine()
        await engine.dispose()
        get_business_session_factory.cache_clear()
        get_business_engine.cache_clear()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a reusable factory; sessions are not global and do not auto-commit."""

    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_business_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Acquire, rollback on error, and close one request/service session."""

    async with session_factory() as session:
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
