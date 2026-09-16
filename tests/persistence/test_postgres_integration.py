"""Disposable PostgreSQL verification for TaskPilot migration coexistence."""

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
from sqlalchemy import delete, insert, inspect, make_url, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from persistence.engine import create_async_engine, create_session_factory, get_business_session
from persistence.models import Membership, Organization, Role, User
from persistence.repositories import (
    MembershipRepository,
    OrganizationRepository,
    UserRepository,
)

pytestmark = pytest.mark.postgres
T021_REVISION = "t021_organization"
T022_REVISION = "t022_user"
T022A_REVISION = "t022a_membership"
EXPECTED_REVISION = T022A_REVISION


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
    database_name = f"taskpilot_t022a_{uuid4().hex}"
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


async def _assert_taskpilot_schema(
    engine: AsyncEngine,
    *,
    expected_revision: str = EXPECTED_REVISION,
    users_expected: bool = True,
    memberships_expected: bool = True,
) -> None:
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
        # The users relation is absent after a T022 -> T021 downgrade, so only
        # reflect it when this revision is expected to own it.
        user_columns = (
            {
                column["name"]: column
                for column in await connection.run_sync(
                    lambda conn: inspect(conn).get_columns("users", schema="taskpilot")
                )
            }
            if users_expected
            else {}
        )
        version_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.alembic_version"},
        )
        organizations_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.organizations"},
        )
        users_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.users"},
        )
        checks = await connection.run_sync(
            lambda conn: inspect(conn).get_check_constraints("organizations", schema="taskpilot")
        )
        user_checks = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        user_unique_constraints = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_unique_constraints("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        user_indexes = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("users", schema="taskpilot")
            )
            if users_expected
            else []
        )
        # The memberships relation only exists from the T022A revision onward.
        membership_columns = (
            {
                column["name"]: column
                for column in await connection.run_sync(
                    lambda conn: inspect(conn).get_columns("memberships", schema="taskpilot")
                )
            }
            if memberships_expected
            else {}
        )
        memberships_relation = await connection.scalar(
            text("SELECT to_regclass(:qualified_name)"),
            {"qualified_name": "taskpilot.memberships"},
        )
        membership_checks = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_check_constraints("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_unique_constraints = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_unique_constraints("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_indexes = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_indexes("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
        membership_foreign_keys = (
            await connection.run_sync(
                lambda conn: inspect(conn).get_foreign_keys("memberships", schema="taskpilot")
            )
            if memberships_expected
            else []
        )
    assert "taskpilot" in schemas
    expected_tables = {"alembic_version", "organizations"}
    if users_expected:
        expected_tables.add("users")
    if memberships_expected:
        expected_tables.add("memberships")
    assert tables == expected_tables
    assert revision == expected_revision
    assert version_relation == "taskpilot.alembic_version"
    assert organizations_relation == "taskpilot.organizations"
    assert set(columns) == {"id", "name", "is_active", "created_at", "updated_at"}
    assert str(columns["id"]["type"]) == "UUID"
    assert columns["created_at"]["type"].timezone is True
    assert columns["updated_at"]["type"].timezone is True
    assert {constraint["name"] for constraint in checks} == {
        "ck_organizations_organization_name_not_blank"
    }
    if users_expected:
        assert users_relation == "taskpilot.users"
        assert set(user_columns) == {
            "id",
            "email",
            "normalized_email",
            "password_hash",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert str(user_columns["id"]["type"]) == "UUID"
        assert user_columns["created_at"]["type"].timezone is True
        assert user_columns["updated_at"]["type"].timezone is True
        assert {constraint["name"] for constraint in user_checks} == {
            "ck_users_user_email_not_blank"
        }
        assert {constraint["name"] for constraint in user_unique_constraints} == {
            "uq_users_normalized_email"
        }
        # PostgreSQL backs the UNIQUE(normalized_email) constraint with a
        # same-named index, so assert the explicit index is present instead of
        # requiring exact set equality.
        assert "ix_taskpilot_users_is_active" in {index["name"] for index in user_indexes}
    else:
        assert users_relation is None
    if memberships_expected:
        assert memberships_relation == "taskpilot.memberships"
        assert set(membership_columns) == {
            "id",
            "user_id",
            "organization_id",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        }
        assert str(membership_columns["id"]["type"]) == "UUID"
        assert str(membership_columns["user_id"]["type"]) == "UUID"
        assert str(membership_columns["organization_id"]["type"]) == "UUID"
        assert all(
            membership_columns[column]["nullable"] is False
            for column in ("user_id", "organization_id", "role", "is_active")
        )
        assert membership_columns["created_at"]["type"].timezone is True
        assert membership_columns["updated_at"]["type"].timezone is True
        assert {constraint["name"] for constraint in membership_checks} == {
            "ck_memberships_membership_role_valid"
        }
        assert {constraint["name"] for constraint in membership_unique_constraints} == {
            "uq_memberships_user_organization"
        }
        assert {
            tuple(constraint["column_names"]) for constraint in membership_unique_constraints
        } == {("user_id", "organization_id")}
        membership_index_names = {index["name"] for index in membership_indexes}
        assert "ix_memberships_user_id" in membership_index_names
        assert "ix_memberships_organization_id" in membership_index_names
        foreign_keys = {tuple(fk["constrained_columns"]): fk for fk in membership_foreign_keys}
        assert set(foreign_keys) == {("user_id",), ("organization_id",)}
        assert foreign_keys[("user_id",)]["referred_schema"] == "taskpilot"
        assert foreign_keys[("user_id",)]["referred_table"] == "users"
        assert foreign_keys[("user_id",)]["options"].get("ondelete") == "RESTRICT"
        assert foreign_keys[("organization_id",)]["referred_schema"] == "taskpilot"
        assert foreign_keys[("organization_id",)]["referred_table"] == "organizations"
        assert foreign_keys[("organization_id",)]["options"].get("ondelete") == "RESTRICT"
    else:
        assert memberships_relation is None


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
            # T022A -> T022 must remove only the membership table and leave the
            # T021/T022 identity rows and their LangGraph neighbours intact.
            await _run_alembic(config, "downgrade", T022_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T022_REVISION,
                users_expected=True,
                memberships_expected=False,
            )
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "upgrade", "head")
            await _assert_taskpilot_schema(engine)
            await _langgraph_read(database_url, "scenario-a")
            await _run_alembic(config, "downgrade", T021_REVISION)
            await _assert_taskpilot_schema(
                engine,
                expected_revision=T021_REVISION,
                users_expected=False,
                memberships_expected=False,
            )
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
                        {"qualified_name": "taskpilot.users"},
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        text("SELECT to_regclass(:qualified_name)"),
                        {"qualified_name": "taskpilot.memberships"},
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


@pytest.mark.asyncio
async def test_user_identity_constraints_and_persistence(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    password_hash = "$argon2id$v=19$test-only-opaque-value"
    user = User(
        email="  Straße@Example.COM  ",
        password_hash=password_hash,
    )

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(user)

    assert user.id is not None
    assert user.id.version == 4
    assert user.email == "Straße@Example.COM"
    assert user.normalized_email == "strasse@example.com"
    assert user.is_active is True
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset().total_seconds() == 0
    assert user.updated_at.tzinfo is not None
    assert user.updated_at.utcoffset().total_seconds() == 0

    async with get_business_session(session_factory) as session:
        async with session.begin():
            repository = UserRepository(session)
            loaded = await repository.get(user.id)
            assert loaded is not None
            assert loaded.password_hash == password_hash
            assert password_hash not in repr(loaded)
            assert await repository.get_by_email("  STRASSE@EXAMPLE.COM ") is loaded
            original_created_at = loaded.created_at
            original_updated_at = loaded.updated_at
            await asyncio.sleep(0.01)
            loaded.is_active = False
            # ``updated_at`` is an ORM onupdate value, so it is only refreshed
            # once the change is flushed to PostgreSQL.
            await session.flush()
            assert loaded.is_active is False
            assert loaded.updated_at > original_updated_at

    async with get_business_session(session_factory) as session:
        loaded = await UserRepository(session).get(user.id)
        assert loaded is not None
        assert loaded.is_active is False
        # The onupdate timestamp must survive the commit, not only the session.
        assert loaded.created_at == original_created_at
        assert loaded.updated_at > original_updated_at
        assert loaded.updated_at.utcoffset().total_seconds() == 0

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await UserRepository(session).add(
                    User(email=" strasse@EXAMPLE.com ", password_hash="another-opaque-hash")
                )

    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    insert(User).values(
                        id=uuid4(),
                        email="null-normalized@example.com",
                        normalized_email=None,
                        password_hash="opaque-test-hash",
                    )
                )

    rolled_back_id = uuid4()
    with pytest.raises(RuntimeError, match="force user rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await UserRepository(session).add(
                    User(
                        id=rolled_back_id,
                        email="rollback@example.com",
                        password_hash="opaque-test-hash",
                    )
                )
                raise RuntimeError("force user rollback")

    async with get_business_session(session_factory) as session:
        assert await UserRepository(session).get(rolled_back_id) is None


@pytest.mark.asyncio
async def test_membership_persistence_roles_and_cross_tenant_scope(
    migrated_engine: AsyncEngine,
) -> None:
    session_factory = create_session_factory(migrated_engine)
    alpha = Organization(name="Tenant Alpha")
    beta = Organization(name="Tenant Beta")
    user = User(email="owner@example.com", password_hash="opaque-test-hash")
    other_user = User(email="other@example.com", password_hash="opaque-test-hash")

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(alpha)
            await OrganizationRepository(session).add(beta)
            await UserRepository(session).add(user)
            await UserRepository(session).add(other_user)

    membership = Membership(user_id=user.id, organization_id=alpha.id, role=Role.OWNER)
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await MembershipRepository(session).add(membership)

    assert membership.id is not None
    assert membership.id.version == 4
    assert membership.is_active is True
    assert membership.created_at.tzinfo is not None
    assert membership.created_at.utcoffset().total_seconds() == 0
    assert membership.updated_at.utcoffset().total_seconds() == 0

    async with get_business_session(session_factory) as session:
        async with session.begin():
            repository = MembershipRepository(session)
            loaded = await repository.get(membership.id)
            assert loaded is not None
            assert loaded.user_id == user.id
            assert loaded.organization_id == alpha.id
            assert loaded.role is Role.OWNER

            # The same user may join a second organization.
            await repository.add(
                Membership(user_id=user.id, organization_id=beta.id, role=Role.MEMBER)
            )
            # Scope is answered per organization and never leaks a sibling tenant.
            alpha_membership = await repository.get_for_user_in_organization(user.id, alpha.id)
            beta_membership = await repository.get_for_user_in_organization(user.id, beta.id)
            assert alpha_membership is not None and alpha_membership.role is Role.OWNER
            assert beta_membership is not None and beta_membership.role is Role.MEMBER
            assert alpha_membership.organization_id != beta_membership.organization_id

            original_updated_at = loaded.updated_at
            await asyncio.sleep(0.01)
            loaded.is_active = False
            # ``updated_at`` is an ORM onupdate value, refreshed on flush only.
            await session.flush()
            assert loaded.is_active is False
            assert loaded.updated_at > original_updated_at

    async with get_business_session(session_factory) as session:
        repository = MembershipRepository(session)
        alpha_memberships = await repository.list_for_organization(alpha.id)
        assert [item.organization_id for item in alpha_memberships] == [alpha.id]
        assert alpha_memberships[0].is_active is False
        reloaded = await repository.get(membership.id)
        assert reloaded is not None
        # Inactive memberships are retained, not deleted.
        assert reloaded.is_active is False
        assert reloaded.updated_at.utcoffset().total_seconds() == 0

        # Organization listings are ordered so callers see a deterministic page.
        second_member = User(email="second@example.com", password_hash="opaque-test-hash")
        await UserRepository(session).add(second_member)
        second_membership = await repository.add(
            Membership(user_id=second_member.id, organization_id=beta.id, role=Role.OWNER)
        )
        beta_listing = await repository.list_for_organization(beta.id)
        assert len(beta_listing) == 2
        assert {item.id for item in beta_listing} == {beta_membership.id, second_membership.id}

        # Every frozen role round-trips through VARCHAR + check constraint.
        for role in Role:
            member = User(email=f"role-{role.value}@example.com", password_hash="opaque-test-hash")
            await UserRepository(session).add(member)
            await repository.add(
                Membership(
                    user_id=member.id,
                    organization_id=alpha.id,
                    role=role,
                )
            )
            stored = await repository.get_for_user_in_organization(member.id, alpha.id)
            assert stored is not None
            assert stored.role is role


@pytest.mark.asyncio
async def test_membership_constraints_and_rollback(migrated_engine: AsyncEngine) -> None:
    session_factory = create_session_factory(migrated_engine)
    organization = Organization(name="Constraint Org")
    user = User(email="constraint@example.com", password_hash="opaque-test-hash")

    async with get_business_session(session_factory) as session:
        async with session.begin():
            await OrganizationRepository(session).add(organization)
            await UserRepository(session).add(user)
            await MembershipRepository(session).add(
                Membership(user_id=user.id, organization_id=organization.id, role=Role.ADMIN)
            )

    # A duplicate (user_id, organization_id) pair is rejected by PostgreSQL,
    # even after the existing membership was deactivated.
    async with get_business_session(session_factory) as session:
        async with session.begin():
            existing = await MembershipRepository(session).get_for_user_in_organization(
                user.id, organization.id
            )
            assert existing is not None
            existing.is_active = False
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await MembershipRepository(session).add(
                    Membership(user_id=user.id, organization_id=organization.id, role=Role.MEMBER)
                )

    # The database rejects roles outside the frozen V1 set.
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    insert(Membership).values(
                        id=uuid4(),
                        user_id=user.id,
                        organization_id=organization.id,
                        role="superadmin",
                    )
                )

    # Foreign keys are enforced for both sides of the pair.
    for unknown_user_id, unknown_organization_id in (
        (uuid4(), organization.id),
        (user.id, uuid4()),
    ):
        with pytest.raises(IntegrityError):
            async with get_business_session(session_factory) as session:
                async with session.begin():
                    await MembershipRepository(session).add(
                        Membership(
                            user_id=unknown_user_id,
                            organization_id=unknown_organization_id,
                            role=Role.MEMBER,
                        )
                    )

    doomed_user = User(email="doomed@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(doomed_user)
            await MembershipRepository(session).add(
                Membership(
                    user_id=doomed_user.id, organization_id=organization.id, role=Role.MEMBER
                )
            )

    # ON DELETE RESTRICT prevents silent cascade removal of authorization rows.
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(delete(User).where(User.id == doomed_user.id))
    with pytest.raises(IntegrityError):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await session.execute(
                    delete(Organization).where(Organization.id == organization.id)
                )

    async with get_business_session(session_factory) as session:
        assert (
            await MembershipRepository(session).get_for_user_in_organization(
                doomed_user.id, organization.id
            )
            is not None
        )

    rolled_back_id = uuid4()
    rolled_back_user = User(email="rollback-member@example.com", password_hash="opaque-test-hash")
    async with get_business_session(session_factory) as session:
        async with session.begin():
            await UserRepository(session).add(rolled_back_user)
    with pytest.raises(RuntimeError, match="force membership rollback"):
        async with get_business_session(session_factory) as session:
            async with session.begin():
                await MembershipRepository(session).add(
                    Membership(
                        id=rolled_back_id,
                        user_id=rolled_back_user.id,
                        organization_id=organization.id,
                        role=Role.MEMBER,
                    )
                )
                raise RuntimeError("force membership rollback")

    async with get_business_session(session_factory) as session:
        assert await MembershipRepository(session).get(rolled_back_id) is None
