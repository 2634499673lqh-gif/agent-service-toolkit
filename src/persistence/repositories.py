"""Small repositories for TaskPilot business models."""

from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.identity import canonicalize_email
from persistence.models import (
    AgentRun,
    Approval,
    AuthSession,
    Membership,
    Organization,
    Task,
    TaskRun,
    TaskRunStatus,
    ToolCall,
    User,
)


class TaskRepository:
    """Tenant-scoped persistence operations for Tasks.

    The caller supplies the server-derived organization scope explicitly. Every
    tenant-owned lookup keeps that scope in SQL so a foreign Task is observed
    as not found rather than fetched and checked in Python.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, task: Task) -> Task:
        """Stage and flush a Task without committing its transaction."""

        self.session.add(task)
        await self.session.flush()
        return task

    async def get_in_principal_tenant(
        self, task_id: UUID, principal_organization_id: UUID
    ) -> Task | None:
        """Load one Task only when it belongs to the trusted tenant scope."""

        statement = select(Task).where(
            Task.id == task_id,
            Task.organization_id == principal_organization_id,
        )
        return await self.session.scalar(statement)

    async def get_for_update_in_principal_tenant(
        self,
        task_id: UUID,
        principal_organization_id: UUID,
        *,
        nowait: bool = False,
    ) -> Task | None:
        """Load and lock one visible Task for a lifecycle transaction."""

        statement = (
            select(Task)
            .where(
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .with_for_update(nowait=nowait)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def next_run_number(self, task_id: UUID) -> int:
        """Return the next run number; callers lock the owning Task first."""

        statement = select(func.coalesce(func.max(TaskRun.run_number), 0) + 1).where(
            TaskRun.task_id == task_id
        )
        value = await self.session.scalar(statement)
        return int(value or 1)

    async def list_for_organization(self, organization_id: UUID) -> list[Task]:
        """List only Tasks owned by one trusted organization scope."""

        statement = (
            select(Task)
            .where(Task.organization_id == organization_id)
            .order_by(Task.created_at, Task.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class TaskRunRepository:
    """Tenant-scoped persistence operations for TaskRun history."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, task_run: TaskRun) -> TaskRun:
        """Stage and flush a TaskRun without committing its transaction."""

        self.session.add(task_run)
        await self.session.flush()
        return task_run

    async def get_in_principal_tenant(
        self, task_run_id: UUID, principal_organization_id: UUID
    ) -> TaskRun | None:
        """Load a TaskRun only through its Task's trusted tenant scope."""

        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_task_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        principal_organization_id: UUID,
    ) -> TaskRun | None:
        """Load one run only when it belongs to the requested visible Task."""

        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_task_and_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        principal_organization_id: UUID,
    ) -> tuple[Task, TaskRun] | None:
        """Load a Task and its requested run through one tenant-scoped SQL join.

        The runtime uses this query as its trust boundary before deriving a
        checkpoint identity.  Both the requested Task and nested TaskRun are
        constrained in SQL; neither checkpoint data nor a later Python
        organization comparison can make a foreign run visible.
        """

        statement = (
            select(Task, TaskRun)
            .join(TaskRun, TaskRun.task_id == Task.id)
            .where(
                Task.id == task_id,
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(statement)
        row = result.one_or_none()
        return None if row is None else (row[0], row[1])

    async def get_for_update_in_principal_tenant(
        self, task_run_id: UUID, principal_organization_id: UUID
    ) -> TaskRun | None:
        """Load and lock one visible TaskRun for a lifecycle transaction."""

        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                Task.organization_id == principal_organization_id,
            )
            .with_for_update()
        )
        return await self.session.scalar(statement)

    async def get_for_update_for_task_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        principal_organization_id: UUID,
        *,
        nowait: bool = False,
    ) -> TaskRun | None:
        """Load and lock one nested run after its tenant-scoped Task lock."""

        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .with_for_update(of=TaskRun, nowait=nowait)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_active_for_update(
        self,
        task_id: UUID,
        status: TaskRunStatus,
        *,
        nowait: bool = False,
    ) -> TaskRun | None:
        """Load and lock the sole active run expected by a Task state."""

        statement = (
            select(TaskRun)
            .where(
                TaskRun.task_id == task_id,
                TaskRun.status == status,
            )
            .with_for_update(nowait=nowait)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def list_for_task_in_organization(
        self, task_id: UUID, organization_id: UUID
    ) -> list[TaskRun]:
        """List all historical runs for a visible Task in run-number order."""

        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.task_id == task_id,
                Task.organization_id == organization_id,
            )
            .order_by(TaskRun.run_number, TaskRun.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class AgentRunRepository:
    """Tenant-scoped persistence operations for AgentRun observations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _parent_in_principal_tenant(
        self, task_run_id: UUID, principal_organization_id: UUID
    ) -> TaskRun | None:
        statement = (
            select(TaskRun)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def add(self, agent_run: AgentRun, principal_organization_id: UUID) -> AgentRun:
        """Validate the tenant-scoped parent, then flush without committing."""

        if (
            await self._parent_in_principal_tenant(agent_run.task_run_id, principal_organization_id)
            is None
        ):
            raise ValueError("TaskRun parent is not visible in the organization")
        self.session.add(agent_run)
        await self.session.flush()
        return agent_run

    async def add_in_principal_tenant(
        self, agent_run: AgentRun, principal_organization_id: UUID
    ) -> AgentRun:
        """Named alias for callers that want the tenant boundary explicit."""

        return await self.add(agent_run, principal_organization_id)

    async def get_in_principal_tenant(
        self, agent_run_id: UUID, principal_organization_id: UUID
    ) -> AgentRun | None:
        """Load one observation only through TaskRun -> Task ownership in SQL."""

        statement = (
            select(AgentRun)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                AgentRun.id == agent_run_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_task_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        agent_run_id: UUID,
        principal_organization_id: UUID,
    ) -> AgentRun | None:
        """Load one observation through the requested visible Task and run."""

        statement = (
            select(AgentRun)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                AgentRun.id == agent_run_id,
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def list_for_task_run_in_principal_tenant(
        self, task_id: UUID, task_run_id: UUID, principal_organization_id: UUID
    ) -> list[AgentRun]:
        """List immutable AgentRun evidence in deterministic event order."""

        statement = (
            select(AgentRun)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .order_by(AgentRun.started_at, AgentRun.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class ToolCallRepository:
    """Tenant-scoped persistence operations for ToolCall observations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _parent_in_principal_tenant(
        self, agent_run_id: UUID, principal_organization_id: UUID
    ) -> AgentRun | None:
        statement = (
            select(AgentRun)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                AgentRun.id == agent_run_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def add(self, tool_call: ToolCall, principal_organization_id: UUID) -> ToolCall:
        """Validate the tenant-scoped AgentRun parent, then flush only."""

        if (
            await self._parent_in_principal_tenant(
                tool_call.agent_run_id, principal_organization_id
            )
            is None
        ):
            raise ValueError("AgentRun parent is not visible in the organization")
        self.session.add(tool_call)
        await self.session.flush()
        return tool_call

    async def add_in_principal_tenant(
        self, tool_call: ToolCall, principal_organization_id: UUID
    ) -> ToolCall:
        """Named alias for callers that want the tenant boundary explicit."""

        return await self.add(tool_call, principal_organization_id)

    async def get_in_principal_tenant(
        self, tool_call_id: UUID, principal_organization_id: UUID
    ) -> ToolCall | None:
        """Load one ToolCall only through AgentRun -> TaskRun -> Task in SQL."""

        statement = (
            select(ToolCall)
            .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                ToolCall.id == tool_call_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_agent_run_in_principal_tenant(
        self,
        agent_run_id: UUID,
        tool_call_id: UUID,
        principal_organization_id: UUID,
    ) -> ToolCall | None:
        """Load one ToolCall beneath a visible AgentRun."""

        statement = (
            select(ToolCall)
            .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                ToolCall.id == tool_call_id,
                AgentRun.id == agent_run_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_task_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        tool_call_id: UUID,
        principal_organization_id: UUID,
    ) -> ToolCall | None:
        """Load one ToolCall through an explicitly visible Task and TaskRun."""

        statement = (
            select(ToolCall)
            .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                ToolCall.id == tool_call_id,
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def list_for_agent_run_in_principal_tenant(
        self, agent_run_id: UUID, principal_organization_id: UUID
    ) -> list[ToolCall]:
        """List one AgentRun's calls inside the principal tenant."""

        statement = (
            select(ToolCall)
            .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                AgentRun.id == agent_run_id,
                Task.organization_id == principal_organization_id,
            )
            .order_by(ToolCall.call_index, ToolCall.id)
        )
        result = await self.session.scalars(statement)
        return list(result)

    async def list_for_task_run_in_principal_tenant(
        self, task_id: UUID, task_run_id: UUID, principal_organization_id: UUID
    ) -> list[ToolCall]:
        """List all calls for a visible TaskRun through the normalized path."""

        statement = (
            select(ToolCall)
            .join(AgentRun, AgentRun.id == ToolCall.agent_run_id)
            .join(TaskRun, TaskRun.id == AgentRun.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .order_by(ToolCall.started_at, AgentRun.id, ToolCall.call_index, ToolCall.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class ApprovalRepository:
    """Tenant-scoped persistence operations for Approval records.

    Every read resolves ownership through Approval -> TaskRun -> Task in SQL.
    This repository flushes inserts but leaves transaction ownership to the
    calling service.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, approval: Approval) -> Approval:
        """Stage and flush an Approval without committing its transaction."""

        self.session.add(approval)
        await self.session.flush()
        return approval

    async def get_for_task_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        principal_organization_id: UUID,
    ) -> Approval | None:
        """Load one Approval only through its requested visible Task and run."""

        statement = (
            select(Approval)
            .join(TaskRun, TaskRun.id == Approval.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                Approval.id == approval_id,
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_update_for_task_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        principal_organization_id: UUID,
        *,
        nowait: bool = False,
    ) -> Approval | None:
        """Load and lock one Approval through its nested tenant ownership path."""

        statement = (
            select(Approval)
            .join(TaskRun, TaskRun.id == Approval.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                Approval.id == approval_id,
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .with_for_update(of=Approval, nowait=nowait)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_for_action_identity_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        replan_count: int,
        step_position: int,
        principal_organization_id: UUID,
    ) -> Approval | None:
        """Load an Approval by its canonical action identity inside one tenant."""

        statement = (
            select(Approval)
            .join(TaskRun, TaskRun.id == Approval.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
                Approval.replan_count == replan_count,
                Approval.step_position == step_position,
            )
        )
        return await self.session.scalar(statement)

    async def get_for_update_by_action_identity_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        replan_count: int,
        step_position: int,
        principal_organization_id: UUID,
        *,
        nowait: bool = False,
    ) -> Approval | None:
        """Load and lock one canonical action identity inside the tenant."""

        statement = (
            select(Approval)
            .join(TaskRun, TaskRun.id == Approval.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
                Approval.replan_count == replan_count,
                Approval.step_position == step_position,
            )
            .with_for_update(of=Approval, nowait=nowait)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def list_for_task_run_in_principal_tenant(
        self,
        task_id: UUID,
        task_run_id: UUID,
        principal_organization_id: UUID,
    ) -> list[Approval]:
        """List a run's Approvals only through the requested visible Task."""

        statement = (
            select(Approval)
            .join(TaskRun, TaskRun.id == Approval.task_run_id)
            .join(Task, Task.id == TaskRun.task_id)
            .where(
                TaskRun.id == task_run_id,
                TaskRun.task_id == task_id,
                Task.id == task_id,
                Task.organization_id == principal_organization_id,
            )
            .order_by(Approval.created_at, Approval.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class OrganizationRepository:
    """Persistence operations for organizations; transaction ownership stays above."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, organization: Organization) -> Organization:
        """Stage and flush an organization without committing its transaction."""

        self.session.add(organization)
        await self.session.flush()
        return organization

    async def get(self, organization_id: UUID) -> Organization | None:
        """Load one organization by its tenant identifier."""

        return await self.session.get(Organization, organization_id)

    async def get_by_name(self, name: str) -> Organization | None:
        """Load one organization by its display name."""

        statement = select(Organization).where(Organization.name == name)
        return await self.session.scalar(statement)

    async def get_in_principal_tenant(
        self, organization_id: UUID, principal_organization_id: UUID
    ) -> Organization | None:
        """Load an organization only when the row is inside the principal's tenant.

        The tenant predicate lives in the SQL, so a row outside the principal's
        tenant is simply *not found* and never reaches an authorization
        decision - which would otherwise let a caller distinguish tenants
        through a 403/404 difference.  Callers pass the server-derived
        ``CurrentPrincipal.organization_id`` as the scope; a caller-supplied
        identifier is never accepted as the tenant.

        For an organization row the tenant *is* the organization, so the
        resource and scope arguments hold the same value: the principal can only
        ever address its own tenant's row.  The two predicates are kept explicit
        because the tenant-owned resources that follow (tasks, runs, approvals)
        receive a distinct ``organization_id`` column and reuse this shape.
        """

        statement = select(Organization).where(
            Organization.id == organization_id,
            Organization.id == principal_organization_id,
        )
        return await self.session.scalar(statement)


class UserRepository:
    """Persistence operations for users; transaction ownership stays above."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, user: User) -> User:
        """Stage and flush a user without committing its transaction."""

        self.session.add(user)
        await self.session.flush()
        return user

    async def get(self, user_id: UUID) -> User | None:
        """Load one user by its identity identifier."""

        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Load one user using the shared application email identity contract."""

        _, normalized_email = canonicalize_email(email)
        return await self.get_by_normalized_email(normalized_email)

    async def get_by_normalized_email(self, normalized_email: str) -> User | None:
        """Load one user by an already canonicalized email identity."""

        statement = select(User).where(User.normalized_email == normalized_email)
        return await self.session.scalar(statement)


class MembershipRepository:
    """Tenant-scoped membership persistence; transaction ownership stays above.

    Every lookup answers a question that already names the organization scope,
    which keeps the future principal chain (user -> membership -> organization
    -> role) expressible without introducing a global membership listing.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, membership: Membership) -> Membership:
        """Stage and flush a membership without committing its transaction."""

        self.session.add(membership)
        await self.session.flush()
        return membership

    async def get(self, membership_id: UUID) -> Membership | None:
        """Load one membership by its identity identifier."""

        return await self.session.get(Membership, membership_id)

    async def get_for_user_in_organization(
        self, user_id: UUID, organization_id: UUID
    ) -> Membership | None:
        """Load the single membership for one user inside one organization."""

        statement = select(Membership).where(
            Membership.user_id == user_id,
            Membership.organization_id == organization_id,
        )
        return await self.session.scalar(statement)

    async def get_active_for_principal(
        self,
        membership_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ) -> Membership | None:
        """Revalidate a principal's active membership in its selected tenant."""

        statement = (
            select(Membership)
            .where(
                Membership.id == membership_id,
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
                Membership.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def get_active_for_principal_for_update(
        self,
        membership_id: UUID,
        user_id: UUID,
        organization_id: UUID,
    ) -> Membership | None:
        """Load and share-lock the active principal membership for a write."""

        statement = (
            select(Membership)
            .where(
                Membership.id == membership_id,
                Membership.user_id == user_id,
                Membership.organization_id == organization_id,
                Membership.is_active.is_(True),
            )
            .with_for_update(read=True, of=Membership)
            .execution_options(populate_existing=True)
        )
        return await self.session.scalar(statement)

    async def list_for_organization(self, organization_id: UUID) -> list[Membership]:
        """List the memberships owned by one organization scope."""

        statement = select(Membership).where(Membership.organization_id == organization_id)
        statement = statement.order_by(Membership.created_at, Membership.id)
        result = await self.session.scalars(statement)
        return list(result)

    async def list_for_user(self, user_id: UUID) -> list[Membership]:
        """List one user's memberships.

        This is deliberately the only user-scoped membership query.  Login must
        resolve the caller's own membership before any organization scope
        exists, and it fails closed when the result is not a single active row.
        """

        statement = select(Membership).where(Membership.user_id == user_id)
        statement = statement.order_by(Membership.created_at, Membership.id)
        result = await self.session.scalars(statement)
        return list(result)

    async def list_eligible_for_user(self, user_id: UUID) -> list[Membership]:
        """List one user's memberships whose organization also exists and is active.

        Eligibility is evaluated in PostgreSQL - user scope, active membership,
        and an existing active organization - so login never filters tenants in
        Python and never counts an inactive or dangling row as selectable.
        """

        statement = (
            select(Membership)
            .join(Organization, Organization.id == Membership.organization_id)
            .where(
                Membership.user_id == user_id,
                Membership.is_active.is_(True),
                Organization.is_active.is_(True),
            )
            .order_by(Membership.created_at, Membership.id)
        )
        result = await self.session.scalars(statement)
        return list(result)


class AuthSessionRepository:
    """Opaque session persistence; transaction ownership stays above.

    Lookups use the token digest only.  The raw token is never a parameter name,
    never stored, and never returned from this boundary.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, auth_session: AuthSession) -> AuthSession:
        """Stage and flush a session without committing its transaction."""

        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def get(self, session_id: UUID) -> AuthSession | None:
        """Load one session by its identity identifier."""

        return await self.session.get(AuthSession, session_id)

    async def get_by_token_hash(self, token_hash: str) -> AuthSession | None:
        """Load the single session owning ``token_hash`` (never a raw token)."""

        statement = select(AuthSession).where(AuthSession.token_hash == token_hash)
        return await self.session.scalar(statement)

    async def revoke(self, auth_session: AuthSession, revoked_at: datetime) -> AuthSession:
        """Mark one session revoked; the row is retained for audit."""

        auth_session.revoked_at = revoked_at
        await self.session.flush()
        return auth_session

    async def delete_expired(self, now: datetime) -> int:
        """Delete only rows that already expired; explicit revocation is never deleted."""

        statement = delete(AuthSession).where(AuthSession.expires_at < now)
        result = cast(CursorResult, await self.session.execute(statement))
        return int(result.rowcount or 0)
