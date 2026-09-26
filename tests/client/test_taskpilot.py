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
