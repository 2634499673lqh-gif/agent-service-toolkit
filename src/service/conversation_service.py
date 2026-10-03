"""Small, deterministic conversational facade over existing TaskPilot records."""

from __future__ import annotations

import calendar
import re
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Task, TaskRun
from persistence.repositories import TaskRepository, TaskRunRepository
from schema.confirmed_intent import ConfirmedIntent
from schema.conversation_api import ConversationTaskSummary, TaskProposal
from service.session import CurrentPrincipal
from service.task_service import TaskService

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


def proposal_from_message(message: str) -> TaskProposal:
    """Interpret common product phrasing without giving an LLM execution authority."""

    text = message.strip()
    area_match = _AREA_RE.search(text)
    if "武汉东湖" in text or "东湖" in text:
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
        required_parameters={"source": "Sentinel-2"},
    )


def missing_proposal_fields(proposal: TaskProposal) -> list[str]:
    missing: list[str] = []
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
]
