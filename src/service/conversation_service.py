"""Small, deterministic conversational facade over existing TaskPilot records."""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.llm import configured_model_identity, get_model
from core.settings import settings
from persistence.models import Task, TaskRun
from persistence.repositories import TaskRepository, TaskRunRepository
from schema.confirmed_intent import ConfirmedIntent
from schema.conversation_api import (
    ConversationTaskSummary,
    LLMIntent,
    ResultInterpretation,
    TaskProposal,
)
from service.logging import current_request_id
from service.session import CurrentPrincipal
from service.task_service import TaskService

logger = logging.getLogger(__name__)

_AREA_RE = re.compile(
    r"(?:分析|查看|研究|比较)(?P<area>[^，。:：,.！？!]{1,80}?)(?:最近|近几年|植被|NDVI|变化|有没有|是否|[，。:：,.！？!]|$)"
)
_DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_MONTH_RE = re.compile(r"(20\d{2})年\s*(1[0-2]|0?[1-9])月")
_RESULT_QUERY_FILLER = (
    "打开",
    "之前",
    "那个",
    "分析",
    "结果",
    "查看",
    "历史",
    "请",
    "帮我",
    "的",
)

_LLM_SEMAPHORE = asyncio.Semaphore(2)


class LLMConversationError(RuntimeError):
    """Safe, user-facing classification for a platform-managed LLM failure."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


def _model_text(value: Any) -> str:
    content = getattr(value, "content", value)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item) for item in content
        )
    return str(content)


def _json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model response is not a JSON object")
    value = json.loads(candidate[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model response is not an object")
    return value


def _proposal_from_intent(intent: LLMIntent, message: str) -> TaskProposal:
    indicator = intent.indicator or "NDVI"
    analysis_type = (
        intent.analysis_type
        or {
            "NDVI": "vegetation_change",
            "NDWI": "water_change",
            "NDBI": "urban_change",
        }[indicator]
    )
    area = intent.analysis_area
    if area in {"东湖", "武汉东湖", "wuhan_east_lake"}:
        area = "武汉东湖"
    elif area in {"江汉区", "武汉市江汉区", "jianghan_district_420103", "江汉"}:
        area = "武汉市江汉区"
    elif area:
        area = area.strip()
    return TaskProposal(
        title=(intent.title or "遥感变化分析")[:255],
        description=(intent.description or message)[:2000],
        analysis_area=area,
        analysis_type=analysis_type,
        indicator=indicator,
        period_a=intent.period_a,
        period_b=intent.period_b,
        required_parameters={
            "source": "Landsat-8/9" if area == "武汉市江汉区" else "Sentinel-2",
            **intent.required_parameters,
        },
        data_mode="real_stac_landsat_local"
        if area == "武汉市江汉区"
        else "local_real_raster_fixture",
    )


async def interpret_message(message: str, context: list[dict[str, str]] | None = None) -> LLMIntent:
    """Call the configured platform model and validate its bounded intent output."""

    if settings.USE_FAKE_MODEL and not settings.GEOCHANGE_LIVE_LLM:
        proposal = proposal_from_message(message)
        return LLMIntent(
            intent="new_analysis",
            response="我已整理出任务提案，请确认后再创建任务。",
            title=proposal.title,
            description=proposal.description,
            analysis_area=proposal.analysis_area,
            indicator=proposal.indicator,  # type: ignore[arg-type]
            analysis_type=proposal.analysis_type,  # type: ignore[arg-type]
            period_a=proposal.period_a,
            period_b=proposal.period_b,
            required_parameters=proposal.required_parameters,
        )
    if not settings.DEFAULT_MODEL or not settings.DEEPSEEK_API_KEY:
        raise LLMConversationError("unavailable", "AI 服务暂不可用，请由平台配置模型后重试。")
    bounded_context = [
        {"role": str(item.get("role", "user"))[:16], "content": str(item.get("content", ""))[:500]}
        for item in (context or [])[-8:]
        if isinstance(item, dict)
    ]
    system = (
        "你是 TaskPilot 的平台遥感助手。只输出结构化意图，不执行工具。"
        "支持新建武汉东湖 Sentinel-2 NDVI/NDWI/NDBI 双时相连续指数变化；"
        "不支持任意地点、在线下载、面积扩张结论。区分新分析、历史、结果、闲聊、澄清和不支持。"
        "若请求含新地点/指标/日期，即使出现‘结果’也必须是 new_analysis。"
        "日期必须输出 ISO 日期；缺少必要字段使用 clarification。"
        "输出 JSON 字段必须是 intent,response,title,description,analysis_area,indicator,"
        "analysis_type,period_a,period_b,required_parameters；period_a/period_b 使用"
        "{start,end}，闲聊和能力问答也必须保留 intent=response。"
    )
    prompt = {"message": message, "context": bounded_context}
    try:
        async with _LLM_SEMAPHORE:
            model = get_model(settings.DEFAULT_MODEL)
            result: Any = await asyncio.wait_for(
                model.ainvoke(
                    [
                        ("system", system),
                        ("human", str(prompt) + "\n只输出一个 JSON 对象，不要 Markdown。"),
                    ]
                ),
                timeout=settings.LLM_REQUEST_TIMEOUT,
            )
        intent = LLMIntent.model_validate(_json_object(_model_text(result)))
        if intent.intent == "chat" and any(
            phrase in message for phrase in ("你是什么模型", "什么模型", "模型身份", "你是谁")
        ):
            provider, model_name = configured_model_identity()
            intent.response = f"我是 TaskPilot AI 遥感助手，当前由平台配置的 {provider} 模型服务支持（{model_name}）。"
        elif intent.intent == "chat" and not intent.response:
            intent.response = "我可以分析武汉东湖缓存场景中的 NDVI、NDWI 和 NDBI 双时相连续指数变化，并在确认方案后执行任务。"
        return intent
    except TimeoutError as error:
        logger.warning(
            "conversation_llm_failure category=timeout provider=deepseek model=%s request_id=%s",
            settings.DEFAULT_MODEL,
            current_request_id(),
        )
        raise LLMConversationError("timeout", "AI 服务响应超时，请稍后重试。") from error
    except LLMConversationError:
        raise
    except Exception as error:
        category = (
            "malformed_response"
            if error.__class__.__name__ in {"ValidationError", "JSONDecodeError", "ValueError"}
            else "provider"
        )
        logger.warning(
            "conversation_llm_failure category=%s provider=deepseek model=%s request_id=%s",
            category,
            settings.DEFAULT_MODEL,
            current_request_id(),
        )
        message = (
            "AI 返回格式无法验证，请重试。"
            if category == "malformed_response"
            else "AI 服务暂时不可用，请稍后重试。"
        )
        raise LLMConversationError(category, message) from error


async def explain_result(metadata: dict[str, Any], question: str = "") -> ResultInterpretation:
    """Generate bounded prose from verifier-owned evidence only."""

    indicator = metadata.get("indicator")
    metrics = metadata.get("metrics")
    periods = metadata.get("analysis_periods")
    if metadata.get("verifier_status") != "passed" or not isinstance(metrics, dict):
        return ResultInterpretation(
            text="当前结果尚未完成验证，暂时不能生成分析解读。",
            evidence_status="unavailable",
            limitations=["结果验证状态不足"],
        )
    if settings.USE_FAKE_MODEL and not settings.GEOCHANGE_LIVE_LLM:
        return ResultInterpretation(
            text="结果已通过服务端验证，可查看两个时段的连续指数及其变化。",
            evidence_status="verified",
            limitations=["NDWI/NDBI 不代表水域或建设用地面积变化"],
        )
    if not settings.DEFAULT_MODEL or not settings.DEEPSEEK_API_KEY:
        raise LLMConversationError("unavailable", "AI 服务暂不可用，请由平台配置模型后重试。")
    evidence = {
        "indicator": indicator,
        "analysis_periods": periods,
        "metrics": metrics,
        "data_source": metadata.get("data_source"),
        "verifier_status": metadata.get("verifier_status"),
        "scientific_limit": {
            "NDVI": "植被指数变化，不等同植被面积变化",
            "NDWI": "连续水体相关指数，不确认水域面积或扩张",
            "NDBI": "连续建成区相关指数，不确认建设用地或城市扩张面积",
        }.get(str(indicator), "仅依据已验证指标证据解释"),
    }
    prompt = (
        "只基于以下已验证证据生成中文简短解读，不补造数值，不推断因果或面积。"
        "必须说明数据来源、时段和科学限制。只输出 JSON，字段严格为 text、evidence_status、"
        "limitations；evidence_status 只能是 verified、limited、unavailable，limitations 是字符串数组。"
        "用户问题：" + question[:500] + "\n证据：" + str(evidence)
    )
    try:
        async with _LLM_SEMAPHORE:
            model = get_model(settings.DEFAULT_MODEL)
            result: Any = await asyncio.wait_for(
                model.ainvoke(
                    [
                        ("system", "你是严格的科学结果解读助手。只输出 JSON 对象，不要 Markdown。"),
                        ("human", prompt),
                    ]
                ),
                timeout=settings.LLM_REQUEST_TIMEOUT,
            )
        try:
            return ResultInterpretation.model_validate(_json_object(_model_text(result)))
        except (ValueError, TypeError) as error:
            raise LLMConversationError(
                "malformed_response", "分析解读格式无法验证，请稍后重试。"
            ) from error
    except TimeoutError as error:
        raise LLMConversationError("timeout", "分析解读响应超时，请稍后重试。") from error
    except LLMConversationError:
        raise
    except Exception as error:
        raise LLMConversationError("provider", "分析解读服务暂时不可用，请稍后重试。") from error


def proposal_from_message(message: str) -> TaskProposal:
    """Interpret common product phrasing without giving an LLM execution authority."""

    text = message.strip()
    area_match = _AREA_RE.search(text)
    if any(token in text for token in ("江汉区", "江汉区", "Jianghan", "jianghan")):
        area = "武汉市江汉区"
    elif "武汉东湖" in text or "东湖" in text:
        area = "武汉东湖"
    else:
        area = area_match.group("area").strip() if area_match else None
    dates = [date.fromisoformat(value) for value in _DATE_RE.findall(text)]
    period_a = period_b = None
    if len(dates) == 4:
        from geochange.models import Period

        period_a = Period(start=dates[0], end=dates[1])
        period_b = Period(start=dates[2], end=dates[3])
    else:
        from geochange.models import Period

        months = [(int(year), int(month)) for year, month in _MONTH_RE.findall(text)]
        if len(months) >= 2:
            month_periods = []
            for year, month in months[:2]:
                month_periods.append(
                    Period(
                        start=date(year, month, 1),
                        end=date(year, month, calendar.monthrange(year, month)[1]),
                    )
                )
            period_a, period_b = month_periods
    if any(token in text.casefold() for token in ("ndbi", "urban", "built-up", "建成区", "城市")):
        analysis_type, indicator = "urban_change", "NDBI"
    elif "水" in text and "植被" not in text and "NDVI" not in text.upper():
        analysis_type, indicator = "water_change", "NDWI"
    else:
        analysis_type, indicator = "vegetation_change", "NDVI"
    area_label = area or "未指定区域"
    title = f"{area_label} {indicator}变化分析" if area else "遥感变化分析"
    return TaskProposal(
        title=title[:255],
        description=text,
        analysis_area=area,
        analysis_type=analysis_type,
        indicator=indicator,
        period_a=period_a,
        period_b=period_b,
        required_parameters={"source": "Landsat-8/9" if area == "武汉市江汉区" else "Sentinel-2"},
        data_mode="real_stac_landsat_local"
        if area == "武汉市江汉区"
        else "local_real_raster_fixture",
    )


def missing_proposal_fields(proposal: TaskProposal) -> list[str]:
    missing: list[str] = []
    if proposal.analysis_area is None:
        missing.append("analysis_area")
    if proposal.period_a is None:
        missing.append("period_a")
    if proposal.period_b is None:
        missing.append("period_b")
    return missing


def confirmed_intent_from_proposal(proposal: TaskProposal) -> ConfirmedIntent:
    return ConfirmedIntent.model_validate(
        {
            "analysis_type": proposal.analysis_type,
            "indicator": proposal.indicator,
            "analysis_area": proposal.analysis_area,
            "period_a": proposal.period_a,
            "period_b": proposal.period_b,
            "parameters": proposal.required_parameters,
            "data_mode": proposal.data_mode,
        }
    )


def validate_proposal(proposal: TaskProposal) -> TaskProposal:
    validated = TaskProposal.model_validate(proposal.model_dump())
    confirmed_intent_from_proposal(validated)
    return validated


def normalize_result_query(query: str) -> str:
    """Remove conversational filler while retaining searchable task words."""

    normalized = query.casefold().strip()
    for filler in _RESULT_QUERY_FILLER:
        normalized = normalized.replace(filler.casefold(), " ")
    return " ".join(normalized.split())


def _analysis_type(task: Task, run: TaskRun | None = None) -> str | None:
    task_intent = getattr(task, "confirmed_intent", None)
    if isinstance(task_intent, dict) and isinstance(task_intent.get("analysis_type"), str):
        return task_intent["analysis_type"]
    metadata = run.result_metadata if run is not None else None
    if isinstance(metadata, dict) and isinstance(metadata.get("analysis_type"), str):
        return metadata["analysis_type"]
    return None


async def history(
    session: AsyncSession, principal: CurrentPrincipal
) -> list[ConversationTaskSummary]:
    tasks = await TaskRepository(session).list_for_user_in_organization(
        principal.user_id, principal.organization_id
    )
    runs_repo = TaskRunRepository(session)
    summaries: list[ConversationTaskSummary] = []
    for task in tasks:
        runs = await runs_repo.list_for_task_in_organization(task.id, principal.organization_id)
        summaries.append(
            ConversationTaskSummary(
                task_id=task.id,
                title=task.title,
                analysis_type=_analysis_type(task, runs[-1] if runs else None),
                status=task.status.value,
                created_at=task.created_at,
                runs=[
                    {
                        "id": run.id,
                        "run_number": run.run_number,
                        "status": run.status.value,
                        "created_at": run.created_at,
                    }
                    for run in runs
                ],
            )
        )
    return summaries


async def find_result(
    session: AsyncSession, principal: CurrentPrincipal, query: str
) -> dict[str, object] | None:
    """Locate persisted result metadata; never synthesize historical evidence."""

    tasks = await TaskRepository(session).list_for_user_in_organization(
        principal.user_id, principal.organization_id
    )
    normalized_query = normalize_result_query(query)
    query_terms = [
        term.casefold()
        for term in re.findall(r"[\w\u4e00-\u9fff]+", normalized_query)
        if len(term) > 1
    ]
    for task in reversed(tasks):
        haystack = f"{task.title} {task.description or ''}".casefold()
        if query_terms and not any(term in haystack for term in query_terms):
            continue
        runs = await TaskRunRepository(session).list_for_task_in_organization(
            task.id, principal.organization_id
        )
        for run in reversed(runs):
            metadata = run.result_metadata
            if run.status.value == "succeeded" and isinstance(metadata, dict):
                return {
                    "task_id": task.id,
                    "task_title": task.title,
                    "task_run_id": run.id,
                    "status": run.status.value,
                    "result_metadata": metadata,
                    "artifact_names": sorted(
                        key
                        for key in metadata.get("artifact_references", {})
                        if isinstance(key, str)
                    ),
                }
    return None


async def create_confirmed_task(
    session: AsyncSession, principal: CurrentPrincipal, proposal: TaskProposal
) -> Task:
    validated = validate_proposal(proposal)
    return await TaskService(session).create_task(
        principal,
        title=validated.title,
        description=validated.description,
        confirmed_intent=confirmed_intent_from_proposal(validated).model_dump(mode="json"),
        proposal_id=validated.proposal_id,
    )


__all__ = [
    "create_confirmed_task",
    "find_result",
    "history",
    "proposal_from_message",
    "confirmed_intent_from_proposal",
    "normalize_result_query",
    "missing_proposal_fields",
    "validate_proposal",
    "interpret_message",
    "LLMConversationError",
    "_proposal_from_intent",
    "explain_result",
]
