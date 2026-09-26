"""PostgreSQL-backed HTTP/security evidence for T036."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import AuthSession, Membership, Organization, Role, Task, TaskRun, User
from persistence.passwords import hash_password
from service.auth_dependency import get_session_factory
from service.service import app
from service.session import AuthService

pytestmark = pytest.mark.postgres
PASSWORD = "T036-test-password"


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_t036_{uuid4().hex}"
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
async def api_context() -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], dict[str, str]]]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        organization = Organization(id=uuid4(), name="T036 own")
        foreign_organization = Organization(id=uuid4(), name="T036 foreign")
        chooser_organization_a = Organization(id=uuid4(), name="T036 chooser A")
        chooser_organization_b = Organization(id=uuid4(), name="T036 chooser B")
        owner = User(
            id=uuid4(), email="t036-owner@example.com", password_hash=hash_password(PASSWORD)
        )
        foreign_user = User(
            id=uuid4(), email="t036-foreign@example.com", password_hash=hash_password(PASSWORD)
        )
        chooser = User(
            id=uuid4(), email="t036-chooser@example.com", password_hash=hash_password(PASSWORD)
        )
        owner_membership = Membership(
            user_id=owner.id,
            organization_id=organization.id,
            role=Role.MEMBER,
        )
        foreign_membership = Membership(
            user_id=foreign_user.id,
            organization_id=foreign_organization.id,
            role=Role.MEMBER,
        )
        chooser_memberships = [
            Membership(
                user_id=chooser.id,
                organization_id=chooser_organization_a.id,
                role=Role.OWNER,
            ),
            Membership(
                user_id=chooser.id,
                organization_id=chooser_organization_b.id,
                role=Role.ADMIN,
            ),
        ]
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        organization,
                        foreign_organization,
                        chooser_organization_a,
                        chooser_organization_b,
                        owner,
                        foreign_user,
                        chooser,
                    ]
                )
                await session.flush()
                session.add_all([owner_membership, foreign_membership, *chooser_memberships])
        async with factory() as session:
            async with session.begin():
                owner_login = await AuthService(session).login("t036-owner@example.com", PASSWORD)
                foreign_login = await AuthService(session).login(
                    "t036-foreign@example.com", PASSWORD
                )
        assert hasattr(owner_login, "token") and hasattr(foreign_login, "token")
        app.dependency_overrides[get_session_factory] = lambda: factory
        try:
            yield (
                factory,
                {
                    "owner": owner_login.token,  # type: ignore[union-attr]
                    "foreign": foreign_login.token,  # type: ignore[union-attr]
                    "chooser": "unused",
                },
            )
        finally:
            app.dependency_overrides.pop(get_session_factory, None)
            await engine.dispose()


@pytest.mark.asyncio
async def test_task_api_auth_create_list_get_and_tenant_isolation(api_context) -> None:
    factory, tokens = api_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        unauthenticated = await client.get("/api/v1/tasks")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"

        headers = {"Authorization": f"Bearer {tokens['owner']}"}
        invalid = await client.post("/api/v1/tasks", headers=headers, json={"title": "   "})
        assert invalid.status_code == 422
        created = await client.post(
            "/api/v1/tasks",
            headers=headers,
            json={
                "title": "Own task",
                "description": "created through T036",
                "organization_id": str(uuid4()),
                "created_by_user_id": str(uuid4()),
            },
        )
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "draft"
        assert body["description"] == "created through T036"
        created_id = body["id"]

        listed = await client.get("/api/v1/tasks", headers=headers)
        assert listed.status_code == 200
        assert [task["id"] for task in listed.json()] == [created_id]

        own = await client.get(f"/api/v1/tasks/{created_id}", headers=headers)
        assert own.status_code == 200
        assert own.json()["id"] == created_id

        foreign_headers = {"Authorization": f"Bearer {tokens['foreign']}"}
        foreign_list = await client.get("/api/v1/tasks", headers=foreign_headers)
        assert foreign_list.status_code == 200
        assert foreign_list.json() == []
        foreign_get = await client.get(f"/api/v1/tasks/{created_id}", headers=foreign_headers)
        missing_get = await client.get(f"/api/v1/tasks/{uuid4()}", headers=headers)
        assert foreign_get.status_code == missing_get.status_code == 404
        assert foreign_get.json() == missing_get.json() == {"detail": "Not Found"}

    async with factory() as session:
        task = await session.scalar(select(Task).where(Task.id == created_id))
        assert task is not None
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        )
        assert task.status.value == "draft"
        assert runs == []


@pytest.mark.asyncio
async def test_t118_login_session_and_logout_are_real_http_paths(api_context) -> None:
    factory, tokens = api_context
    del factory, tokens
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        invalid = await client.post(
            "/api/v1/auth/login",
            json={"email": "t036-owner@example.com", "password": "wrong-password"},
        )
        assert invalid.status_code == 401
        assert invalid.json() == {"detail": "Not authenticated"}
        assert invalid.headers["www-authenticate"] == "Bearer"
        assert invalid.headers["cache-control"] == "no-store"

        login = await client.post(
            "/api/v1/auth/login",
            json={"email": "t036-owner@example.com", "password": PASSWORD},
        )
        assert login.status_code == 200
        assert login.headers["cache-control"] == "no-store"
        body = login.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
        token = body["access_token"]

        current = await client.get(
            "/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert current.status_code == 200
        assert current.json()["role"] == "member"
        assert current.headers["cache-control"] == "no-store"

        logout = await client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {token}"}
        )
        assert logout.status_code == 204
        assert logout.headers["cache-control"] == "no-store"

        revoked = await client.get(
            "/api/v1/auth/session", headers={"Authorization": f"Bearer {token}"}
        )
        assert revoked.status_code == 401
        assert revoked.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_t118_selection_malformed_duplicate_logout_and_freshness(
    api_context, caplog: pytest.LogCaptureFixture
) -> None:
    factory, _ = api_context
    transport = httpx.ASGITransport(app=app)
    caplog.set_level(logging.WARNING)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        selection = await client.post(
            "/api/v1/auth/login",
            json={"email": "t036-chooser@example.com", "password": PASSWORD},
        )
        assert selection.status_code == 409
        selection_body = selection.json()
        assert selection_body["code"] == "ORGANIZATION_SELECTION_REQUIRED"
        organization_ids = selection_body["organization_ids"]
        assert organization_ids == sorted(set(organization_ids))
        assert "access_token" not in selection_body
        assert "token" not in selection.text.casefold()

        malformed = await client.post(
            "/api/v1/auth/login",
            json={"email": 123, "password": PASSWORD, "unexpected": "secret"},
        )
        assert malformed.status_code == 401
        assert malformed.json() == {"detail": "Not authenticated"}

        selected = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "t036-chooser@example.com",
                "password": PASSWORD,
                "organization_id": organization_ids[0],
            },
        )
        assert selected.status_code == 200
        token = selected.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.get("/api/v1/auth/session", headers=headers)
        assert first.status_code == 200

        async with factory() as session:
            membership = await session.get(Membership, first.json()["membership_id"])
            assert membership is not None
            membership.role = Role.MEMBER
            await session.commit()

        fresh = await client.get("/api/v1/auth/session", headers=headers)
        assert fresh.status_code == 200
        assert fresh.json()["role"] == "member"

        logout = await client.post("/api/v1/auth/logout", headers=headers)
        assert logout.status_code == 204
        duplicate = await client.post("/api/v1/auth/logout", headers=headers)
        assert duplicate.status_code == 401
        assert duplicate.json() == {"detail": "Not authenticated"}

    assert PASSWORD not in caplog.text
    assert token not in caplog.text


@pytest.mark.asyncio
async def test_t118_login_commit_failure_rolls_back_real_postgres_session(api_context) -> None:
    factory, _ = api_context
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == "t036-owner@example.com"))
        assert user is not None
        user_id = user.id
        before = len(
            list(
                (
                    await session.scalars(select(AuthSession).where(AuthSession.user_id == user_id))
                ).all()
            )
        )
        original_commit = session.commit
        session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="commit failed"):
            await AuthService(session).login_and_commit("t036-owner@example.com", PASSWORD)
        session.commit = original_commit  # type: ignore[method-assign]
        after = len(
            list(
                (
                    await session.scalars(select(AuthSession).where(AuthSession.user_id == user_id))
                ).all()
            )
        )
        assert after == before
