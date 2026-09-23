"""Protected nested Approval inspection and decision routes (T082)."""

from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from persistence.models import Approval
from schema.approval_api import ApprovalDecisionRequest, ApprovalResponse
from service.approval_service import (
    APPROVAL_CONFLICT_DETAIL,
    ApprovalAuthenticationError,
    ApprovalConflictError,
    ApprovalNotFoundError,
    ApprovalService,
)
from service.auth_dependency import PrincipalDependency
from service.authorization import (
    AUTHENTICATION_FAILED_DETAIL,
    RESOURCE_NOT_FOUND_DETAIL,
    WWW_AUTHENTICATE_HEADER,
    AuthorizationError,
)
from service.logging import redact_text, redact_value
from service.task_api import TaskSessionDependency

approval_router = APIRouter(prefix="/api/v1/tasks", tags=["approvals"])


@approval_router.get(
    "/{task_id}/runs/{run_id}/approvals",
    response_model=list[ApprovalResponse],
)
async def list_approvals(
    task_id: UUID,
    run_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> list[ApprovalResponse]:
    try:
        approvals = await ApprovalService(session).list_for_task_run(
            principal, task_id=task_id, task_run_id=run_id
        )
    except (ApprovalNotFoundError, ApprovalAuthenticationError) as error:
        _raise_approval_error(error)
    return [_approval_response(approval) for approval in approvals]


@approval_router.get(
    "/{task_id}/runs/{run_id}/approvals/{approval_id}",
    response_model=ApprovalResponse,
)
async def get_approval(
    task_id: UUID,
    run_id: UUID,
    approval_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> ApprovalResponse:
    try:
        approval = await ApprovalService(session).get(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=approval_id,
        )
    except (ApprovalNotFoundError, ApprovalAuthenticationError) as error:
        _raise_approval_error(error)
    return _approval_response(approval)


@approval_router.post(
    "/{task_id}/runs/{run_id}/approvals/{approval_id}/approve",
    response_model=ApprovalResponse,
)
async def approve_approval(
    task_id: UUID,
    run_id: UUID,
    approval_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
    payload: ApprovalDecisionRequest | None = None,
) -> ApprovalResponse:
    try:
        approval = await ApprovalService(session).approve(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=approval_id,
            reason=payload.reason if payload is not None else None,
        )
    except (ApprovalNotFoundError, ApprovalAuthenticationError, ApprovalConflictError) as error:
        _raise_approval_error(error)
    except AuthorizationError as error:
        _raise_approval_error(error)
    return _approval_response(approval)


@approval_router.post(
    "/{task_id}/runs/{run_id}/approvals/{approval_id}/reject",
    response_model=ApprovalResponse,
)
async def reject_approval(
    task_id: UUID,
    run_id: UUID,
    approval_id: UUID,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
    payload: ApprovalDecisionRequest | None = None,
) -> ApprovalResponse:
    try:
        approval = await ApprovalService(session).reject(
            principal,
            task_id=task_id,
            task_run_id=run_id,
            approval_id=approval_id,
            reason=payload.reason if payload is not None else None,
        )
    except (ApprovalNotFoundError, ApprovalAuthenticationError, ApprovalConflictError) as error:
        _raise_approval_error(error)
    except AuthorizationError as error:
        _raise_approval_error(error)
    return _approval_response(approval)


def _approval_response(approval: Approval) -> ApprovalResponse:
    """Map persisted evidence into the sanitized public response contract."""

    return ApprovalResponse(
        id=approval.id,
        task_run_id=approval.task_run_id,
        replan_count=approval.replan_count,
        step_position=approval.step_position,
        action_name=approval.action_name,
        action_version=approval.action_version,
        proposed_action=redact_value(approval.proposed_action),
        risk_level=approval.risk_level,
        status=approval.status,
        requester_membership_id=approval.requester_membership_id,
        decider_membership_id=approval.decider_membership_id,
        decided_at=approval.decided_at,
        decision_reason=(
            redact_text(approval.decision_reason) if approval.decision_reason is not None else None
        ),
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )


def _raise_approval_error(error: Exception) -> NoReturn:
    if isinstance(error, ApprovalNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=RESOURCE_NOT_FOUND_DETAIL,
        ) from None
    if isinstance(error, ApprovalAuthenticationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=AUTHENTICATION_FAILED_DETAIL,
            headers=dict(WWW_AUTHENTICATE_HEADER),
        ) from None
    if isinstance(error, AuthorizationError):
        raise HTTPException(status_code=error.status_code, detail=error.detail) from None
    if isinstance(error, ApprovalConflictError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=APPROVAL_CONFLICT_DETAIL,
        ) from None
    raise error


__all__ = ["approval_router"]
