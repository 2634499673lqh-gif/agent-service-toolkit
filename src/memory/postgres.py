import logging
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import URL, make_url

from core.settings import settings

logger = logging.getLogger(__name__)


def validate_postgres_config() -> None:
    """
    Validate that all required PostgreSQL configuration is present.
    Raises ValueError if any required configuration is missing.
    """
    required_vars = [
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "POSTGRES_DB",
    ]

    missing = [var for var in required_vars if not getattr(settings, var, None)]
    if missing:
        raise ValueError(
            f"Missing required PostgreSQL configuration: {', '.join(missing)}. "
            "These environment variables must be set to use PostgreSQL persistence."
        )

    if settings.POSTGRES_MIN_CONNECTIONS_PER_POOL > settings.POSTGRES_MAX_CONNECTIONS_PER_POOL:
        raise ValueError(
            f"POSTGRES_MIN_CONNECTIONS_PER_POOL ({settings.POSTGRES_MIN_CONNECTIONS_PER_POOL}) must be less than or equal to POSTGRES_MAX_CONNECTIONS_PER_POOL ({settings.POSTGRES_MAX_CONNECTIONS_PER_POOL})"
        )


def get_postgres_connection_string() -> str:
    """Build the canonical SQLAlchemy PostgreSQL URL from settings."""
    if settings.POSTGRES_PASSWORD is None:
        raise ValueError("POSTGRES_PASSWORD is not set")
    return URL.create(
        drivername="postgresql+psycopg",
        username=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD.get_secret_value(),
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
    ).render_as_string(hide_password=False)


def to_psycopg_pool_url(database_url: str) -> str:
    """Adapt a SQLAlchemy PostgreSQL URL to the libpq syntax required by psycopg_pool."""
    return (
        make_url(database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _pool_connection_string() -> str:
    return to_psycopg_pool_url(get_postgres_connection_string())


@asynccontextmanager
async def get_postgres_saver():
    """Initialize and return a PostgreSQL saver instance based on a connection pool for more resilent connections."""
    validate_postgres_config()
    application_name = settings.POSTGRES_APPLICATION_NAME + "-" + "saver"

    async with AsyncConnectionPool(
        _pool_connection_string(),
        min_size=settings.POSTGRES_MIN_CONNECTIONS_PER_POOL,
        max_size=settings.POSTGRES_MAX_CONNECTIONS_PER_POOL,
        timeout=settings.POSTGRES_POOL_CHECKOUT_TIMEOUT,
        max_lifetime=float(settings.POSTGRES_POOL_RECYCLE_SECONDS),
        # Langgraph requires autocommmit=true and row_factory to be set to dict_row.
        # Application_name is passed so you can identify the connection in your Postgres database connection manager.
        kwargs={"autocommit": True, "row_factory": dict_row, "application_name": application_name},
        # makes sure that the connection is still valid before using it
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        try:
            checkpointer = AsyncPostgresSaver(pool)  # type: ignore[bad-argument-type]
            await checkpointer.setup()
            yield checkpointer
        finally:
            await pool.close()


@asynccontextmanager
async def get_postgres_store():
    """
    Get a PostgreSQL store instance based on a connection pool for more resilent connections.

    Returns an AsyncPostgresStore instance that can be used with async context manager pattern.

    """
    validate_postgres_config()
    application_name = settings.POSTGRES_APPLICATION_NAME + "-" + "store"

    async with AsyncConnectionPool(
        _pool_connection_string(),
        min_size=settings.POSTGRES_MIN_CONNECTIONS_PER_POOL,
        max_size=settings.POSTGRES_MAX_CONNECTIONS_PER_POOL,
        timeout=settings.POSTGRES_POOL_CHECKOUT_TIMEOUT,
        max_lifetime=float(settings.POSTGRES_POOL_RECYCLE_SECONDS),
        # Langgraph requires autocommmit=true and row_factory to be set to dict_row
        # Application_name is passed so you can identify the connection in your Postgres database connection manager.
        kwargs={"autocommit": True, "row_factory": dict_row, "application_name": application_name},
        # makes sure that the connection is still valid before using it
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        try:
            store = AsyncPostgresStore(pool)  # type: ignore[bad-argument-type]
            await store.setup()
            yield store
        finally:
            await pool.close()
