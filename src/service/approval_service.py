"""Approval persistence use cases and terminal human decisions (T082)."""

import json
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import (
    APPROVAL_JSON_MAX_BYTES,
    Approval,
    ApprovalRiskLevel,
    ApprovalStatus,
    Membership,
    Task,
    TaskRun,
    TaskRunStatus,
    TaskStatus,
    utc_now,
)
from persistence.repositories import (
    ApprovalRepository,
    MembershipRepository,
    TaskRepository,
    TaskRunRepository,
)
from service.authorization import APPROVAL_DECISION_ROLES, require_role
from service.logging import redact_text, redact_value
from service.session import CurrentPrincipal

logger = logging.getLogger(__name__)

APPROVAL_CONFLICT_DETAIL = "Approval conflict"
APPROVAL_UNIQUE_CONSTRAINT = "uq_approvals_run_replan_step"


class ApprovalError(Exception):
    """Base class for non-disclosing Approval service errors."""


class ApprovalAuthenticationError(ApprovalError):
    """The supplied principal no longer matches an active membership."""


class ApprovalNotFoundError(ApprovalError):
    """The requested Task, run, or Approval is not visible in the tenant."""


class ApprovalConflictError(ApprovalError):
    """The requested create or decision conflicts with durable Approval state."""


class ApprovalRunNotActiveError(ApprovalConflictError):
    """A runtime checkpoint references a cancelled or terminal business run."""


@dataclass(frozen=True, slots=True)
class ApprovalProposal:
    """Trusted server-selected action metadata and its typed, validated arguments.

    This value is an internal service input. It is intentionally not an HTTP
    request model: the runtime selects the action and validates its arguments
    before handing a proposal to this service.
    """

    action_name: str
    action_version: str
    proposed_action: dict[str, Any]


