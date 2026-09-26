"""PostgreSQL-backed HTTP/security evidence for T038 TaskRun APIs."""

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
PASSWORD = "T038-test-password"


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
    name = f"taskpilot_t038_{uuid4().hex}"
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
        organization = Organization(id=uuid4(), name="T038 own")
        foreign_organization = Organization(id=uuid4(), name="T038 foreign")
        owner = User(
            id=uuid4(), email="t038-owner@example.com", password_hash=hash_password(PASSWORD)
        )
        admin = User(
            id=uuid4(), email="t038-admin@example.com", password_hash=hash_password(PASSWORD)
        )
        member = User(
            id=uuid4(), email="t038-member@example.com", password_hash=hash_password(PASSWORD)
        )
        foreign_member = User(
            id=uuid4(), email="t038-foreign@example.com", password_hash=hash_password(PASSWORD)
        )
        draft_task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=member.id,
            title="Draft task",
        )
        failed_task = Task(
            id=uuid4(),
            organization_id=organization.id,
            created_by_user_id=member.id,
            title="Failed task",
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
                session.add_all([draft_task, failed_task, foreign_task, *memberships])

        async with factory() as session:
            first_run = await TaskLifecycleService(session).start_task(
                failed_task.id, organization.id
            )
        async with factory() as session:
            await TaskLifecycleService(session).begin_run(failed_task.id, organization.id)
            await TaskLifecycleService(session).fail_run(failed_task.id, organization.id)

        async with factory() as session:
            foreign_run = TaskRun(
                task_id=foreign_task.id,
                run_number=1,
                status=TaskRunStatus.FAILED,
            )
            session.add(foreign_run)
            await session.commit()
            foreign_run_id = foreign_run.id

        tokens = {
            "owner": await _issue_token(factory, owner.email),
            "admin": await _issue_token(factory, admin.email),
            "member": await _issue_token(factory, member.email),
            "foreign": await _issue_token(factory, foreign_member.email),
        }
        ids = {
            "organization": organization.id,
            "owner": owner.id,
            "admin": admin.id,
            "member": member.id,
            "draft_task": draft_task.id,
            "failed_task": failed_task.id,
            "failed_run": first_run.id,
            "foreign_task": foreign_task.id,
            "foreign_run": foreign_run_id,
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
async def test_task_run_start_and_inspect_are_tenant_scoped(api_context) -> None:
    factory, tokens, ids = api_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        unauthenticated = await client.post(f"/api/v1/tasks/{ids['draft_task']}/runs")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"

        started = await client.post(
            f"/api/v1/tasks/{ids['draft_task']}/runs",
            headers=_headers(tokens["member"]),
            json={"status": "succeeded", "organization_id": str(uuid4())},
        )
        assert started.status_code == 201
        body = started.json()
        assert body["task_id"] == str(ids["draft_task"])
        assert body["run_number"] == 1
        assert body["status"] == "pending"
        assert set(body) == {
            "id",
            "task_id",
            "run_number",
            "status",
            "created_at",
            "updated_at",
        }
        run_id = body["id"]

        inspected = await client.get(
            f"/api/v1/tasks/{ids['draft_task']}/runs/{run_id}",
            headers=_headers(tokens["owner"]),
        )
        assert inspected.status_code == 200
        assert inspected.json() == body

        wrong_task = await client.get(
            f"/api/v1/tasks/{ids['failed_task']}/runs/{run_id}",
            headers=_headers(tokens["member"]),
        )
        foreign = await client.get(
            f"/api/v1/tasks/{ids['foreign_task']}/runs/{ids['foreign_run']}",
            headers=_headers(tokens["member"]),
        )
        missing = await client.get(
            f"/api/v1/tasks/{ids['draft_task']}/runs/{uuid4()}",
            headers=_headers(tokens["member"]),
        )
        assert wrong_task.status_code == foreign.status_code == missing.status_code == 404
        assert wrong_task.json() == foreign.json() == missing.json() == {"detail": "Not Found"}

        foreign_start = await client.post(
            f"/api/v1/tasks/{ids['foreign_task']}/runs",
            headers=_headers(tokens["member"]),
        )
        missing_start = await client.post(
            f"/api/v1/tasks/{uuid4()}/runs",
            headers=_headers(tokens["member"]),
        )
        assert foreign_start.status_code == missing_start.status_code == 404
        assert foreign_start.json() == missing_start.json() == {"detail": "Not Found"}

        repeated_start = await client.post(
            f"/api/v1/tasks/{ids['draft_task']}/runs",
            headers=_headers(tokens["member"]),
        )
        assert repeated_start.status_code == 409
        assert repeated_start.json() == {"detail": "Task lifecycle conflict"}

    async with factory() as session:
        task = await session.get(Task, ids["draft_task"])
        runs = list(
            (await session.scalars(select(TaskRun).where(TaskRun.task_id == task.id))).all()
        )
        assert task is not None
        assert task.status is TaskStatus.QUEUED
        assert len(runs) == 1
        assert runs[0].status is TaskRunStatus.PENDING


@pytest.mark.asyncio
async def test_task_run_retry_preserves_history_and_rejects_illegal_states(api_context) -> None:
    factory, tokens, ids = api_context
    queued_id = await _add_task(factory, ids["organization"], ids["admin"], "Queued")
    running_id = await _add_task(factory, ids["organization"], ids["admin"], "Running")
    succeeded_id = await _add_task(factory, ids["organization"], ids["admin"], "Succeeded")
    cancelled_id = await _add_task(factory, ids["organization"], ids["admin"], "Cancelled")
    async with factory() as session:
        lifecycle = TaskLifecycleService(session)
        await lifecycle.start_task(queued_id, ids["organization"])
        await lifecycle.start_task(running_id, ids["organization"])
        await lifecycle.begin_run(running_id, ids["organization"])
        await lifecycle.start_task(succeeded_id, ids["organization"])
        await lifecycle.begin_run(succeeded_id, ids["organization"])
        await lifecycle.succeed_run(succeeded_id, ids["organization"])
        await lifecycle.start_task(cancelled_id, ids["organization"])
        await lifecycle.cancel_task(cancelled_id, ids["organization"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        retry = await client.post(
            f"/api/v1/tasks/{ids['failed_task']}/runs",
            headers=_headers(tokens["admin"]),
        )
        assert retry.status_code == 201
        retry_body = retry.json()
        assert retry_body["run_number"] == 2
        assert retry_body["status"] == "pending"

        for task_id in (queued_id, running_id, succeeded_id, cancelled_id):
            conflict = await client.post(
                f"/api/v1/tasks/{task_id}/runs",
                headers=_headers(tokens["owner"]),
            )
            assert conflict.status_code == 409
            assert conflict.json() == {"detail": "Task lifecycle conflict"}

    async with factory() as session:
        task = await session.get(Task, ids["failed_task"])
        runs = list(
            (
                await session.scalars(
                    select(TaskRun)
                    .where(TaskRun.task_id == ids["failed_task"])
                    .order_by(TaskRun.run_number)
                )
            ).all()
        )
        assert task is not None
        assert task.status is TaskStatus.QUEUED
        assert [run.run_number for run in runs] == [1, 2]
        assert [run.status for run in runs] == [TaskRunStatus.FAILED, TaskRunStatus.PENDING]


@pytest.mark.asyncio
async def test_t119_lists_only_visible_runs_in_deterministic_order(api_context) -> None:
    factory, tokens, ids = api_context
    async with factory() as session:
        async with session.begin():
            session.add_all(
                [
                    TaskRun(
                        task_id=ids["failed_task"],
                        run_number=3,
                        status=TaskRunStatus.CANCELLED,
                    ),
                    TaskRun(
                        task_id=ids["failed_task"],
                        run_number=2,
                        status=TaskRunStatus.FAILED,
                    ),
                ]
            )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        listed = await client.get(
            f"/api/v1/tasks/{ids['failed_task']}/runs", headers=_headers(tokens["member"])
        )
        assert listed.status_code == 200
        assert [item["run_number"] for item in listed.json()] == [1, 2, 3]

        empty = await client.get(
            f"/api/v1/tasks/{ids['draft_task']}/runs", headers=_headers(tokens["member"])
        )
        assert empty.status_code == 200
        assert empty.json() == []

        foreign = await client.get(
            f"/api/v1/tasks/{ids['foreign_task']}/runs", headers=_headers(tokens["member"])
        )
        missing = await client.get(
            f"/api/v1/tasks/{uuid4()}/runs", headers=_headers(tokens["member"])
        )
        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "Not Found"}

        unauthenticated = await client.get(f"/api/v1/tasks/{ids['failed_task']}/runs")
        assert unauthenticated.status_code == 401
