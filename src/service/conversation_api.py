"""Protected conversational facade for TaskPilot Product users."""

import logging

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import ValidationError

from core.settings import settings
from schema.conversation_api import (
    ConfirmTaskRequest,
    ConversationRequest,
    ConversationResponse,
    ConversationTaskSummary,
    TaskProposal,
)
from service.auth_dependency import PrincipalDependency
from service.conversation_service import (
    LLMConversationError,
    _proposal_from_intent,
    create_confirmed_task,
    find_result,
    history,
    interpret_message,
    missing_proposal_fields,
    proposal_from_message,
)
from service.task_api import TaskSessionDependency
from service.task_service import ExistingAnalysisError

logger = logging.getLogger(__name__)

conversation_router = APIRouter(prefix="/api/v1/conversation", tags=["conversation"])


@conversation_router.get("/history", response_model=list[ConversationTaskSummary])
async def conversation_history(
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> list[ConversationTaskSummary]:
    """Return persisted history without spending an LLM call or creating a task."""

    return await history(session, principal)


@conversation_router.post("", response_model=ConversationResponse)
async def converse(
    payload: ConversationRequest,
    principal: PrincipalDependency,
    session: TaskSessionDependency,
) -> ConversationResponse:
    assistant_message = "我已整理出任务提案，请确认后再创建任务。"
    if settings.USE_FAKE_MODEL and not settings.GEOCHANGE_LIVE_LLM:
        text = payload.message.casefold()
        if any(word in text for word in ("打开", "查看结果", "结果", "open", "result")):
            result = await find_result(session, principal, payload.message)
            if result is None:
                return ConversationResponse(
                    kind="result_not_found", message="没有找到可展示的历史分析结果。"
                )
            return ConversationResponse(
                kind="result", message="已找到历史分析结果。", result=result
            )
        if any(word in text for word in ("之前", "历史", "做过", "history")):
            records = await history(session, principal)
            return ConversationResponse(
                kind="history", message=f"找到 {len(records)} 条分析任务记录。", tasks=records
            )
        try:
            proposal = proposal_from_message(payload.message)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="无法生成有效任务提案。") from error
    else:
        try:
            intent = await interpret_message(payload.message, payload.context)
        except LLMConversationError as error:
            return ConversationResponse(kind="llm_unavailable", message=str(error))
        if intent.intent == "history":
            records = await history(session, principal)
            return ConversationResponse(
                kind="history", message=f"找到 {len(records)} 条分析任务记录。", tasks=records
            )
        if intent.intent == "result":
            result = await find_result(session, principal, payload.message)
            return (
                ConversationResponse(kind="result", message="已找到历史分析结果。", result=result)
                if result
                else ConversationResponse(
                    kind="result_not_found", message="没有找到可展示的历史分析结果。"
                )
            )
        if intent.intent in {"chat", "unsupported"}:
            return ConversationResponse(
                kind=intent.intent, message=intent.response or "这个请求目前不在支持范围内。"
            )
        proposal = _proposal_from_intent(intent, payload.message)
        if intent.response:
            assistant_message = intent.response
        else:
            assistant_message = "我已整理出任务提案，请确认后再创建任务。"
    missing = missing_proposal_fields(proposal)
    if missing:
        return ConversationResponse(
            kind="clarification",
            message=assistant_message,
            proposal=proposal,
            missing_fields=missing,
        )
    return ConversationResponse(
        kind="proposal",
        message=assistant_message,
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
    except ExistingAnalysisError as error:
        logger.info(
            "conversation_confirmation_existing task_id=%s status=%s",
            error.task_id,
            error.task_status,
        )
        response.status_code = status.HTTP_200_OK
        return ConversationResponse(
            kind="existing_analysis",
            message=(
                "这份分析方案已经提交过了。请从“我的分析”打开已有结果，"
                "如需重新运行，请重新发送分析请求。"
            ),
            proposal=TaskProposal.model_validate(payload.proposal),
            result={"task_id": error.task_id, "status": error.task_status},
        )
    except (ValueError, ValidationError) as error:
        logger.info("conversation_confirmation_invalid error_type=%s", type(error).__name__)
        raise HTTPException(
            status_code=422,
            detail="分析方案无法通过安全校验，请检查区域、指标和比较时段。",
        ) from None
    return ConversationResponse(
        kind="task_created",
        message="任务已创建为草稿。请在任务页面确认后开始执行。",
        proposal=TaskProposal.model_validate(payload.proposal),
        result={"task_id": task.id, "status": task.status.value},
    )


__all__ = ["conversation_router"]
