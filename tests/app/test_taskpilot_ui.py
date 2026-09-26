"""Product AppTest evidence with a deterministic HTTP boundary."""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

import httpx
from streamlit.testing.v1 import AppTest


def _task(task_id: str, title: str = "Task") -> dict[str, str]:
    return {
        "id": task_id,
        "title": title,
        "description": "Description",
        "status": "DRAFT",
        "created_at": "2026-09-26T10:00:00Z",
        "updated_at": "2026-09-26T11:00:00Z",
    }


def _response(method: str, url: str, data: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=data, request=httpx.Request(method, url))


def _login(at: AppTest, email: str = "person@example.com") -> AppTest:
    at.text_input(key="taskpilot_email").set_value(email)
    at.text_input(key="taskpilot_login_password_0").set_value("password")
    at.button(key="FormSubmitter:taskpilot_login-Sign in").click().run()
    return at


def test_product_is_default_without_legacy_startup():
    with patch("client.AgentClient") as legacy:
        at = AppTest.from_file("../../src/streamlit_app.py").run()
    assert at.sidebar.radio(key="app_view").value == "TaskPilot Product"
    assert at.subheader[0].value == "Sign in to TaskPilot"
    assert "user_id" not in at.session_state
    assert "agent_client" not in at.session_state
    legacy.assert_not_called()
    assert not at.exception


def test_login_and_empty_tasks():
    organization_id = str(uuid4())
    user_id = str(uuid4())
    membership_id = str(uuid4())
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith("/auth/login"):
            data = {
                "access_token": "test-token",
                "token_type": "bearer",
                "expires_at": "2026-10-01T00:00:00Z",
            }
        elif url.endswith("/auth/session"):
            data = {
                "user_id": user_id,
                "membership_id": membership_id,
                "organization_id": organization_id,
                "role": "member",
            }
        else:
            data = []
        return httpx.Response(200, json=data, request=httpx.Request(method, url))

    with patch("httpx.request", side_effect=request):
        at = AppTest.from_file("../../src/streamlit_app.py").run()
        at.text_input(key="taskpilot_email").set_value("person@example.com")
        at.text_input(key="taskpilot_login_password_0").set_value("password")
        at.button(key="FormSubmitter:taskpilot_login-Sign in").click().run()
    assert not at.exception
    assert at.session_state.taskpilot_client.token == "test-token"
    assert any("No tasks yet" in item.value for item in at.info)
    assert "taskpilot_password" not in at.session_state
    assert any(url.endswith("/auth/session") for _, url, _ in calls)


def test_organization_selection_requires_fresh_password_attempt():
    organization_id = str(uuid4())
    user_id = str(uuid4())
    membership_id = str(uuid4())
    calls = 0

    def request(method, url, **kwargs):
        nonlocal calls
        calls += 1
        if url.endswith("/auth/login") and calls == 1:
            return httpx.Response(
                409,
                json={
                    "code": "ORGANIZATION_SELECTION_REQUIRED",
                    "organization_ids": [organization_id],
                },
                request=httpx.Request(method, url),
            )
        if url.endswith("/auth/login"):
            return httpx.Response(
                200,
                json={
                    "access_token": "token",
                    "token_type": "bearer",
                    "expires_at": "2026-10-01T00:00:00Z",
                },
                request=httpx.Request(method, url),
            )
        if url.endswith("/auth/session"):
            return httpx.Response(
                200,
                json={
                    "user_id": user_id,
                    "membership_id": membership_id,
                    "organization_id": organization_id,
                    "role": "member",
                },
                request=httpx.Request(method, url),
            )
        return httpx.Response(200, json=[], request=httpx.Request(method, url))

    with patch("httpx.request", side_effect=request):
        at = AppTest.from_file("../../src/streamlit_app.py").run()
        at.text_input(key="taskpilot_email").set_value("person@example.com")
        at.text_input(key="taskpilot_login_password_0").set_value("first")
        at.button(key="FormSubmitter:taskpilot_login-Sign in").click().run()
        assert at.text_input(key="taskpilot_org_login_password_0").value == ""
        at.selectbox(key="taskpilot_org_choice").set_value(organization_id)
        at.text_input(key="taskpilot_email_selection").set_value("person@example.com")
        at.text_input(key="taskpilot_org_login_password_0").set_value("second")
        at.button(
            key="FormSubmitter:taskpilot_org_login-Sign in to selected organization"
        ).click().run()
    assert at.session_state.taskpilot_client.token == "token"
    assert at.session_state.taskpilot_client.token == "token"


