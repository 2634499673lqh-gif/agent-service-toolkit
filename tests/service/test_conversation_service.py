from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.responses import Response

from schema.confirmed_intent import ConfirmedIntent
from schema.conversation_api import (
    ConfirmTaskRequest,
    ConversationRequest,
    LLMIntent,
    ResultInterpretation,
    TaskProposal,
)
from service import conversation_api, conversation_service
from service.conversation_service import (
    LLMConversationError,
    confirmed_intent_from_proposal,
    create_confirmed_task,
    missing_proposal_fields,
    normalize_result_query,
    proposal_from_message,
    validate_proposal,
)
from service.task_service import (
    ExistingAnalysisError,
    ProposalIdentityMismatchError,
    TaskService,
)


def test_proposal_from_chinese_request_extracts_area_and_years() -> None:
    proposal = proposal_from_message("帮我分析武汉东湖最近几年植被有没有变化。")

    assert proposal.analysis_area == "武汉东湖"
    assert proposal.analysis_type == "vegetation_change"
    assert proposal.indicator == "NDVI"
    assert proposal.required_parameters["source"] == "Sentinel-2"
    assert missing_proposal_fields(proposal) == ["period_a", "period_b"]


def test_message_with_two_explicit_periods_completes_proposal() -> None:
    proposal = proposal_from_message(
        "分析武汉东湖，时段A 2023-07-01至2023-07-31，时段B 2024-07-01至2024-07-31"
    )
    assert missing_proposal_fields(proposal) == []
    assert proposal.analysis_area == "武汉东湖"
    assert proposal.period_a is not None
    assert proposal.period_b is not None


@pytest.mark.parametrize("indicator", ["NDVI", "NDWI", "NDBI"])
def test_normal_chinese_month_request_validates_confirmed_intent(indicator: str) -> None:
    proposal = proposal_from_message(f"请分析武汉东湖地区2023年7月与2024年7月的{indicator}变化")
    assert proposal.analysis_area == "武汉东湖"
    assert proposal.period_a is not None and proposal.period_b is not None
    validate_proposal(proposal)


def test_unknown_area_remains_rejected_by_confirmed_intent() -> None:
    proposal = proposal_from_message("请分析北京地区2023年7月与2024年7月的NDVI变化")
    with pytest.raises(ValueError):
        validate_proposal(proposal)


@pytest.mark.asyncio
async def test_live_interpretation_without_platform_key_is_explicitly_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(conversation_service.settings, "USE_FAKE_MODEL", False)
    monkeypatch.setattr(conversation_service.settings, "GEOCHANGE_LIVE_LLM", True)
    monkeypatch.setattr(conversation_service.settings, "DEEPSEEK_API_KEY", None)
    monkeypatch.setattr(conversation_service.settings, "DEFAULT_MODEL", "deepseek-v4-flash")
    with pytest.raises(LLMConversationError, match="AI 服务暂不可用"):
        await conversation_service.interpret_message("请分析武汉东湖的 NDVI 变化")


def test_llm_intent_normalizes_provider_slot_aliases() -> None:
    intent = LLMIntent.model_validate(
        {
            "intent": "new_analysis",
            "location": "武汉东湖",
            "index": "NDVI",
            "time_ranges": [
                {"start_date": "2023-07-01", "end_date": "2023-07-31"},
                {"start_date": "2024-07-01", "end_date": "2024-07-31"},
            ],
        }
    )
    assert intent.analysis_area == "武汉东湖"
    assert intent.indicator == "NDVI"
    assert intent.period_a is not None and intent.period_b is not None


def test_result_interpretation_normalizes_provider_aliases_without_fabricating_evidence() -> None:
    result = ResultInterpretation.model_validate(
        {
            "summary": "依据已验证指标生成解读。",
            "status": "passed",
            "constraints": "仅展示连续指数变化",
        }
    )
    assert result.text.startswith("依据")
    assert result.evidence_status == "verified"
    assert result.limitations == ["仅展示连续指数变化"]


def test_proposal_validation_rejects_reverse_period() -> None:
    with pytest.raises(ValueError, match="comparison periods"):
        validate_proposal(
            TaskProposal(
                title="变化",
                description="分析",
                analysis_area="武汉东湖",
                period_a={"start": "2025-01-01", "end": "2025-01-31"},
                period_b={"start": "2024-01-01", "end": "2024-01-31"},
            )
        )


def test_proposal_is_not_an_execution_command() -> None:
    proposal = proposal_from_message("分析东湖植被变化")
    assert not hasattr(proposal, "run")
    assert proposal.model_dump().get("required_parameters")


def test_request_and_proposal_share_the_2000_character_boundary() -> None:
    with pytest.raises(ValueError):
        ConversationRequest(message="x" * 2001)
    TaskProposal(title="分析", description="x" * 1990)


def test_result_query_normalization_keeps_meaningful_location() -> None:
    assert normalize_result_query("打开之前武汉东湖那个分析") == "武汉东湖"


