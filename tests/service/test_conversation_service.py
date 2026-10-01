from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.responses import Response

from schema.conversation_api import ConfirmTaskRequest, ConversationRequest, TaskProposal
from service import conversation_api, conversation_service
from service.conversation_service import (
    confirmed_intent_from_proposal,
    create_confirmed_task,
    missing_proposal_fields,
    normalize_result_query,
    proposal_from_message,
    validate_proposal,
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
