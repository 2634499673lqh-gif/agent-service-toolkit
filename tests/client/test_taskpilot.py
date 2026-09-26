from unittest.mock import patch
from uuid import uuid4

import httpx
import pytest

from client.taskpilot import TaskPilotClient, TaskPilotClientError


def response(status: int, payload=None):
    return httpx.Response(
        status,
        json=payload if payload is not None else {},
        request=httpx.Request("POST", "http://test/api"),
    )


def test_login_selection_then_fresh_login_and_session():
    organization_id = uuid4()
    client = TaskPilotClient("http://test")
    with patch(
        "httpx.request",
        side_effect=[
            response(
                409,
                {
                    "code": "ORGANIZATION_SELECTION_REQUIRED",
                    "organization_ids": [str(organization_id)],
                },
            ),
            response(
                200,
                {
                    "access_token": "secret",
                    "token_type": "bearer",
                    "expires_at": "2026-01-01T00:00:00Z",
                },
            ),
            response(
                200,
                {
                    "user_id": str(uuid4()),
                    "membership_id": str(uuid4()),
                    "organization_id": str(organization_id),
                    "role": "member",
                },
            ),
        ],
    ) as request:
        result = client.login("person@example.com", "password")
        assert result.requires_organization
        result = client.login("person@example.com", "password", organization_id)
        assert result.token == "secret"
        assert client.session()["organization_id"] == str(organization_id)
        assert request.call_args_list[2].kwargs["headers"]["Authorization"] == "Bearer secret"


def test_timeout_is_classified_without_details():
    client = TaskPilotClient("http://test")
    with patch("httpx.request", side_effect=httpx.TimeoutException("private details")):
        with pytest.raises(TaskPilotClientError, match="timed out") as error:
            client.list_tasks()
    assert error.value.kind == "timeout"
    assert "private details" not in str(error.value)


def test_401_clears_token_and_logout_always_clears():
    client = TaskPilotClient("http://test", token="secret")
    with patch("httpx.request", return_value=response(401)):
        with pytest.raises(TaskPilotClientError) as error:
            client.list_tasks()
    assert error.value.kind == "unauthorized"
    assert client.token is None


def test_create_sends_only_allowed_fields_and_does_not_retry():
    client = TaskPilotClient("http://test", token="secret")
    task = {"id": str(uuid4()), "title": "same", "description": "details"}
    with patch("httpx.request", return_value=response(201, task)) as request:
        assert client.create_task("same", "details") == task
    request.assert_called_once()
    assert request.call_args.kwargs["json"] == {"title": "same", "description": "details"}
    assert "organization_id" not in request.call_args.kwargs["json"]
    assert "status" not in request.call_args.kwargs["json"]


def test_lost_create_response_is_safe_and_reads_are_explicit():
    client = TaskPilotClient("http://test", token="secret")
    with patch("httpx.request", side_effect=httpx.TimeoutException("lost")) as request:
        with pytest.raises(TaskPilotClientError, match="timed out"):
            client.create_task("duplicate")
    request.assert_called_once()
    with patch("httpx.request", return_value=response(200, [])) as request:
        assert client.list_tasks() == []
    request.assert_called_once()


def test_missing_task_is_bounded_and_contains_no_response_body():
    client = TaskPilotClient("http://test", token="secret")
    missing = response(404, {"secret": "do-not-render"})
    with patch("httpx.request", return_value=missing):
        with pytest.raises(TaskPilotClientError, match="not found") as error:
            client.get_task(uuid4())
    assert "do-not-render" not in str(error.value)

    client.token = "secret"
    with patch("httpx.request", side_effect=TaskPilotClientError("server")):
        with pytest.raises(TaskPilotClientError):
            client.logout()
    assert client.token is None


def test_run_approval_and_trace_routes_preserve_wire_order_and_payload_bounds():
    task_id, run_id, approval_id = uuid4(), uuid4(), uuid4()
    run = {
        "id": str(run_id),
        "task_id": str(task_id),
        "run_number": 1,
        "status": "pending",
        "created_at": "2026-09-26T10:00:00Z",
        "updated_at": "2026-09-26T10:00:00Z",
    }
    approval = {
        "id": str(approval_id),
        "task_run_id": str(run_id),
        "status": "pending",
        "proposed_action": {"name": "safe-action"},
    }
    trace = [{"event_id": str(uuid4()), "event_kind": "agent_run"}]
    client = TaskPilotClient("http://test", token="secret")
    with patch(
        "httpx.request",
        side_effect=[
            response(200, [run]),
            response(200, run),
            response(201, run),
            response(200, [approval]),
            response(200, approval),
            response(200, {**approval, "status": "approved"}),
            response(200, trace),
        ],
    ) as request:
        assert client.list_task_runs(task_id) == [run]
        assert client.get_task_run(task_id, run_id) == run
        assert client.start_task_run(task_id) == run
        assert client.list_approvals(task_id, run_id) == [approval]
        assert client.get_approval(task_id, run_id, approval_id) == approval
        assert client.decide_approval(task_id, run_id, approval_id, "approve", "ok")["status"] == (
            "approved"
        )
        assert client.get_trace(task_id, run_id, limit=500) == trace

    paths = [call.args[1].removeprefix("http://test") for call in request.call_args_list]
    assert paths == [
        f"/api/v1/tasks/{task_id}/runs",
        f"/api/v1/tasks/{task_id}/runs/{run_id}",
        f"/api/v1/tasks/{task_id}/runs",
        f"/api/v1/tasks/{task_id}/runs/{run_id}/approvals",
        f"/api/v1/tasks/{task_id}/runs/{run_id}/approvals/{approval_id}",
        f"/api/v1/tasks/{task_id}/runs/{run_id}/approvals/{approval_id}/approve",
        f"/api/v1/tasks/{task_id}/runs/{run_id}/trace",
    ]
    assert request.call_args_list[5].kwargs["json"] == {"reason": "ok"}
    assert request.call_args_list[6].kwargs["params"] == {"limit": 500}


@pytest.mark.parametrize(
    ("status", "kind", "message"),
    [
        (403, "forbidden", "permission"),
        (409, "conflict", "resource changed"),
        (422, "validation", "invalid"),
        (503, "service_unavailable", "unavailable"),
    ],
)
def test_run_client_classifies_safe_http_errors(status, kind, message):
    client = TaskPilotClient("http://test", token="secret")
    with patch("httpx.request", return_value=response(status, {"detail": "private backend"})):
        with pytest.raises(TaskPilotClientError, match=message) as error:
            client.list_task_runs(uuid4())
    assert error.value.kind == kind
    assert "private backend" not in str(error.value)


def test_trace_limit_is_frozen_and_malformed_payload_is_rejected():
    client = TaskPilotClient("http://test", token="secret")
    for invalid in (0, 501, True):
        with pytest.raises(ValueError, match="between 1 and 500"):
            client.get_trace(uuid4(), uuid4(), limit=invalid)
    with patch("httpx.request", return_value=response(200, {"detail": "not a trace list"})):
        with pytest.raises(TaskPilotClientError, match="invalid response") as error:
            client.get_trace(uuid4(), uuid4(), limit=100)
    assert "not a trace list" not in str(error.value)