@pytest.mark.asyncio
async def test_result_lookup_uses_normalized_user_scoped_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SimpleNamespace(
        id=uuid4(), title="武汉东湖 NDVI变化分析", description="武汉东湖植被变化"
    )
    run = SimpleNamespace(
        id=uuid4(),
        status=SimpleNamespace(value="succeeded"),
        result_metadata={"artifact_references": {"change": "change"}},
    )

    class FakeTasks:
        def __init__(self, session: object) -> None:
            self.session = session

        async def list_for_user_in_organization(self, user_id: object, organization_id: object):
            assert user_id == principal.user_id
            assert organization_id == principal.organization_id
            return [task]

    class FakeRuns:
        def __init__(self, session: object) -> None:
            self.session = session

        async def list_for_task_in_organization(self, task_id: object, organization_id: object):
            assert task_id == task.id
            assert organization_id == principal.organization_id
            return [run]

    principal = SimpleNamespace(user_id=uuid4(), organization_id=uuid4())
    monkeypatch.setattr(conversation_service, "TaskRepository", FakeTasks)
    monkeypatch.setattr(conversation_service, "TaskRunRepository", FakeRuns)

    result = await conversation_service.find_result(object(), principal, "打开之前武汉东湖那个分析")

    assert result is not None
    assert result["task_run_id"] == run.id
    assert result["artifact_names"] == ["change"]


@pytest.mark.asyncio
async def test_history_is_scoped_to_principal_user_and_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal = SimpleNamespace(user_id=uuid4(), organization_id=uuid4())
    own = SimpleNamespace(
        id=uuid4(),
        title="own",
        description="own",
        confirmed_intent={"analysis_type": "vegetation_change"},
        status=SimpleNamespace(value="draft"),
        created_at=datetime.now(UTC),
    )

    class FakeTasks:
        def __init__(self, session: object) -> None:
            pass

        async def list_for_user_in_organization(self, user_id: object, organization_id: object):
            assert (user_id, organization_id) == (principal.user_id, principal.organization_id)
            return [own]

    class FakeRuns:
        def __init__(self, session: object) -> None:
            pass

        async def list_for_task_in_organization(self, task_id: object, organization_id: object):
            assert task_id == own.id
            assert organization_id == principal.organization_id
            return []

    monkeypatch.setattr(conversation_service, "TaskRepository", FakeTasks)
    monkeypatch.setattr(conversation_service, "TaskRunRepository", FakeRuns)
    records = await conversation_service.history(object(), principal)
    assert [record.title for record in records] == ["own"]


def test_confirmed_proposal_is_consumed_by_offline_runtime_input() -> None:

    proposal = TaskProposal(
        title="武汉东湖植被变化",
        description="分析武汉东湖植被变化",
        analysis_area="武汉东湖",
        analysis_type="vegetation_change",
        indicator="NDVI",
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        required_parameters={"cloud_threshold": "12", "source": "Sentinel-2"},
    )
    intent = confirmed_intent_from_proposal(proposal)
    assert intent.period_a.start == date(2023, 7, 1)
    assert intent.period_b.start == date(2024, 7, 1)
    assert intent.runtime_task().period_b.start == date(2024, 7, 1)


def test_unsupported_confirmed_intent_fails_explicitly() -> None:
    with pytest.raises(ValueError):
        confirmed_intent_from_proposal(
            TaskProposal(
                title="unsupported",
                description="unsupported",
                analysis_area="武汉东湖",
                analysis_type="water_change",
                indicator="water_index",
                period_a={"start": "2023-07-01", "end": "2023-07-31"},
                period_b={"start": "2024-07-01", "end": "2024-07-31"},
            )
        )


@pytest.mark.asyncio
async def test_result_intent_has_priority_over_history(monkeypatch: pytest.MonkeyPatch) -> None:
    lookup = AsyncMock(return_value={"task_id": uuid4(), "task_run_id": uuid4()})
    history = AsyncMock(side_effect=AssertionError("history must not be queried"))
    monkeypatch.setattr(conversation_api, "find_result", lookup)
    monkeypatch.setattr(conversation_api, "history", history)

    response = await conversation_api.converse(
        ConversationRequest(message="打开之前武汉东湖那个分析"), object(), object()
    )

    assert response.kind == "result"
    lookup.assert_awaited_once()
    history.assert_not_awaited()


@pytest.mark.asyncio
async def test_proposal_endpoint_does_not_create_a_task() -> None:
    response = await conversation_api.converse(
        ConversationRequest(message="分析武汉东湖植被变化"), object(), object()
    )

    assert response.kind == "clarification"
    assert response.proposal is not None
    assert response.missing_fields == ["period_a", "period_b"]


@pytest.mark.asyncio
async def test_confirmation_missing_periods_returns_clarification() -> None:
    response = await conversation_api.confirm_conversation_task(
        ConfirmTaskRequest(proposal=proposal_from_message("分析武汉东湖植被变化")),
        object(),
        object(),
        Response(),
    )
    assert response.kind == "clarification"
    assert response.missing_fields == ["period_a", "period_b"]