def test_product_create_reconciles_and_does_not_replay_on_refresh():
    task_id = str(uuid4())
    created = _task(task_id, "Created task")
    calls: list[tuple[str, str, dict]] = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
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
                    "role": "member",
                },
            )
        if url.endswith("/tasks") and method == "POST":
            return _response(method, url, created, 201)
        if url.endswith("/tasks"):
            return _response(method, url, [created])
        return _response(method, url, created)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.text_input(key="taskpilot_new_title").set_value("Created task")
        at.text_area(key="taskpilot_new_description").set_value("Description")
        at.button(key="FormSubmitter:taskpilot_create-Create task").click().run()
        at.button(key="taskpilot_refresh").click().run()
    post_calls = [call for call in calls if call[0] == "POST" and call[1].endswith("/tasks")]
    assert len(post_calls) == 1
    assert post_calls[0][2]["json"] == {"title": "Created task", "description": "Description"}
    assert not any("ui-token" in element.value for element in at.markdown)


def test_product_lost_create_shows_unknown_and_reads_list():
    calls: list[tuple[str, str]] = []

    def request(method, url, **kwargs):
        calls.append((method, url))
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user-a",
                    "membership_id": "member-a",
                    "organization_id": "org-a",
                    "role": "member",
                },
            )
        if url.endswith("/tasks") and method == "POST":
            raise httpx.TimeoutException("private transport detail")
        return _response(method, url, [])

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.text_input(key="taskpilot_new_title").set_value("Duplicate title")
        at.button(key="FormSubmitter:taskpilot_create-Create task").click().run()
    assert any("outcome is unknown" in warning.value for warning in at.warning)
    assert not any("private transport detail" in item.value for item in at.error)
    assert any(url.endswith("/tasks") and method == "GET" for method, url in calls)


def test_product_401_clears_all_protected_state():
    session_calls = 0

    def request(method, url, **kwargs):
        nonlocal session_calls
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            session_calls += 1
            if session_calls > 2:
                return _response(method, url, {"detail": "raw secret"}, 401)
            return _response(
                method,
                url,
                {
                    "user_id": "user-a",
                    "membership_id": "member-a",
                    "organization_id": "org-a",
                    "role": "member",
                },
            )
        return _response(method, url, [])

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.session_state["taskpilot_tasks"] = [_task(str(uuid4()))]
        at.session_state["taskpilot_selected_task_id"] = "task"
        at.button(key="taskpilot_refresh").click().run()
    assert "taskpilot_client" not in at.session_state
    assert "taskpilot_identity" not in at.session_state
    assert "taskpilot_tasks" not in at.session_state
    assert "taskpilot_selected_task_id" not in at.session_state
    assert any("session has expired" in item.value for item in at.error)
    assert not any("raw secret" in item.value for item in at.error)


def test_product_logout_clears_state_when_remote_revoke_times_out():
    def request(method, url, **kwargs):
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user-a",
                    "membership_id": "member-a",
                    "organization_id": "org-a",
                    "role": "member",
                },
            )
        if url.endswith("/auth/logout"):
            raise httpx.TimeoutException("raw revoke detail")
        return _response(method, url, [])

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.session_state["taskpilot_tasks"] = [_task(str(uuid4()))]
        at.sidebar.button(key="taskpilot_logout").click().run()
    assert at.session_state.taskpilot_client.token is None
    assert "taskpilot_identity" not in at.session_state
    assert "taskpilot_tasks" not in at.session_state
    assert any("Local session cleared" in item.value for item in at.warning)
    assert not any("raw revoke detail" in item.value for item in at.warning)


def test_product_account_change_clears_prior_state():
    session_calls = 0

    def request(method, url, **kwargs):
        nonlocal session_calls
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            session_calls += 1
            user = "user-a" if session_calls <= 2 else "user-b"
            return _response(
                method,
                url,
                {
                    "user_id": user,
                    "membership_id": "member",
                    "organization_id": "org",
                    "role": "member",
                },
            )
        return _response(method, url, [_task(str(uuid4()))])

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.session_state["taskpilot_tasks"] = [_task(str(uuid4()))]
        at.button(key="taskpilot_refresh").click().run()
    assert "taskpilot_identity" not in at.session_state
    assert "taskpilot_tasks" not in at.session_state
    assert any("changed" in item.value for item in at.info)


