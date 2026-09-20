"""PostgreSQL-backed HTTP/security evidence for T037 Task mutations."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import (
    Membership,
    Organization,
    Role,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    User,
)
from persistence.passwords import hash_password
from service.auth_dependency import get_session_factory
from service.service import app
from service.session import AuthService
from service.task_lifecycle import TaskLifecycleService

pytestmark = pytest.mark.postgres
PASSWORD = "T037-test-password"


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_t037_{uuid4().hex}"
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


async def _issue_token(factory: async_sessionmaker[AsyncSession], email: str) -> str:
    async with factory() as session:
        async with session.begin():
            result = await AuthService(session).login(email, PASSWORD)
        assert hasattr(result, "token"), result
        return result.token  # type: ignore[union-attr]


async def _add_task(
    factory: async_sessionmaker[AsyncSession],
    organization_id: UUID,
    creator_id: UUID,
    title: str,
) -> UUID:
    task_id = uuid4()
    async with factory() as session:
        async with session.begin():
            session.add(
                Task(
                    id=task_id,
                    organization_id=organization_id,
                    created_by_user_id=creator_id,
                    title=title,
                )
            )
    return task_id


@pytest_asyncio.fixture
async def api_context() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], dict[str, str], dict[str, UUID]]
]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        organization = Organization(id=uuid4(), name="T037 own")
        foreign_organization = Organization(id=uuid4(), name="T037 foreign")
        owner = User(
            id=uuid4(), email="t037-owner@example.com", password_hash=hash_password(PASSWORD)
        )
        admin = User(
            id=uuid4(), email="t037-admin@example.com", password_hash=hash_password(PASSWORD)
        )
        member = User(
            id=uuid4(), email="t037-member@example.com", password_hash=hash_password(PASSWORD)
        )
        foreign_member = User(
            id=uuid4(), email="t037-foreign@example.com", password_hash=hash_password(PASSWORD)
        )
        member_task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=member.id,
            title="Member task",
        )
        admin_task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=admin.id,
            title="Admin task",
        )
        foreign_task = Task(
            id=uuid4(),
            organization_id=foreign_organization.id,
            created_by_user_id=foreign_member.id,
            title="Foreign task",
        )
        memberships = [
            Membership(user_id=owner.id, organization_id=organization.id, role=Role.OWNER),
            Membership(user_id=admin.id, organization_id=organization.id, role=Role.ADMIN),
            Membership(user_id=member.id, organization_id=organization.id, role=Role.MEMBER),
            Membership(
                user_id=foreign_member.id,
                organization_id=foreign_organization.id,
                role=Role.MEMBER,
            ),
        ]
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [organization, foreign_organization, owner, admin, member, foreign_member]
                )
                await session.flush()
                session.add_all([member_task, admin_task, foreign_task, *memberships])
        tokens = {
            "owner": await _issue_token(factory, owner.email),
            "admin": await _issue_token(factory, admin.email),
            "member": await _issue_token(factory, member.email),
            "foreign": await _issue_token(factory, foreign_member.email),
        }
        ids = {
            "organization": organization.id,
            "foreign_organization": foreign_organization.id,
            "admin": admin.id,
            "member": member.id,
            "member_task": member_task.id,
            "admin_task": admin_task.id,
            "foreign_task": foreign_task.id,
        }
        app.dependency_overrides[get_session_factory] = lambda: factory
        try:
            yield factory, tokens, ids
        finally:
            app.dependency_overrides.pop(get_session_factory, None)
            await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_task_update_auth_tenant_and_ownership_policy(api_context) -> None:
    factory, tokens, ids = api_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        unauthenticated = await client.patch(
            f"/api/v1/tasks/{ids['member_task']}", json={"title": "blocked"}
        )
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"

        empty = await client.patch(
            f"/api/v1/tasks/{ids['member_task']}",
            headers=_headers(tokens["member"]),
            json={},
        )
        assert empty.status_code == 422
        blank = await client.patch(
            f"/api/v1/tasks/{ids['member_task']}",
            headers=_headers(tokens["member"]),
            json={"title": "   "},
        )
        assert blank.status_code == 422

        own = await client.patch(
            f"/api/v1/tasks/{ids['member_task']}",
            headers=_headers(tokens["member"]),
            json={"title": "Member updated", "description": "new details"},
        )
        assert own.status_code == 200
        assert own.json()["title"] == "Member updated"

        forbidden = await client.patch(
            f"/api/v1/tasks/{ids['admin_task']}",
            headers=_headers(tokens["member"]),
            json={"title": "not allowed"},
        )
        assert forbidden.status_code == 403
        assert forbidden.json() == {"detail": "Forbidden"}

        admin = await client.patch(
            f"/api/v1/tasks/{ids['member_task']}",
            headers=_headers(tokens["admin"]),
            json={
                "title": "Admin updated",
                "organization_id": str(uuid4()),
                "created_by_user_id": str(uuid4()),
                "status": "succeeded",
            },
        )
        assert admin.status_code == 200
        assert admin.json()["organization_id"] == str(ids["organization"])
        assert admin.json()["created_by_user_id"] == str(ids["member"])
        assert admin.json()["status"] == "draft"

        foreign = await client.patch(
            f"/api/v1/tasks/{ids['foreign_task']}",
            headers=_headers(tokens["member"]),
            json={"title": "hidden"},
        )
        missing = await client.patch(
            f"/api/v1/tasks/{uuid4()}",
            headers=_headers(tokens["member"]),
            json={"title": "hidden"},
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "Not Found"}

    async with factory() as session:
        task = await session.get(Task, ids["member_task"])
        assert task is not None
        assert task.title == "Admin updated"
        assert task.organization_id == ids["organization"]
        assert task.created_by_user_id == ids["member"]
        assert task.status is TaskStatus.DRAFT


@pytest.mark.asyncio
async def test_task_cancel_uses_t035_and_preserves_conflict_and_tenant_semantics(
    api_context,
) -> None:
    factory, tokens, ids = api_context
    queued_id = await _add_task(factory, ids["organization"], ids["admin"], "Queued")
    running_id = await _add_task(factory, ids["organization"], ids["admin"], "Running")
    succeeded_id = await _add_task(factory, ids["organization"], ids["admin"], "Succeeded")
    failed_id = await _add_task(factory, ids["organization"], ids["admin"], "Failed")
    async with factory() as session:
        lifecycle = TaskLifecycleService(session)
        await lifecycle.start_task(queued_id, ids["organization"])
        await lifecycle.start_task(running_id, ids["organization"])
    async with factory() as session:
        lifecycle = TaskLifecycleService(session)
        await lifecycle.begin_run(running_id, ids["organization"])
        await lifecycle.start_task(succeeded_id, ids["organization"])
        await lifecycle.begin_run(succeeded_id, ids["organization"])
        await lifecycle.succeed_run(succeeded_id, ids["organization"])
        await lifecycle.start_task(failed_id, ids["organization"])
        await lifecycle.begin_run(failed_id, ids["organization"])
        await lifecycle.fail_run(failed_id, ids["organization"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        unauthenticated = await client.post(f"/api/v1/tasks/{ids['member_task']}/cancel")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"

        own = await client.post(
            f"/api/v1/tasks/{ids['member_task']}/cancel", headers=_headers(tokens["member"])
        )
        repeated = await client.post(
            f"/api/v1/tasks/{ids['member_task']}/cancel", headers=_headers(tokens["member"])
        )
        assert own.status_code == repeated.status_code == 200
        assert own.json()["status"] == repeated.json()["status"] == "cancelled"

        forbidden = await client.post(
            f"/api/v1/tasks/{ids['admin_task']}/cancel", headers=_headers(tokens["member"])
        )
        assert forbidden.status_code == 403

        foreign = await client.post(
            f"/api/v1/tasks/{ids['foreign_task']}/cancel", headers=_headers(tokens["member"])
        )
        missing = await client.post(
            f"/api/v1/tasks/{uuid4()}/cancel", headers=_headers(tokens["member"])
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "Not Found"}

        queued = await client.post(
            f"/api/v1/tasks/{queued_id}/cancel", headers=_headers(tokens["admin"])
        )
        running = await client.post(
            f"/api/v1/tasks/{running_id}/cancel", headers=_headers(tokens["admin"])
        )
        assert queued.status_code == running.status_code == 200
        assert queued.json()["status"] == running.json()["status"] == "cancelled"

        for terminal_id in (succeeded_id, failed_id):
            conflict = await client.post(
                f"/api/v1/tasks/{terminal_id}/cancel", headers=_headers(tokens["admin"])
            )
            assert conflict.status_code == 409
            assert conflict.json() == {"detail": "Task lifecycle conflict"}

    async with factory() as session:
        queued = await session.get(Task, queued_id)
        running = await session.get(Task, running_id)
        succeeded = await session.get(Task, succeeded_id)
        failed = await session.get(Task, failed_id)
        assert queued is not None and running is not None
        assert succeeded is not None and failed is not None
        assert queued.status is running.status is TaskStatus.CANCELLED
        assert succeeded.status is TaskStatus.SUCCEEDED
        assert failed.status is TaskStatus.FAILED
        runs = list(
            (
                await session.scalars(
                    select(TaskRun).where(TaskRun.task_id.in_([queued_id, running_id]))
                )
            ).all()
        )
        assert {run.status for run in runs} == {TaskRunStatus.CANCELLED}
