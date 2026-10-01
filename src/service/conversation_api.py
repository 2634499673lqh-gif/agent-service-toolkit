"""Protected conversational facade for TaskPilot Product users."""

from fastapi import APIRouter, HTTPException, Response, status

from schema.conversation_api import (
    ConfirmTaskRequest,
    ConversationRequest,
    ConversationResponse,
    TaskProposal,
)
from service.auth_dependency import PrincipalDependency
from service.conversation_service import (
    create_confirmed_task,
    find_result,
    history,
    missing_proposal_fields,
    proposal_from_message,
)
from service.task_api import TaskSessionDependency

conversation_router = APIRouter(prefix="/api/v1/conversation", tags=["conversation"])


@conversation_router.post("", response_model=ConversationResponse)
async def converse(
    payload: ConversationRequest,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> ConversationResponse:
    text = payload.message.casefold()
    if any(word in text for word in ("打开", "查看结果", "结果", "open", "result")):
        result = await find_result(session, principal, payload.message)
        if result is None:
            return ConversationResponse(
                kind="result_not_found", message="没有找到可展示的历史分析结果。"
            )
        return ConversationResponse(kind="result", message="已找到历史分析结果。", result=result)
    if any(word in text for word in ("之前", "历史", "做过", "history")):
        records = await history(session, principal)
        return ConversationResponse(
            kind="history",
            message=f"找到 {len(records)} 条分析任务记录。",
            tasks=records,
        )
    try:
        proposal = proposal_from_message(payload.message)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="无法生成有效任务提案。") from error
    missing = missing_proposal_fields(proposal)
    if missing:
        return ConversationResponse(
            kind="clarification",
            message="请补充两个明确、先后不重叠的比较时段（period_a 和 period_b）。",
            proposal=proposal,
            missing_fields=missing,
        )
    return ConversationResponse(
        kind="proposal",
        message="我已整理出任务提案，请确认后再创建任务。",
        proposal=proposal,
    )


@conversation_router.post("/confirm", response_model=ConversationResponse)
async def confirm_conversation_task(
    payload: ConfirmTaskRequest,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
    response: Response,
) -> ConversationResponse:
    try:
        missing = missing_proposal_fields(payload.proposal)
        if missing:
            return ConversationResponse(
                kind="clarification",
                message="该提案还缺少两个明确的比较时段，请补充后再确认。",
                proposal=payload.proposal,
                missing_fields=missing,
            )
        task = await create_confirmed_task(session, principal, payload.proposal)
        response.status_code = status.HTTP_201_CREATED
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ConversationResponse(
        kind="task_created",
        message="任务已创建为草稿。请在任务页面确认后开始执行。",
        proposal=TaskProposal.model_validate(payload.proposal),
        result={"task_id": task.id, "status": task.status.value},
    )


__all__ = ["conversation_router"]