@pytest.mark.asyncio
async def test_complete_confirmation_sets_created_status(monkeypatch: pytest.MonkeyPatch) -> None:
    proposal = proposal_from_message(
        "分析武汉东湖，时段A 2023-07-01至2023-07-31，时段B 2024-07-01至2024-07-31"
    )
    task = SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="draft"))
    monkeypatch.setattr(conversation_api, "create_confirmed_task", AsyncMock(return_value=task))
    response = Response()
    result = await conversation_api.confirm_conversation_task(
        ConfirmTaskRequest(proposal=proposal), object(), object(), response
    )
    assert response.status_code == 201
    assert result.kind == "task_created"


@pytest.mark.asyncio
async def test_confirmation_reports_existing_analysis_instead_of_invalid_proposal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proposal = proposal_from_message(
        "分析武汉东湖，时段A 2023-07-01至2023-07-31，时段B 2024-07-01至2024-07-31"
    )
    task = SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="succeeded"))
    monkeypatch.setattr(
        conversation_api,
        "create_confirmed_task",
        AsyncMock(side_effect=ExistingAnalysisError(task)),
    )
    response = await conversation_api.confirm_conversation_task(
        ConfirmTaskRequest(proposal=proposal), object(), object(), Response()
    )
    assert response.kind == "existing_analysis"
    assert response.result == {"task_id": task.id, "status": "succeeded"}


@pytest.mark.asyncio
async def test_confirmation_persists_structured_proposal_without_starting_a_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = type("Task", (), {"status": type("Status", (), {"value": "draft"})()})()

    class FakeTaskService:
        def __init__(self, session: object) -> None:
            self.session = session

        async def create_task(self, principal: object, **kwargs: object) -> object:
            assert kwargs["confirmed_intent"]["analysis_type"] == "vegetation_change"
            return created

    monkeypatch.setattr(conversation_service, "TaskService", FakeTaskService)
    proposal = TaskProposal(
        title="武汉东湖植被变化",
        description="分析武汉东湖植被变化",
        analysis_type="vegetation_change",
        indicator="NDVI",
        analysis_area="武汉东湖",
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )

    result = await create_confirmed_task(object(), object(), proposal)

    assert result is created


@pytest.mark.asyncio
async def test_task_creation_reuses_matching_draft_for_replayed_proposal() -> None:
    existing = SimpleNamespace(status=SimpleNamespace(value="draft"))
    intent = {
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "analysis_area": "武汉东湖",
        "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
    }

    class DraftRepository:
        async def lock_confirmation_scope(self, membership_id, user_id, organization_id):
            return object()

        async def find_for_user_with_intent(self, user_id, organization_id, intent):
            assert intent["indicator"] == "NDVI"
            return existing

    service = TaskService.__new__(TaskService)
    service.tasks = DraftRepository()
    service.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    principal = SimpleNamespace(user_id=uuid4(), membership_id=uuid4(), organization_id=uuid4())

    result = await service.create_task(
        principal,
        title="武汉东湖 NDVI",
        confirmed_intent=intent,
    )

    assert result is existing
    service.session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_creation_rejects_replayed_started_proposal() -> None:
    existing = SimpleNamespace(id=uuid4(), status=SimpleNamespace(value="running"))
    intent = {
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "analysis_area": "武汉东湖",
        "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
    }

    class ExistingRepository:
        async def lock_confirmation_scope(self, membership_id, user_id, organization_id):
            return object()

        async def find_for_user_with_intent(self, user_id, organization_id, intent):
            return existing

    service = TaskService.__new__(TaskService)
    service.tasks = ExistingRepository()
    service.session = SimpleNamespace(rollback=AsyncMock())
    principal = SimpleNamespace(user_id=uuid4(), membership_id=uuid4(), organization_id=uuid4())

    with pytest.raises(ValueError, match="already been confirmed"):
        await service.create_task(
            principal,
            title="武汉东湖 NDVI",
            confirmed_intent=intent,
        )


@pytest.mark.asyncio
async def test_task_creation_rejects_proposal_id_reused_with_different_intent() -> None:
    intent = {
        "analysis_type": "vegetation_change",
        "indicator": "NDVI",
        "analysis_area": "武汉东湖",
        "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
    }
    changed_intent = {**intent, "period_b": {"start": "2025-07-01", "end": "2025-07-31"}}
    existing = SimpleNamespace(
        status=SimpleNamespace(value="draft"),
        confirmed_intent=ConfirmedIntent.model_validate(intent).model_dump(mode="json"),
    )

    class ExistingRepository:
        async def lock_confirmation_scope(self, membership_id, user_id, organization_id):
            return object()

        async def find_for_user_with_proposal_id(self, user_id, organization_id, proposal_id):
            return existing

    service = TaskService.__new__(TaskService)
    service.tasks = ExistingRepository()
    service.session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    principal = SimpleNamespace(user_id=uuid4(), membership_id=uuid4(), organization_id=uuid4())

    with pytest.raises(ProposalIdentityMismatchError):
        await service.create_task(
            principal,
            title="武汉东湖 NDVI",
            confirmed_intent=changed_intent,
            proposal_id=uuid4(),
        )
    service.session.commit.assert_not_awaited()
    service.session.rollback.assert_awaited_once()
