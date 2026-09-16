"""Async Alembic environment for TaskPilot-owned metadata only."""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from core.settings import settings
from persistence.base import Base
from persistence.engine import normalize_business_database_url
from persistence.migration_filters import include_name, include_object
from persistence.models import Organization, User  # noqa: F401 - register model metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def get_url() -> str:
    if database_url := config.attributes.get("database_url"):
        return normalize_business_database_url(database_url)
    if settings.TASKPILOT_DATABASE_URL is None:
        raise ValueError("TASKPILOT_DATABASE_URL must be set before running Alembic")
    return normalize_business_database_url(settings.TASKPILOT_DATABASE_URL)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema="taskpilot",
        version_table="alembic_version",
    )
    context.execute("CREATE SCHEMA IF NOT EXISTS taskpilot;")
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # Alembic creates its version table before executing the first revision, so
    # the owned schema must exist first. This idempotent bootstrap touches no
    # public/LangGraph object.
    connection.execute(text("CREATE SCHEMA IF NOT EXISTS taskpilot"))
    connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        include_object=include_object,
        version_table_schema="taskpilot",
        version_table="alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {**config.get_section(config.config_ini_section, {}), "sqlalchemy.url": get_url()},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    if sys.platform == "win32":
        asyncio.run(run_async_migrations(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