def test_product_list_detail_status_refresh_and_selection_clear():
    first_id, second_id = str(uuid4()), str(uuid4())
    tasks = [_task(first_id, "First"), _task(second_id, "Second")]
    detail_reads = 0
    list_reads = 0

    def request(method, url, **kwargs):
        nonlocal detail_reads, list_reads
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user",
                    "membership_id": "member",
                    "organization_id": "org",
                    "role": "member",
                },
            )
        if url.endswith("/tasks"):
            list_reads += 1
            return _response(method, url, tasks)
        detail_reads += 1
        return _response(method, url, next(task for task in tasks if url.endswith(task["id"])))

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(first_id).run()
        assert detail_reads == 1
        assert any(
            "Status: DRAFT" in item.value and "Updated: 2026-09-26T11:00:00Z" in item.value
            for item in at.caption
        )
        at.session_state["taskpilot_run"] = {"status": "PENDING"}
        at.selectbox(key="taskpilot_selected_task_id").set_value(second_id).run()
        assert "taskpilot_run" not in at.session_state
        before = list_reads
        at.button(key="taskpilot_refresh").click().run()
    assert list_reads > before
    assert detail_reads >= 2


def test_product_missing_task_is_bounded_unavailable_resource():
    task_id = str(uuid4())

    def request(method, url, **kwargs):
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user",
                    "membership_id": "member",
                    "organization_id": "org",
                    "role": "member",
                },
            )
        if url.endswith("/tasks"):
            return _response(method, url, [_task(task_id)])
        return _response(method, url, {"detail": "foreign secret"}, 404)

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
        at.selectbox(key="taskpilot_selected_task_id").set_value(task_id).run()
    assert any("not found" in item.value for item in at.error)
    assert not any("foreign secret" in item.value for item in at.error)
    assert "taskpilot_task" not in at.session_state


def test_two_independent_product_sessions_do_not_share_credentials_or_tasks():
    def request(method, url, **kwargs):
        if url.endswith("/auth/login"):
            email = kwargs["json"]["email"]
            token = "token-a" if email == "a@example.com" else "token-b"
            return _response(
                method, url, {"access_token": token, "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            token = kwargs["headers"].get("Authorization", "")
            suffix = "a" if token.endswith("token-a") else "b"
            return _response(
                method,
                url,
                {
                    "user_id": f"user-{suffix}",
                    "membership_id": f"member-{suffix}",
                    "organization_id": f"org-{suffix}",
                    "role": "member",
                },
            )
        token = kwargs["headers"].get("Authorization", "")
        suffix = "A" if token.endswith("token-a") else "B"
        return _response(method, url, [_task(str(uuid4()), f"Task {suffix}")])

    with patch("httpx.request", side_effect=request):
        first = _login(AppTest.from_file("../../src/streamlit_app.py").run(), "a@example.com")
        second = _login(AppTest.from_file("../../src/streamlit_app.py").run(), "b@example.com")
    assert first.session_state.taskpilot_client.token == "token-a"
    assert second.session_state.taskpilot_client.token == "token-b"
    assert first.session_state.taskpilot_identity["organization_id"] == "org-a"
    assert second.session_state.taskpilot_identity["organization_id"] == "org-b"
    assert (
        first.selectbox(key="taskpilot_selected_task_id").options
        != second.selectbox(key="taskpilot_selected_task_id").options
    )


def test_product_network_failure_is_bounded():
    def request(method, url, **kwargs):
        if url.endswith("/auth/login"):
            return _response(
                method, url, {"access_token": "token", "expires_at": "2026-10-01T00:00:00Z"}
            )
        if url.endswith("/auth/session"):
            return _response(
                method,
                url,
                {
                    "user_id": "user",
                    "membership_id": "member",
                    "organization_id": "org",
                    "role": "member",
                },
            )
        raise httpx.ConnectError("raw connection detail")

    with patch("httpx.request", side_effect=request):
        at = _login(AppTest.from_file("../../src/streamlit_app.py").run())
    assert any("Could not reach" in item.value for item in at.error)
    assert not any("raw connection detail" in item.value for item in at.error)
