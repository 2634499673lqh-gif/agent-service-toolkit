"""Disposable PostgreSQL verification for T021 migration coexistence."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio

try:
    from alembic import command
    from alembic.config import Config
except ModuleNotFoundError:
    pytest.skip("HOST_ENVIRONMENT: Alembic dependency is not installed", allow_module_level=True)

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore
from psycopg import AsyncConnection, errors, sql
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy import inspect, make_url, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from persistence.engine import create_async_engine, create_session_factory, get_business_session
from persistence.models import Organization
from persistence.repositories import OrganizationRepository

pytestmark = pytest.mark.postgres
EXPECTED_REVISION = "t021_organization"


def _configured_test_url() -> str:
    database_url = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("HOST_ENVIRONMENT: TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(database_url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return database_url


@asynccontextmanager
async def disposable_database(base_url: str) -> AsyncIterator[str]:
    """Create and destroy one uniquely named database; never alter the base DB."""

    base = make_url(base_url)
    base_name = base.database or ""
    if "test" not in base_name.casefold():
        raise RuntimeError("Refusing database lifecycle operation outside a test database")
    database_name = f"taskpilot_t021_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    isolated_url = base.set(database=database_name).render_as_string(hide_password=False)
    created = False
    try:
        async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
            try:
                await connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )
            except errors.InsufficientPrivilege as exc:
                raise RuntimeError(
                    "TASKPILOT_TEST_DATABASE_URL role must have CREATE DATABASE privilege; "
                    "refusing to fall back to a shared database"
                ) from exc
        created = True
        yield isolated_url
    finally:
        if created:
            async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
                await connection.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(database_name)
                    )
                )


def _alembic_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[2]
    config = Config(root / "alembic.ini")
    config.attributes["database_url"] = database_url
    return config


async def _run_alembic(config: Config, operation: str, revision: str) -> None:
    await asyncio.to_thread(getattr(command, operation), config, revision)


async def _langgraph_roundtrip(database_url: str, key: str) -> None:
    psycopg_url = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    async with AsyncConnectionPool(
        psycopg_url,
        min_size=1,
        max_size=1,
        kwargs={"autocommit": True, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        await AsyncPostgresSaver(pool).setup()  # type: ignore[bad-argument-type]
        store = AsyncPostgresStore(pool)
        await store.setup()
        await store.aput(("t021",), key, {"value": key}, index=False)
        item = await store.aget(("t021",), key)
        assert item is not None
        assert item.value == {"value": key}


async def _langgraph_read(database_url: str, key: str) -> None:
    psycopg_url = (
        make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )
    async with AsyncConnectionPool(
        psycopg_url,
        min_size=1,
        max_size=1,
        kwargs={"autocommit": True, "row_factory": dict_row},
        check=AsyncConnectionPool.check_connection,
    ) as pool:
        item = await AsyncPostgresStore(pool).aget(("t021",), key)
        assert item is not None
        assert item.value == {"value": key}


async def _assert_taskpilot_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        schemas = set(await connection.run_sync(lambda conn: inspect(conn).get_schema_names()))
        tables = set(
            await connection.run_sync(
                lambda conn: inspect(conn).get_table_names(schema="taskpilot")
            )
        )
        revision = await connection.scalar(
            text("SELECT version_num FROM taskpilot.alembic_version")
        )
        columns = {
            column["name"]: column
            for column in await connection.run_sync(
                lambda conn: inspect(conn).get_columns("organizations", schema="taskpilot")
            )
        }
        version_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.alembic_version"},
        )
        organizations_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.organizations"},
        )
        checks = await connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("organizations", schema="taskpilot")
        )
    assert "taskpilot" in schemas
    assert tables == {"alembic_version", "organizations"}
    assert revision == EXPECTED_REVISION
    assert version_relation == "taskpilot.alembic_version"
    assert organizations_relation == "taskpilot.organizations"
    assert set(columns) == {"id", "name", "is_active", "created_at", "updated_at"}
    assert str(columns["id"]["type"]) == "UUID"
    assert columns["created_at"]["type"].timezone is True
    assert columns["updated_at"]["type"].timezone is True
    assert {constraint["name"] for constraint in checks} == {
        "ck_organizations_organization_name_not_blank"
    }


@pytest.mark.asyncio
async def test_scenario_a_langgraph_then_taskpilot_and_downgrade() -> None:
    async with disposable_database(_configured_test_url()) as database_url:
        config = _alembic_config(database_url)
        engine = create_async_engine(database_url)
        try:
            await _langgraph_roundtrip(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "downgrade", "base")
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            await _langgraph_read(database_url, "scenario-a")
        finally:
            await engine.dispose()


@pytest_asyncio.fixture
async def migrated_engine() -> AsyncIterator[AsyncEngine]:
    async with disposable_database(_configured_test_url()) as database_url:
        config = _alembic_config(database_url)
        engine = create_async_engine(database_url)
        try:
            async with engine.connect() as connection:
                schemas = set(
                    await connection.run_sync(lambda conn: inspect(conn).get_schema_names())
                )
                assert "taskpilot" not in schemas
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.organizations"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.alembic_version"},
                    )
                    is None
                )
            await _run_alembic(config, "upgrade", "head")
            yield engine
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_scenario_b_taskpilot_then_langgraph(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Scenario B")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(organization.id) is not None
    await _langgraph_roundtrip(
        migrated_engine.url.render_as_string(hide_password=False), "scenario-b"
    )
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(organization.id) is not None


@pytest.mark.asyncio
async def test_transactions_sessions_and_name_constraints(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    committed = Organization(name="Committed")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(committed)
    async with get_business_session(session_factory) as session:
        loaded = await OrganizationRepository(session).get(committed.id)
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert loaded.updated_at.utcoffset().total_seconds() == 0

    rolled_back_id = uuid4()
    with pytest.raises(RuntimeError, match="force rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await OrganizationRepository(session).add(
                    Organization(id=rolled_back_id, name="Rolled back")
                )
                raise RuntimeError("force rollback")
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(rolled_back_id) is None

    uncommitted_id = uuid4()
    async with get_business_session(session_factory) as session:
        await OrganizationRepository(session).add(
            Organization(id=uncommitted_id, name="Not committed")
        )
    async with get_business_session(session_factory) as session:
        assert await OrganizationRepository(session).get(uncommitted_id) is None

    session_one = session_factory()
    session_two = session_factory()
    try:
        await session_one.begin()
        await OrganizationRepository(session_one).add(Organization(name="Isolated"))
        result = await session_two.scalars(
            select(Organization).where(Organization.name == "Isolated")
        )
        assert result.one_or_none() is None
    finally:
        await session_one.rollback()
        await session_one.close()
        await session_two.close()

    for invalid_name in ("   ", "\t\n"):
        with pytest.raises(Exception):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await OrganizationRepository(session).add(Organization(name=invalid_name))
