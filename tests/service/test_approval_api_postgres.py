"""PostgreSQL-backed security, transaction, and race evidence for T082."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from persistence.engine import create_async_engine
from persistence.models import (
    Approval,
    ApprovalStatus,
    Membership,
    Organization,
    Role,
    Task,
    User,
)
from persistence.passwords import hash_password
from service.approval_service import (
    ApprovalConflictError,
    ApprovalProposal,
    ApprovalService,
)
from service.auth_dependency import get_session_factory
from service.authorization import AuthorizationError
from service.service import app
from service.session import AuthService, CurrentPrincipal
from service.task_lifecycle import TaskLifecycleService

pytestmark = pytest.mark.postgres
PASSWORD = "T082-test-password"
PROPOSAL = ApprovalProposal(
    action_name="phase6.mock_external_write",
    action_version="1",
    proposed_action={"target": "review@example.test", "api_key": "test-secret-value"},
)


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
    name = f"taskpilot_t082_{uuid4().hex}"
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


async def _principal(factory: async_sessionmaker[AsyncSession], token: str) -> CurrentPrincipal:
    async with factory() as session:
        principal = await AuthService(session).authenticate(token)
        assert principal is not None
        return principal


async def _create_approval(
    factory: async_sessionmaker[AsyncSession],
    principal: CurrentPrincipal,
    task_id: UUID,
    run_id: UUID,
    *,
    step_position: int,
    proposal: ApprovalProposal = PROPOSAL,
) -> Approval:
    async with factory() as session:
        return await ApprovalService(session).create_or_reuse(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            replan_count=0,
            step_position=step_position,
            proposal=proposal,
        )


@pytest_asyncio.fixture
async def approval_context() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], dict[str, str], dict[str, UUID]]
]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        own_organization = Organization(id=uuid4(), name="T082 own")
        foreign_organization = Organization(id=uuid4(), name="T082 foreign")
        owner = User(
            id=uuid4(), email="t082-owner@example.test", password_hash=hash_password(PASSWORD)
        )
        admin = User(
            id=uuid4(), email="t082-admin@example.test", password_hash=hash_password(PASSWORD)
        )
        member = User(
            id=uuid4(), email="t082-member@example.test", password_hash=hash_password(PASSWORD)
        )
        foreign_owner = User(
            id=uuid4(),
            email="t082-foreign@example.test",
            password_hash=hash_password(PASSWORD),
        )
        own_task = Task(
            id=uuid4(),
            organization_id=own_organization.id,
            created_by_user_id=member.id,
            title="Own running task",
        )
        foreign_task = Task(
            id=uuid4(),
            organization_id=foreign_organization.id,
            created_by_user_id=foreign_owner.id,
            title="Foreign running task",
        )
        memberships = [
            Membership(user_id=owner.id, organization_id=own_organization.id, role=Role.OWNER),
            Membership(user_id=admin.id, organization_id=own_organization.id, role=Role.ADMIN),
            Membership(user_id=member.id, organization_id=own_organization.id, role=Role.MEMBER),
            Membership(
                user_id=foreign_owner.id,
                organization_id=foreign_organization.id,
                role=Role.OWNER,
            ),
        ]
        async with factory() as session:
            async with session.begin():
                session.add_all(
                    [
                        own_organization,
                        foreign_organization,
                        owner,
                        admin,
                        member,
                        foreign_owner,
                    ]
                )
                await session.flush()
                session.add_all([own_task, foreign_task, *memberships])

        async with factory() as session:
            own_run = await TaskLifecycleService(session).start_task(
                own_task.id, own_organization.id
            )
        async with factory() as session:
            await TaskLifecycleService(session).begin_run(own_task.id, own_organization.id)
        async with factory() as session:
            foreign_run = await TaskLifecycleService(session).start_task(
                foreign_task.id, foreign_organization.id
            )
        async with factory() as session:
            await TaskLifecycleService(session).begin_run(foreign_task.id, foreign_organization.id)

        tokens = {
            "owner": await _issue_token(factory, owner.email),
            "admin": await _issue_token(factory, admin.email),
            "member": await _issue_token(factory, member.email),
            "foreign": await _issue_token(factory, foreign_owner.email),
        }
        principals = {key: await _principal(factory, token) for key, token in tokens.items()}
        own_approval = await _create_approval(
            factory, principals["owner"], own_task.id, own_run.id, step_position=0
        )
        foreign_approval = await _create_approval(
            factory,
            principals["foreign"],
            foreign_task.id,
            foreign_run.id,
            step_position=0,
        )
        ids = {
            "organization": own_organization.id,
            "owner": owner.id,
            "admin": admin.id,
            "member": member.id,
            "owner_membership": memberships[0].id,
            "admin_membership": memberships[1].id,
            "member_membership": memberships[2].id,
            "task": own_task.id,
            "run": own_run.id,
            "approval": own_approval.id,
            "foreign_task": foreign_task.id,
            "foreign_run": foreign_run.id,
            "foreign_approval": foreign_approval.id,
        }
        app.dependency_overrides[get_session_factory] = lambda: factory
        try:
            yield factory, tokens, ids
        finally:
            app.dependency_overrides.pop(get_session_factory, None)
            await engine.dispose()


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _approval_path(task_id: UUID, run_id: UUID, approval_id: UUID | None = None) -> str:
    path = f"/api/v1/tasks/{task_id}/runs/{run_id}/approvals"
    return path if approval_id is None else f"{path}/{approval_id}"


@pytest.mark.asyncio
async def test_approval_reads_are_tenant_scoped_and_sanitized(approval_context) -> None:
    _factory, tokens, ids = approval_context
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        path = _approval_path(ids["task"], ids["run"], ids["approval"])
        unauthenticated = await client.get(path)
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["www-authenticate"] == "Bearer"
        invalid = await client.get(path, headers=_headers("not-a-valid-session"))
        assert invalid.status_code == 401

        listed = await client.get(
            _approval_path(ids["task"], ids["run"]), headers=_headers(tokens["member"])
        )
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["id"] == str(ids["approval"])

        visible = await client.get(path, headers=_headers(tokens["member"]))
        assert visible.status_code == 200
        body = visible.json()
        assert body["proposed_action"]["api_key"] == "[REDACTED]"
        assert "test-secret-value" not in visible.text
        assert body["status"] == "pending"
        assert body["risk_level"] == "L2"
        assert "organization_id" not in body
        assert "user_id" not in body
        assert "role" not in body
        assert "outcome" not in body

        foreign = await client.get(
            _approval_path(ids["foreign_task"], ids["foreign_run"], ids["foreign_approval"]),
            headers=_headers(tokens["owner"]),
        )
        missing = await client.get(
            _approval_path(ids["task"], ids["run"], uuid4()),
            headers=_headers(tokens["owner"]),
        )
        foreign_list = await client.get(
            _approval_path(ids["foreign_task"], ids["foreign_run"]),
            headers=_headers(tokens["owner"]),
        )
        foreign_decision = await client.post(
            f"{_approval_path(ids['foreign_task'], ids['foreign_run'], ids['foreign_approval'])}/approve",
            headers=_headers(tokens["member"]),
        )
        assert foreign.status_code == missing.status_code == foreign_list.status_code == 404
        assert foreign.json() == missing.json() == foreign_list.json() == {"detail": "Not Found"}
        assert foreign_decision.status_code == 404


@pytest.mark.asyncio
async def test_decisions_enforce_roles_ignore_forged_fields_and_are_terminal(
    approval_context,
) -> None:
    factory, tokens, ids = approval_context
    second = await _create_approval(
        factory,
        await _principal(factory, tokens["owner"]),
        ids["task"],
        ids["run"],
        step_position=1,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        path = _approval_path(ids["task"], ids["run"], ids["approval"])
        member_denied = await client.post(
            f"{path}/approve", headers=_headers(tokens["member"]), json={"reason": "ok"}
        )
        assert member_denied.status_code == 403
        assert member_denied.json() == {"detail": "Forbidden"}

        forged_member_role = replace(await _principal(factory, tokens["member"]), role=Role.OWNER)
        async with factory() as session:
            with pytest.raises(AuthorizationError) as error:
                await ApprovalService(session).approve(
                    forged_member_role,
                    task_id=ids["task"],
                    task_run_id=ids["run"],
                    approval_id=ids["approval"],
                )
            assert error.value.status_code == 403

        forged = await client.post(
            f"{path}/approve",
            headers=_headers(tokens["owner"]),
            json={
                "reason": "ok",
                "actor_id": str(uuid4()),
                "organization_id": str(uuid4()),
                "role": "owner",
                "proposed_action": {"target": "changed"},
            },
        )
        assert forged.status_code == 422

        approved = await client.post(
            f"{path}/approve",
            headers=_headers(tokens["owner"]),
            json={"reason": "password=private-value"},
        )
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["decider_membership_id"] == str(ids["owner_membership"])
        assert "private-value" not in approved.text
        assert approved.json()["decision_reason"] == "password=[REDACTED]"

        duplicate = await client.post(
            f"{path}/approve", headers=_headers(tokens["owner"]), json={"reason": "same"}
        )
        competing = await client.post(
            f"{path}/reject", headers=_headers(tokens["admin"]), json={"reason": "other"}
        )
        assert duplicate.status_code == competing.status_code == 409
        assert duplicate.json() == competing.json() == {"detail": "Approval conflict"}

        rejected = await client.post(
            f"{_approval_path(ids['task'], ids['run'], second.id)}/reject",
            headers=_headers(tokens["admin"]),
            json={"reason": "not approved"},
        )
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decider_membership_id"] == str(ids["admin_membership"])

    async with factory() as session:
        approval = await session.get(Approval, ids["approval"])
        rejected_approval = await session.get(Approval, second.id)
        assert approval is not None and approval.status is ApprovalStatus.APPROVED
        assert approval.proposed_action["target"] == "review@example.test"
        assert approval.proposed_action["api_key"] == "[REDACTED]"
        assert rejected_approval is not None
        assert rejected_approval.status is ApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_create_reuses_only_equal_proposals_and_preserves_decision(approval_context) -> None:
    factory, tokens, ids = approval_context
    principal = await _principal(factory, tokens["owner"])
    original = await _create_approval(factory, principal, ids["task"], ids["run"], step_position=0)
    assert original.id == ids["approval"]

    async with factory() as session:
        approved = await ApprovalService(session).approve(
            principal,
            task_id=ids["task"],
            task_run_id=ids["run"],
            approval_id=original.id,
        )
        assert approved.status is ApprovalStatus.APPROVED

    reused = await _create_approval(factory, principal, ids["task"], ids["run"], step_position=0)
    assert reused.id == original.id
    assert reused.status is ApprovalStatus.APPROVED

    mismatched = ApprovalProposal(
        action_name=PROPOSAL.action_name,
        action_version=PROPOSAL.action_version,
        proposed_action={"target": "different@example.test", "api_key": "test-secret-value"},
    )
    async with factory() as session:
        with pytest.raises(ApprovalConflictError):
            await ApprovalService(session).create_or_reuse(
                principal,
                task_id=ids["task"],
                task_run_id=ids["run"],
                replan_count=0,
                step_position=0,
                proposal=mismatched,
            )

    member_proposal = await _create_approval(
        factory,
        await _principal(factory, tokens["member"]),
        ids["task"],
        ids["run"],
        step_position=2,
    )
    assert member_proposal.requester_membership_id == ids["member_membership"]


@pytest.mark.asyncio
async def test_approval_service_logs_only_safe_identifiers(approval_context, caplog) -> None:
    factory, tokens, ids = approval_context
    principal = await _principal(factory, tokens["owner"])
    with caplog.at_level("INFO", logger="service.approval_service"):
        await _create_approval(factory, principal, ids["task"], ids["run"], step_position=12)
    assert "test-secret-value" not in caplog.text
    assert "review@example.test" not in caplog.text
    assert "approval.created" in caplog.text


@pytest.mark.asyncio
async def test_concurrent_create_and_competing_decisions_serialize(approval_context) -> None:
    factory, tokens, ids = approval_context
    owner = await _principal(factory, tokens["owner"])
    admin = await _principal(factory, tokens["admin"])

    async def create() -> Approval:
        async with factory() as session:
            return await ApprovalService(session).create_or_reuse(
                owner,
                task_id=ids["task"],
                task_run_id=ids["run"],
                replan_count=0,
                step_position=8,
                proposal=PROPOSAL,
            )

    first, second = await asyncio.gather(create(), create())
    assert first.id == second.id
    async with factory() as session:
        count = await session.scalar(
            select(func.count(Approval.id)).where(
                Approval.task_run_id == ids["run"],
                Approval.replan_count == 0,
                Approval.step_position == 8,
            )
        )
        assert count == 1

    async def decide(principal: CurrentPrincipal, approve: bool) -> Approval:
        async with factory() as session:
            service = ApprovalService(session)
            method = service.approve if approve else service.reject
            return await method(
                principal,
                task_id=ids["task"],
                task_run_id=ids["run"],
                approval_id=first.id,
            )

    results = await asyncio.gather(
        decide(owner, True), decide(admin, False), return_exceptions=True
    )
    winners = [result for result in results if isinstance(result, Approval)]
    conflicts = [result for result in results if isinstance(result, ApprovalConflictError)]
    assert len(winners) == len(conflicts) == 1
    async with factory() as session:
        final = await session.get(Approval, first.id)
        assert final is not None
        assert final.status is winners[0].status


@pytest.mark.asyncio
async def test_transaction_failure_rolls_back_and_stale_run_cannot_decide(
    approval_context, monkeypatch
) -> None:
    factory, tokens, ids = approval_context
    principal = await _principal(factory, tokens["owner"])
    async with factory() as session:

        async def fail_commit() -> None:
            raise RuntimeError("simulated commit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="simulated commit failure"):
            await ApprovalService(session).create_or_reuse(
                principal,
                task_id=ids["task"],
                task_run_id=ids["run"],
                replan_count=0,
                step_position=31,
                proposal=PROPOSAL,
            )

    async with factory() as session:

        async def fail_decision_commit() -> None:
            raise RuntimeError("simulated decision commit failure")

        monkeypatch.setattr(session, "commit", fail_decision_commit)
        with pytest.raises(RuntimeError, match="simulated decision commit failure"):
            await ApprovalService(session).approve(
                principal,
                task_id=ids["task"],
                task_run_id=ids["run"],
                approval_id=ids["approval"],
            )

    async with factory() as session:
        count = await session.scalar(
            select(func.count(Approval.id)).where(
                Approval.task_run_id == ids["run"], Approval.step_position == 31
            )
        )
        assert count == 0
        approval = await session.get(Approval, ids["approval"])
        assert approval is not None and approval.status is ApprovalStatus.PENDING
        await TaskLifecycleService(session).cancel_task(ids["task"], ids["organization"])

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://taskpilot.test") as client:
        path = f"{_approval_path(ids['task'], ids['run'], ids['approval'])}/approve"
        member_response = await client.post(path, headers=_headers(tokens["member"]))
        owner_response = await client.post(path, headers=_headers(tokens["owner"]))
        assert member_response.status_code == 403
        assert owner_response.status_code == 409

    async with factory() as session:
        approval = await session.get(Approval, ids["approval"])
        assert approval is not None and approval.status is ApprovalStatus.PENDING
