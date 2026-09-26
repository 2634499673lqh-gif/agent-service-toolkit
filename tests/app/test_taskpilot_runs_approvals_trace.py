"""Product AppTest evidence for the Phase 9 run, approval and trace views."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch
from uuid import uuid4

import httpx
from streamlit.testing.v1 import AppTest


def _response(method: str, url: str, data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data, request=httpx.Request(method, url))


def _fixture(role: str = "member", task_status: str = "queued") -> dict[str, object]:
    task_id, first_run, second_run, approval_id = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
    task = {
        "id": task_id,
        "title": "Evidence task",
        "description": "inspect persisted state",
        "status": task_status,
        "created_at": "2026-09-26T10:00:00Z",
        "updated_at": "2026-09-26T11:00:00Z",
    }
    runs = [
        {
            "id": first_run,
            "task_id": task_id,
            "run_number": 1,
            "status": "succeeded",
            "created_at": "2026-09-26T10:01:00Z",
            "updated_at": "2026-09-26T10:02:00Z",
        },
        {
            "id": second_run,
            "task_id": task_id,
            "run_number": 2,
            "status": "pending",
            "created_at": "2026-09-26T10:03:00Z",
            "updated_at": "2026-09-26T10:03:00Z",
        },
    ]
    approval = {
        "id": approval_id,
        "task_run_id": first_run,
        "replan_count": 0,
        "step_position": 2,
        "action_name": "safe-action",
        "action_version": "v1",
        "proposed_action": {"operation": "inspect", "authorization": "[REDACTED]"},
        "risk_level": "L2",
        "status": "pending",
        "requester_membership_id": str(uuid4()),
        "decider_membership_id": None,
        "decided_at": None,
        "decision_reason": None,
        "created_at": "2026-09-26T10:01:00Z",
        "updated_at": "2026-09-26T10:01:00Z",
    }
    trace = [
        {
            "event_id": str(uuid4()),
            "event_kind": "agent_run",
            "task_id": task_id,
            "task_run_id": first_run,
            "request_id": None,
            "replan_count": 0,
            "step_position": 0,
            "retry_count": 0,
            "approval_id": None,
            "agent_run_id": str(uuid4()),
            "agent_name": "planner",
            "tool_call_id": None,
            "call_index": None,
            "tool_name": None,
            "tool_version": None,
            "status": "succeeded",
            "started_at": "2026-09-26T10:01:01Z",
            "finished_at": None,
            "duration_ms": None,
            "error_class": None,
            "error_code": None,
            "error_message": None,
            "usage": {"status": "unavailable", "reason": "not_returned"},
            "estimate": {"status": "unknown", "reason": "usage_unavailable"},
            "metadata": {"response_id": "Bearer [REDACTED]", "excluded": "never display"},
        },
        {
            "event_id": str(uuid4()),
            "event_kind": "tool_call",
            "task_id": task_id,
            "task_run_id": first_run,
            "request_id": None,
            "replan_count": 0,
            "step_position": 1,
            "retry_count": 0,
            "approval_id": approval_id,
            "agent_run_id": str(uuid4()),
            "agent_name": "executor",
            "tool_call_id": str(uuid4()),
            "call_index": 0,
            "tool_name": "safe_tool",
            "tool_version": "1",
            "status": "succeeded",
            "started_at": "2026-09-26T10:01:02Z",
            "finished_at": "2026-09-26T10:01:03Z",
            "duration_ms": 1000,
            "error_class": None,
            "error_code": None,
            "error_message": "Bearer backend-secret",
            "usage": {"status": "known", "input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            "estimate": {"status": "unknown", "reason": "unsupported_model"},
            "metadata": None,
        },
    ]
    return {
        "task": task,
        "task_id": task_id,
        "runs": runs,
        "approval": approval,
        "trace": trace,
        "role": role,
        "decisions": [],
    }


def _request_handler(fixture: dict[str, object]) -> Callable[..., httpx.Response]:
    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        task = fixture["task"]
        task_id = str(fixture["task_id"])
        runs = fixture["runs"]
        approval = fixture["approval"]
        selected_run_id = runs[0]["id"]
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "ui-token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user-a",
                    "membership_id": "member-a",
                    "organization_id": "org-a",
                    "role": fixture["role"],
                },
            )
        if url.endswith("/tasks") and method == "GET":
            return _response(method, url, [task])
        if url.endswith(f"/tasks/{task_id}"):
            return _response(method, url, task)
        if url.endswith(f"/tasks/{task_id}/runs") and method == "GET":
            return _response(method, url, runs)
        if url.endswith(f"/tasks/{task_id}/runs/{selected_run_id}/approvals"):
            return _response(method, url, [approval])
        if url.endswith(f"/tasks/{task_id}/runs/{selected_run_id}/approvals/{approval['id']}"):
            return _response(method, url, approval)
        if url.endswith(
            f"/tasks/{task_id}/runs/{selected_run_id}/approvals/{approval['id']}/approve"
        ):
            fixture["decisions"].append(kwargs.get("json"))
            approval["status"] = "approved"
            return _response(method, url, approval)
        if url.endswith(f"/tasks/{task_id}/runs/{selected_run_id}/trace"):
            return _response(method, url, fixture["trace"])
        for run in runs:
            if url.endswith(f"/runs/{run['id']}"):
                return _response(method, url, run)
        return _response(method, url, {"detail": "private backend detail"}, 404)

    return request


def _login(at: AppTest, role: str = "member") -> AppTest:
    at.text_input(key="taskpilot_email").set_value(f"{role}@example.com")
    at.text_input(key="taskpilot_login_password_0").set_value("password")
    at.button(key="FormSubmitter:taskpilot_login-Sign in").click().run()
    return at


def test_run_history_preserves_server_order_and_trace_is_observational():
    fixture = _fixture()
    with patch("httpx.request", side_effect=_request_handler(fixture)):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
    run_selector = at.selectbox(key="taskpilot_selected_run_id")
    assert [label.split(" · ", 1)[0] for label in run_selector.options] == ["Run 1", "Run 2"]
    assert at.selectbox(key="taskpilot_selected_approval_id").options
    assert [event["event_id"] for event in at.session_state.taskpilot_trace] == [
        event["event_id"] for event in fixture["trace"]
    ]
    assert not any("backend-secret" in item.value for item in at.markdown)
    assert not any("excluded" in item.value for item in at.markdown)
    assert any("Unavailable" in item.value for item in at.text)


def test_create_submit_is_disabled_while_mutation_is_in_flight():
    fixture = _fixture()
    post_calls = 0
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        nonlocal post_calls
        if method == "POST" and url.endswith("/tasks"):
            post_calls += 1
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.session_state.taskpilot_mutation_in_flight = True
        at.run()
        submit = at.button(key="FormSubmitter:taskpilot_create-Create task")
        assert submit.disabled
    assert post_calls == 0


def test_draft_start_is_explicit_and_reconciles_without_replaying_post():
    fixture = _fixture(task_status="draft")
    calls: list[tuple[str, str]] = []
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append((method, url))
        if url.endswith(f"/tasks/{fixture['task_id']}/runs") and method == "POST":
            new_run = {
                "id": str(uuid4()),
                "task_id": fixture["task_id"],
                "run_number": 3,
                "status": "pending",
                "created_at": "2026-09-26T10:04:00Z",
                "updated_at": "2026-09-26T10:04:00Z",
            }
            fixture["runs"].append(new_run)
            return _response(method, url, new_run, 201)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.button(key="taskpilot_start_run").click().run()
    assert [method for method, url in calls if method == "POST" and url.endswith("/runs")] == [
        "POST"
    ]
    assert any("public runtime is not executed" in item.value for item in at.caption)


def test_start_timeout_reconciles_state_and_never_replays_post():
    fixture = _fixture(task_status="draft")
    calls: list[tuple[str, str]] = []
    handler = _request_handler(fixture)
    post_count = 0

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        nonlocal post_count
        calls.append((method, url))
        if url.endswith(f"/tasks/{fixture['task_id']}/runs") and method == "POST":
            post_count += 1
            fixture["task"]["status"] = "queued"
            fixture["runs"].append(
                {
                    "id": str(uuid4()),
                    "task_id": fixture["task_id"],
                    "run_number": 3,
                    "status": "pending",
                    "created_at": "2026-09-26T10:04:00Z",
                    "updated_at": "2026-09-26T10:04:00Z",
                }
            )
            raise httpx.TimeoutException("private lost response")
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.button(key="taskpilot_start_run").click().run()
    assert post_count == 1
    assert any("outcome is unknown" in item.value for item in at.warning)
    assert any(
        method == "GET" and url.endswith(f"/tasks/{fixture['task_id']}/runs")
        for method, url in calls
    )
    assert not any("TaskStep" in item.value or "Progress" in item.value for item in at.markdown)


def test_start_409_reconciles_changed_task_and_does_not_replay_post():
    fixture = _fixture(task_status="draft")
    calls: list[tuple[str, str]] = []
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append((method, url))
        if url.endswith(f"/tasks/{fixture['task_id']}/runs") and method == "POST":
            fixture["task"]["status"] = "queued"
            return _response(method, url, {"detail": "private conflict"}, 409)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.button(key="taskpilot_start_run").click().run()
    assert len([1 for method, url in calls if method == "POST" and url.endswith("/runs")]) == 1
    assert any("resource changed" in item.value for item in at.error)
    assert not any("private conflict" in item.value for item in at.error)
    assert at.session_state.taskpilot_task["status"] == "queued"


def test_member_can_read_selected_approval_but_cannot_decide():
    fixture = _fixture(role="member")
    with patch("httpx.request", side_effect=_request_handler(fixture)):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
        at.selectbox(key="taskpilot_selected_approval_id").set_value(
            fixture["approval"]["id"]
        ).run()
    assert any("Members can inspect approvals" in item.value for item in at.info)
    assert "taskpilot_approve" not in at.button
    assert "taskpilot_reject" not in at.button


def _select_approval(at: AppTest, fixture: dict[str, object]) -> None:
    at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
    at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
    at.selectbox(key="taskpilot_selected_approval_id").set_value(fixture["approval"]["id"]).run()


def test_approval_401_clears_product_session_without_backend_detail():
    fixture = _fixture(role="owner")
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith(f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals"):
            return _response(method, url, {"detail": "private approval auth"}, 401)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
    assert "taskpilot_client" not in at.session_state
    assert not any("private approval auth" in item.value for item in at.error)
    assert any("session has expired" in item.value for item in at.error)


def test_approval_403_is_bounded_and_does_not_leak_backend_detail():
    fixture = _fixture(role="owner")
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        if url.endswith(f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals"):
            return _response(method, url, {"detail": "private approval permission"}, 403)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
    assert any("permission" in item.value for item in at.error)
    assert not any("private approval permission" in item.value for item in at.error)


def test_approval_409_refreshes_detail_and_list_without_replaying_decision():
    fixture = _fixture(role="owner")
    handler = _request_handler(fixture)
    post_calls = 0
    approval_gets = 0

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        nonlocal post_calls, approval_gets
        if url.endswith(
            f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals/{fixture['approval']['id']}/approve"
        ):
            post_calls += 1
            fixture["approval"]["status"] = "approved"
            return _response(method, url, {"detail": "private approval conflict"}, 409)
        if url.endswith(
            f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals/{fixture['approval']['id']}"
        ):
            approval_gets += 1
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        _select_approval(at, fixture)
        at.button(key="taskpilot_approve").click().run()
    assert post_calls == 1
    assert approval_gets >= 2
    assert at.session_state.taskpilot_approval["status"] == "approved"
    assert any("approval changed" in item.value for item in at.warning)
    assert not any("private approval conflict" in item.value for item in at.error)


def test_approval_timeout_refreshes_unknown_outcome_by_fresh_reads():
    fixture = _fixture(role="owner")
    handler = _request_handler(fixture)
    post_calls = 0
    fresh_approval_gets = 0

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        nonlocal post_calls, fresh_approval_gets
        if url.endswith(
            f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals/{fixture['approval']['id']}/approve"
        ):
            post_calls += 1
            fixture["approval"]["status"] = "approved"
            raise httpx.TimeoutException("private approval timeout")
        if url.endswith(
            f"/tasks/{fixture['task_id']}/runs/{fixture['runs'][0]['id']}/approvals/{fixture['approval']['id']}"
        ):
            fresh_approval_gets += 1
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        _select_approval(at, fixture)
        at.button(key="taskpilot_approve").click().run()
    assert post_calls == 1
    assert fresh_approval_gets >= 2
    assert at.session_state.taskpilot_approval["status"] == "approved"
    assert any("outcome is unknown" in item.value for item in at.warning)
    assert not any("private approval timeout" in item.value for item in at.error)


def test_owner_decision_sends_only_reason_and_refreshes_sanitized_state():
    fixture = _fixture(role="owner")
    with patch("httpx.request", side_effect=_request_handler(fixture)):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        at.selectbox(key="taskpilot_selected_task_id").set_value(fixture["task_id"]).run()
        at.selectbox(key="taskpilot_selected_run_id").set_value(fixture["runs"][0]["id"]).run()
        at.selectbox(key="taskpilot_selected_approval_id").set_value(
            fixture["approval"]["id"]
        ).run()
        at.text_area(key=f"taskpilot_approval_reason_{fixture['approval']['id']}").set_value(
            "reviewed"
        )
        at.button(key="taskpilot_approve").click().run()
    assert fixture["decisions"] == [{"reason": "reviewed"}]
    assert any("does not execute or resume" in item.value for item in at.success)


def test_401_clears_selected_run_approval_and_trace_state():
    fixture = _fixture(role="owner")
    session_calls = 0
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        nonlocal session_calls
        if url.endswith("/auth/session"):
            session_calls += 1
            if session_calls > 2:
                return _response(method, url, {"detail": "private auth detail"}, 401)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run(), role="owner")
        for key, value in {
            "taskpilot_run": {"status": "pending"},
            "taskpilot_approvals": [{"id": fixture["approval"]["id"]}],
            "taskpilot_approval": fixture["approval"],
            "taskpilot_trace": fixture["trace"],
            "taskpilot_selected_run_id": fixture["runs"][0]["id"],
            "taskpilot_selected_approval_id": fixture["approval"]["id"],
        }.items():
            at.session_state[key] = value
        at.button(key="taskpilot_refresh").click().run()
    assert "taskpilot_client" not in at.session_state
    assert not any(
        key in at.session_state
        for key in ("taskpilot_run", "taskpilot_approval", "taskpilot_trace")
    )
    assert not any("private auth detail" in item.value for item in at.error)


def test_product_422_create_is_safe_and_does_not_render_backend_detail():
    fixture = _fixture()
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        if method == "POST" and url.endswith("/tasks"):
            return _response(method, url, {"detail": "private validation detail"}, 422)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.text_input(key="taskpilot_new_title").set_value("Invalid at server")
        at.button(key="FormSubmitter:taskpilot_create-Create task").click().run()
    assert any("submitted values are invalid" in item.value for item in at.error)
    assert not any("private validation detail" in item.value for item in at.error)


def test_product_5xx_task_read_is_safe_and_does_not_render_backend_detail():
    fixture = _fixture()
    handler = _request_handler(fixture)

    def request(method: str, url: str, **kwargs: object) -> httpx.Response:
        if method == "GET" and url.endswith("/tasks"):
            return _response(method, url, {"detail": "private service stack"}, 503)
        return handler(method, url, **kwargs)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
    assert any("service is unavailable" in item.value for item in at.error)
    assert not any("private service stack" in item.value for item in at.error)