class ApprovalService:
    """Coordinate Approval reads, create/reuse, and first-writer decisions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.runs = TaskRunRepository(session)
        self.approvals = ApprovalRepository(session)
        self.memberships = MembershipRepository(session)

    async def create_or_reuse(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        replan_count: int,
        step_position: int,
        proposal: ApprovalProposal,
    ) -> Approval:
        """Persist or return one immutable approval for a validated plan slot."""

        try:
            if (
                not isinstance(replan_count, int)
                or isinstance(replan_count, bool)
                or replan_count not in (0, 1)
                or not isinstance(step_position, int)
                or isinstance(step_position, bool)
                or step_position < 0
            ):
                raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
            task, run = await self._lock_task_and_run(principal, task_id, task_run_id)
            await self._require_active_run(task, run)
            canonical_proposal = self._canonical_proposal(proposal)
            existing = await self.approvals.get_for_update_by_action_identity_in_principal_tenant(
                task.id,
                run.id,
                replan_count,
                step_position,
                principal.organization_id,
            )
            requester = await self._active_membership(principal, for_update=True)
            if existing is not None:
                if not self._same_proposal(
                    existing,
                    proposal.action_name,
                    proposal.action_version,
                    canonical_proposal,
                ):
                    raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
                await self.session.commit()
                return existing

            approval = Approval(
                task_run_id=run.id,
                replan_count=replan_count,
                step_position=step_position,
                action_name=proposal.action_name,
                action_version=proposal.action_version,
                proposed_action=canonical_proposal,
                risk_level=ApprovalRiskLevel.L2,
                requester_membership_id=requester.id,
            )
            await self.approvals.add(approval)
            await self.session.commit()
            logger.info(
                "approval.created",
                extra={"event": "approval.created", "approval_id": str(approval.id)},
            )
            return approval
        except IntegrityError as error:
            await self.session.rollback()
            if self._constraint_name(error) == APPROVAL_UNIQUE_CONSTRAINT:
                raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL) from None
            raise
        except BaseException:
            await self.session.rollback()
            raise

    async def list_for_task_run(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
    ) -> list[Approval]:
        """List audit-visible Approvals only after confirming the nested run."""

        parent = await self.runs.get_task_and_run_in_principal_tenant(
            task_id, task_run_id, principal.organization_id
        )
        if parent is None:
            raise ApprovalNotFoundError("Approval parent is not visible")
        await self._active_membership(principal)
        return await self.approvals.list_for_task_run_in_principal_tenant(
            task_id, task_run_id, principal.organization_id
        )

    async def validate_runtime_resume(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        replan_count: int,
        step_position: int,
        proposal: ApprovalProposal,
    ) -> Approval | None:
        """Revalidate a checkpoint reference against active business state.

        ``None`` means the reference, canonical slot, or immutable proposal did
        not match. An inactive or terminal run raises the same non-disclosing
        conflict as create/decision, so stale checkpoint data cannot win over
        T035 lifecycle state.
        """

        try:
            if (
                not isinstance(replan_count, int)
                or isinstance(replan_count, bool)
                or replan_count not in (0, 1)
                or not isinstance(step_position, int)
                or isinstance(step_position, bool)
                or step_position < 0
            ):
                await self.session.rollback()
                return None

            task, run = await self._lock_task_and_run(principal, task_id, task_run_id)
            if not await self._is_active_run(task, run):
                raise ApprovalRunNotActiveError(APPROVAL_CONFLICT_DETAIL)
            approval = await self.approvals.get_for_update_for_task_run_in_principal_tenant(
                task.id,
                run.id,
                approval_id,
                principal.organization_id,
            )
            if approval is None:
                await self.session.commit()
                return None

            await self._active_membership(principal, for_update=True)
            try:
                canonical_proposal = self._canonical_proposal(proposal)
            except ApprovalConflictError:
                await self.session.rollback()
                return None
            if (
                approval.replan_count != replan_count
                or approval.step_position != step_position
                or not self._same_proposal(
                    approval,
                    proposal.action_name,
                    proposal.action_version,
                    canonical_proposal,
                )
            ):
                await self.session.commit()
                return None

            await self.session.commit()
            return approval
        except BaseException:
            await self.session.rollback()
            raise

    async def get(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
    ) -> Approval:
        """Read one Approval through TaskRun and Task tenant ownership in SQL."""

        approval = await self.approvals.get_for_task_run_in_principal_tenant(
            task_id, task_run_id, approval_id, principal.organization_id
        )
        if approval is None:
            raise ApprovalNotFoundError("Approval is not visible")
        await self._active_membership(principal)
        return approval

    async def approve(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> Approval:
        """Commit approval once; every later or competing decision conflicts."""

        return await self._decide(
            principal,
            task_id=task_id,
            task_run_id=task_run_id,
            approval_id=approval_id,
            decision=ApprovalStatus.APPROVED,
            reason=reason,
        )

    async def reject(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        reason: str | None = None,
    ) -> Approval:
        """Commit rejection once; a rejected approval remains terminal evidence."""

        return await self._decide(
            principal,
            task_id=task_id,
            task_run_id=task_run_id,
            approval_id=approval_id,
            decision=ApprovalStatus.REJECTED,
            reason=reason,
        )

    async def _decide(
        self,
        principal: CurrentPrincipal,
        *,
        task_id: UUID,
        task_run_id: UUID,
        approval_id: UUID,
        decision: ApprovalStatus,
        reason: str | None,
    ) -> Approval:
        try:
            task, run = await self._lock_task_and_run(principal, task_id, task_run_id)
            active_run_matches = await self._is_active_run(task, run)
            approval = await self.approvals.get_for_update_for_task_run_in_principal_tenant(
                task.id,
                run.id,
                approval_id,
                principal.organization_id,
            )
            if approval is None:
                raise ApprovalNotFoundError("Approval is not visible")

            membership = await self._active_membership(principal, for_update=True)
            require_role(_principal_with_role(principal, membership), APPROVAL_DECISION_ROLES)

            if not active_run_matches:
                raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
            if approval.status is not ApprovalStatus.PENDING:
                raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)

            approval.status = decision
            approval.decider_membership_id = membership.id
            decided_at = utc_now()
            approval.decided_at = decided_at
            approval.decision_reason = redact_text(reason) if reason is not None else None
            approval.updated_at = decided_at
            await self.session.flush()
            await self.session.commit()
            logger.info(
                "approval.decided",
                extra={
                    "event": "approval.decided",
                    "approval_id": str(approval.id),
                    "decision": decision.value,
                },
            )
            return approval
        except BaseException:
            await self.session.rollback()
            raise

    async def _lock_task_and_run(
        self,
        principal: CurrentPrincipal,
        task_id: UUID,
        task_run_id: UUID,
    ) -> tuple[Task, TaskRun]:
        task = await self.tasks.get_for_update_in_principal_tenant(
            task_id, principal.organization_id
        )
        if task is None:
            raise ApprovalNotFoundError("Task is not visible")
        run = await self.runs.get_for_update_for_task_in_principal_tenant(
            task.id, task_run_id, principal.organization_id
        )
        if run is None:
            raise ApprovalNotFoundError("TaskRun is not visible")
        return task, run

    async def _require_active_run(self, task: Task, run: TaskRun) -> None:
        if not await self._is_active_run(task, run):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)

    async def _is_active_run(self, task: Task, run: TaskRun) -> bool:
        active = await self.runs.get_active_for_update(task.id, TaskRunStatus.RUNNING)
        return not (
            task.status is not TaskStatus.RUNNING
            or run.status is not TaskRunStatus.RUNNING
            or active is None
            or active.id != run.id
        )

    async def _active_membership(
        self, principal: CurrentPrincipal, *, for_update: bool = False
    ) -> Membership:
        get_membership = (
            self.memberships.get_active_for_principal_for_update
            if for_update
            else self.memberships.get_active_for_principal
        )
        membership = await get_membership(
            principal.membership_id, principal.user_id, principal.organization_id
        )
        if membership is None:
            raise ApprovalAuthenticationError("Principal is no longer active")
        return membership

    @staticmethod
    def _canonical_proposal(proposal: ApprovalProposal) -> dict[str, Any]:
        if (
            not isinstance(proposal.action_name, str)
            or not proposal.action_name.strip()
            or len(proposal.action_name) > 128
            or not isinstance(proposal.action_version, str)
            or not proposal.action_version.strip()
            or len(proposal.action_version) > 64
        ):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
        if not isinstance(proposal.proposed_action, dict):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
        sanitized = redact_value(proposal.proposed_action)
        if not isinstance(sanitized, dict):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
        try:
            canonical = json.dumps(
                sanitized,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if len(canonical) > APPROVAL_JSON_MAX_BYTES:
                raise ValueError("bounded proposal required")
            normalized = json.loads(canonical)
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL) from None
        if not isinstance(normalized, dict):
            raise ApprovalConflictError(APPROVAL_CONFLICT_DETAIL)
        return normalized

    @classmethod
    def _same_proposal(
        cls,
        existing: Approval,
        action_name: str,
        action_version: str,
        candidate: dict[str, Any],
    ) -> bool:
        existing_canonical = cls._canonical_object(existing.proposed_action)
        candidate_canonical = cls._canonical_object(candidate)
        return (
            existing.action_name == action_name
            and existing.action_version == action_version
            and existing_canonical is not None
            and candidate_canonical is not None
            and existing_canonical == candidate_canonical
        )

    @staticmethod
    def _canonical_object(value: dict[str, Any]) -> bytes | None:
        try:
            canonical = json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            return None
        return canonical if len(canonical) <= APPROVAL_JSON_MAX_BYTES else None

    @staticmethod
    def _constraint_name(error: IntegrityError) -> str | None:
        diagnostic = getattr(error.orig, "diag", None)
        return getattr(diagnostic, "constraint_name", None)


def _principal_with_role(principal: CurrentPrincipal, membership: Membership) -> CurrentPrincipal:
    """Return the same server-verified identity with its current persisted role."""

    if (
        principal.user_id != membership.user_id
        or principal.membership_id != membership.id
        or principal.organization_id != membership.organization_id
    ):
        raise ApprovalAuthenticationError("Principal membership changed")
    return CurrentPrincipal(
        user_id=principal.user_id,
        membership_id=principal.membership_id,
        organization_id=principal.organization_id,
        role=membership.role,
        session_id=principal.session_id,
    )


__all__ = [
    "APPROVAL_CONFLICT_DETAIL",
    "ApprovalAuthenticationError",
    "ApprovalConflictError",
    "ApprovalNotFoundError",
    "ApprovalProposal",
    "ApprovalService",
]
