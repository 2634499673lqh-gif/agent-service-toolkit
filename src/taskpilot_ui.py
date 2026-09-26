"""Streamlit Product views for persisted TaskPilot evidence.

The Product view is deliberately a read-through UI. The API owns identity,
tenant visibility, lifecycle and approval authority; Streamlit stores only
navigation and display snapshots for the current browser session.
"""

from __future__ import annotations

import os
import re
from typing import Any
from uuid import UUID

import streamlit as st

from client.taskpilot import TaskPilotClient, TaskPilotClientError


def _clear_product_state() -> None:
    """Clear every Product credential, snapshot, navigation id and form value."""

    for key in list(st.session_state):
        if isinstance(key, str) and key.startswith("taskpilot_"):
            st.session_state.pop(key, None)


def _clear_run_children() -> None:
    """Drop state belonging to a previous run while keeping run selection."""

    for key in (
        "taskpilot_run",
        "taskpilot_approvals",
        "taskpilot_selected_approval_id",
        "taskpilot_approval",
        "taskpilot_trace",
    ):
        st.session_state.pop(key, None)


def clear_task_descendants() -> None:
    """Drop state owned by a previously selected task."""

    for key in (
        "taskpilot_task",
        "taskpilot_runs",
        "taskpilot_selected_run_id",
        "taskpilot_run",
        "taskpilot_approvals",
        "taskpilot_selected_approval_id",
        "taskpilot_approval",
        "taskpilot_trace",
    ):
        st.session_state.pop(key, None)


def _client() -> TaskPilotClient:
    client = st.session_state.get("taskpilot_client")
    if isinstance(client, TaskPilotClient):
        return client
    base_url = os.getenv("TASKPILOT_URL") or os.getenv("AGENT_URL") or "http://0.0.0.0:8080"
    client = TaskPilotClient(base_url)
    st.session_state.taskpilot_client = client
    return client


def _handle_error(error: TaskPilotClientError) -> None:
    """Render a fixed safe message and discard protected state on 401."""

    if error.kind == "unauthorized":
        _clear_product_state()
        st.error("Your session has expired. Please sign in again.")
        return
    st.error(str(error))


def _password_input(scope: str) -> tuple[str, str]:
    """Use a new widget key after each attempt so Streamlit clears the value."""

    attempt = int(st.session_state.get(f"{scope}_attempt", 0))
    key = f"{scope}_password_{attempt}"
    return key, st.text_input("Password", type="password", key=key)


def _login() -> None:
    st.subheader("Sign in to TaskPilot")
    with st.form("taskpilot_login", clear_on_submit=True):
        email = st.text_input("Email", key="taskpilot_email")
        _, password = _password_input("taskpilot_login")
        submitted = st.form_submit_button("Sign in")
    if not submitted:
        return
    st.session_state.taskpilot_login_attempt = (
        int(st.session_state.get("taskpilot_login_attempt", 0)) + 1
    )
    try:
        result = _client().login(email, password)
        if result.requires_organization:
            st.session_state.taskpilot_org_ids = list(result.organization_ids)
            st.rerun()
        else:
            st.session_state.taskpilot_identity = _client().session()
            st.session_state.pop("taskpilot_org_ids", None)
            st.success("Signed in.")
            st.rerun()
    except TaskPilotClientError as error:
        _handle_error(error)


def _organization_login() -> None:
    ids: list[UUID] = st.session_state.get("taskpilot_org_ids", [])
    st.subheader("Choose an organization")
    choice = st.selectbox(
        "Organization", options=ids, index=None, format_func=str, key="taskpilot_org_choice"
    )
    with st.form("taskpilot_org_login", clear_on_submit=True):
        st.text_input("Email", key="taskpilot_email_selection")
        _, password = _password_input("taskpilot_org_login")
        submitted = st.form_submit_button("Sign in to selected organization")
    if submitted:
        if choice is None:
            st.error("Select an organization before signing in.")
            return
        st.session_state.taskpilot_org_login_attempt = (
            int(st.session_state.get("taskpilot_org_login_attempt", 0)) + 1
        )
        try:
            result = _client().login(
                st.session_state.get("taskpilot_email_selection", ""), password, choice
            )
            if result.requires_organization:
                st.error("Organization selection was not accepted.")
            else:
                st.session_state.taskpilot_identity = _client().session()
                st.session_state.pop("taskpilot_org_ids", None)
                st.rerun()
        except TaskPilotClientError as error:
            _handle_error(error)


def _task_value(task: dict[str, Any], key: str, fallback: str = "") -> str:
    value = task.get(key, fallback)
    return str(value) if value is not None else fallback


def _run_status(value: object) -> str:
    return str(value or "unknown").upper()


def _display_value(value: object, unavailable: str = "Unavailable / not observed") -> str:
    return unavailable if value is None else str(value)


_SENSITIVE_KEY = re.compile(r"(?i)(password|secret|token|api[_-]?key|authorization|credential)")
_BEARER = re.compile(r"(?i)(\bBearer\s+)[^\s,;]+")


def _safe_object(value: object) -> object:
    """Keep display projections safe if a malformed fixture bypasses API redaction."""

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _safe_object(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_object(item) for item in value]
    if isinstance(value, str):
        return _BEARER.sub(r"\1[REDACTED]", value)
    return value


def _trace_usage(value: object) -> str:
    if not isinstance(value, dict):
        return "Unavailable / not observed"
    status = value.get("status")
    if status == "unavailable":
        return f"Unavailable ({value.get('reason', 'not observed')})"
    if status == "known":
        return (
            "Known · input tokens: "
            f"{value.get('input_tokens', 'Unavailable')} · output tokens: "
            f"{value.get('output_tokens', 'Unavailable')} · total tokens: "
            f"{value.get('total_tokens', 'Unavailable')}"
        )
    return "Unavailable / not observed"


def _trace_estimate(value: object) -> str:
    if not isinstance(value, dict):
        return "Unavailable / not observed"
    status = value.get("status")
    if status == "known":
        return f"Informational estimate: {value.get('amount', 'Unavailable')}"
    return f"Unavailable ({value.get('reason', 'not observed')})"


def _trace_metadata(value: object) -> str:
    if not isinstance(value, dict):
        return "Unavailable / not observed"
    allowed = {
        key: _safe_object(value[key])
        for key in (
            "provider",
            "model",
            "version",
            "response_id",
            "request_id",
            "provider_request_id",
        )
        if key in value
    }
    return str(allowed) if allowed else "Unavailable / not observed"


def _safe_text(value: object) -> str:
    return _BEARER.sub(r"\1[REDACTED]", _display_value(value))


def _reconcile_task_reads(client: TaskPilotClient, task_id: str) -> None:
    """Best-effort reads after a lost/conflicting mutation response."""

    try:
        st.session_state.taskpilot_task = client.get_task(task_id)
        st.session_state.taskpilot_runs = client.list_task_runs(task_id)
    except TaskPilotClientError as error:
        _handle_error(error)


def _reconcile_approval_reads(
    client: TaskPilotClient, task_id: str, run_id: str, approval_id: str
) -> None:
    """Re-read all selected approval resources after an uncertain decision."""

    try:
        st.session_state.taskpilot_task = client.get_task(task_id)
        st.session_state.taskpilot_run = client.get_task_run(task_id, run_id)
        st.session_state.taskpilot_approvals = client.list_approvals(task_id, run_id)
        st.session_state.taskpilot_approval = client.get_approval(task_id, run_id, approval_id)
    except TaskPilotClientError as error:
        _handle_error(error)


def _render_run_view(client: TaskPilotClient, task_id: str, task: dict[str, Any]) -> None:
    """Render server-ordered runs and the selected run's evidence views."""

    st.subheader("Runs")
    status = _run_status(task.get("status"))
    can_start = status in {"DRAFT", "FAILED"}
    action_label = "Retry with new run" if status == "FAILED" else "Start run"
    mutation_in_flight = bool(st.session_state.get("taskpilot_mutation_in_flight"))
    if can_start:
        if st.button(action_label, key="taskpilot_start_run", disabled=mutation_in_flight):
            st.session_state.taskpilot_mutation_in_flight = True
            try:
                created = client.start_task_run(task_id)
                created_id = created.get("id")
                if created_id is not None:
                    st.session_state.taskpilot_selected_run_id = str(created_id)
                _reconcile_task_reads(client, task_id)
                if created_id is not None:
                    st.session_state.taskpilot_run = client.get_task_run(task_id, str(created_id))
                st.rerun()
            except TaskPilotClientError as error:
                if error.kind in {
                    "timeout",
                    "network",
                    "service_unavailable",
                    "malformed_response",
                }:
                    st.warning(
                        "Run start outcome is unknown. Refresh the task and run list before trying again."
                    )
                if error.status_code == 409:
                    st.warning(
                        "The task changed before the run could start. Current state was refreshed."
                    )
                _handle_error(error)
                _reconcile_task_reads(client, task_id)
                return
            finally:
                st.session_state.taskpilot_mutation_in_flight = False
    st.caption(
        "Starting a run creates persisted queued/pending state only; the public runtime is not executed from this Product view."
    )
    if st.button("Refresh runs", key="taskpilot_refresh_runs"):
        st.rerun()

    try:
        with st.spinner("Loading runs..."):
            runs = client.list_task_runs(task_id)
        st.session_state.taskpilot_runs = runs
    except TaskPilotClientError as error:
        if error.status_code == 404:
            _clear_run_children()
            st.session_state.pop("taskpilot_selected_run_id", None)
            st.info("That task is no longer available.")
        _handle_error(error)
        return
    if not runs:
        st.info("No runs yet. Start a run deliberately when the task is ready.")
        return

    labels: dict[str, str] = {}
    for run in runs:
        run_id = str(run.get("id", ""))
        labels[run_id] = (
            f"Run {run.get('run_number', '?')} · {_run_status(run.get('status'))} · "
            f"{_display_value(run.get('created_at'))}"
        )
    selected = st.session_state.get("taskpilot_selected_run_id")
    if selected is not None and str(selected) not in labels:
        _clear_run_children()
        st.session_state.pop("taskpilot_selected_run_id", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    run_id = st.selectbox(
        "Task run",
        options=list(labels),
        index=index,
        format_func=lambda value: labels[value],
        key="taskpilot_selected_run_id",
        placeholder="Select a run",
        on_change=_clear_run_children,
    )
    if run_id is None:
        _clear_run_children()
        return
    try:
        with st.spinner("Loading run status..."):
            run = client.get_task_run(task_id, run_id)
        st.session_state.taskpilot_run = run
    except TaskPilotClientError as error:
        if error.status_code == 404:
            _clear_run_children()
            st.session_state.pop("taskpilot_selected_run_id", None)
            st.info("That run is no longer available.")
        _handle_error(error)
        return

    st.write(f"Run number: {_display_value(run.get('run_number'))}")
    st.write(f"Status: {_run_status(run.get('status'))}")
    st.write(f"Created: {_display_value(run.get('created_at'))}")
    st.write(f"Updated: {_display_value(run.get('updated_at'))}")
    _render_approvals(client, task_id, run_id)
    _render_trace(client, task_id, run_id)


def _approval_label(approval: dict[str, Any]) -> str:
    return (
        f"{approval.get('action_name', 'Approval')} · "
        f"{str(approval.get('status', 'unknown')).upper()} · "
        f"{_display_value(approval.get('created_at'))}"
    )


def _render_approval_detail(
    client: TaskPilotClient, task_id: str, run_id: str, approval_id: str
) -> None:
    try:
        with st.spinner("Loading approval..."):
            approval = client.get_approval(task_id, run_id, approval_id)
        st.session_state.taskpilot_approval = approval
    except TaskPilotClientError as error:
        if error.status_code == 404:
            st.session_state.pop("taskpilot_selected_approval_id", None)
            st.session_state.pop("taskpilot_approval", None)
            st.info("That approval is no longer available.")
        _handle_error(error)
        return

    st.markdown("#### Approval details")
    st.write(f"Action: {_display_value(approval.get('action_name'))}")
    st.write(f"Action version: {_display_value(approval.get('action_version'))}")
    st.write(f"Risk: {_display_value(approval.get('risk_level'))}")
    st.write(f"Status: {str(approval.get('status', 'unknown')).upper()}")
    st.write(f"Requester membership: {_display_value(approval.get('requester_membership_id'))}")
    st.write(f"Decider membership: {_display_value(approval.get('decider_membership_id'))}")
    st.write(f"Created: {_display_value(approval.get('created_at'))}")
    st.write(f"Updated: {_display_value(approval.get('updated_at'))}")
    st.write(f"Decided at: {_display_value(approval.get('decided_at'))}")
    st.write(f"Decision reason: {_safe_text(approval.get('decision_reason'))}")
    st.write(f"Replan count: {_display_value(approval.get('replan_count'))}")
    st.write(f"Step position: {_display_value(approval.get('step_position'))}")
    st.write("Proposed action (immutable):")
    proposed = approval.get("proposed_action")
    if isinstance(proposed, dict):
        st.json(_safe_object(proposed))
    else:
        st.write("Unavailable / not observed")

    role = str(st.session_state.get("taskpilot_identity", {}).get("role", "")).lower()
    pending = str(approval.get("status", "")).lower() == "pending"
    if role not in {"owner", "admin"}:
        st.info("Members can inspect approvals but cannot decide them.")
        return
    if not pending:
        st.info("This approval is already decided.")
        return

    reason = st.text_area(
        "Decision reason (optional, max 500 characters)",
        key=f"taskpilot_approval_reason_{approval_id}",
        max_chars=500,
    )
    disabled = bool(st.session_state.get("taskpilot_mutation_in_flight"))
    approve, reject = st.columns(2)
    approve_clicked = approve.button("Approve", key="taskpilot_approve", disabled=disabled)
    reject_clicked = reject.button("Reject", key="taskpilot_reject", disabled=disabled)
    if not (approve_clicked or reject_clicked):
        return

    decision = "approve" if approve_clicked else "reject"
    st.session_state.taskpilot_mutation_in_flight = True
    try:
        client.get_task(task_id)
        client.get_task_run(task_id, run_id)
        current = client.get_approval(task_id, run_id, approval_id)
        if str(current.get("status", "")).lower() != "pending":
            st.warning("This approval was decided elsewhere. Current state was refreshed.")
        else:
            client.decide_approval(task_id, run_id, approval_id, decision, reason or None)
            st.success("Approval decision recorded; it does not execute or resume this run.")
        st.session_state.taskpilot_task = client.get_task(task_id)
        st.session_state.taskpilot_approvals = client.list_approvals(task_id, run_id)
        st.session_state.taskpilot_approval = client.get_approval(task_id, run_id, approval_id)
        st.session_state.taskpilot_run = client.get_task_run(task_id, run_id)
    except TaskPilotClientError as error:
        if error.status_code == 409:
            st.warning("The approval changed before your decision. Current state was refreshed.")
        if error.kind in {"timeout", "network", "service_unavailable", "malformed_response"}:
            st.warning("Approval decision outcome is unknown. Current state was refreshed.")
        _handle_error(error)
        if error.status_code == 409 or error.kind in {
            "timeout",
            "network",
            "service_unavailable",
            "malformed_response",
        }:
            _reconcile_approval_reads(client, task_id, run_id, approval_id)
    finally:
        st.session_state.taskpilot_mutation_in_flight = False


def _render_approvals(client: TaskPilotClient, task_id: str, run_id: str) -> None:
    st.subheader("Approvals for selected run")
    if st.button("Refresh approvals", key="taskpilot_refresh_approvals"):
        st.rerun()
    try:
        with st.spinner("Loading approvals..."):
            approvals = client.list_approvals(task_id, run_id)
        st.session_state.taskpilot_approvals = approvals
    except TaskPilotClientError as error:
        if error.status_code == 404:
            st.session_state.pop("taskpilot_selected_approval_id", None)
            st.session_state.pop("taskpilot_approval", None)
            st.info("That run is no longer available.")
        _handle_error(error)
        return
    if not approvals:
        st.info("No approvals for this run.")
        return
    labels = {str(item.get("id")): _approval_label(item) for item in approvals}
    selected = st.session_state.get("taskpilot_selected_approval_id")
    if selected is not None and str(selected) not in labels:
        st.session_state.pop("taskpilot_selected_approval_id", None)
        st.session_state.pop("taskpilot_approval", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    approval_id = st.selectbox(
        "Approval",
        options=list(labels),
        index=index,
        format_func=lambda value: labels[value],
        key="taskpilot_selected_approval_id",
        placeholder="Select an approval",
    )
    if approval_id is not None:
        _render_approval_detail(client, task_id, run_id, str(approval_id))


def _render_trace(client: TaskPilotClient, task_id: str, run_id: str) -> None:
    st.subheader("Trace timeline")
    limit = st.selectbox("Trace bound", options=[100, 500], key="taskpilot_trace_limit")
    if st.button("Refresh trace", key="taskpilot_refresh_trace"):
        st.session_state.pop("taskpilot_trace", None)
    try:
        with st.spinner("Loading trace..."):
            trace = client.get_trace(task_id, run_id, limit=int(limit))
        st.session_state.taskpilot_trace = trace
    except TaskPilotClientError as error:
        if error.status_code == 404:
            st.session_state.pop("taskpilot_trace", None)
            st.info("That run is no longer available.")
        _handle_error(error)
        return
    if not trace:
        st.info("No trace evidence observed for this run.")
        return
    if len(trace) >= int(limit):
        st.caption("This bounded view may be incomplete.")
    for index, event in enumerate(trace, start=1):
        with st.expander(
            f"Event {index} · {_display_value(event.get('event_id'))}", expanded=False
        ):
            st.text(f"Kind: {_display_value(event.get('event_kind'))}")
            st.text(f"Agent: {_display_value(event.get('agent_name'))}")
            st.text(
                f"Node/tool: {_display_value(event.get('tool_name') or event.get('agent_name'))}"
            )
            st.text(f"Tool version: {_display_value(event.get('tool_version'))}")
            st.text(f"Status: {_display_value(event.get('status'))}")
            st.text(f"Started: {_display_value(event.get('started_at'))}")
            st.text(f"Finished: {_display_value(event.get('finished_at'))}")
            st.text(f"Duration: {_display_value(event.get('duration_ms'))}")
            st.text(
                f"Step/replan/retry: {event.get('step_position', 'Unavailable / not observed')} / "
                f"{event.get('replan_count', 'Unavailable / not observed')} / "
                f"{event.get('retry_count', 'Unavailable / not observed')}"
            )
            st.text(f"Event id: {_display_value(event.get('event_id'))}")
            st.text(f"Request id: {_display_value(event.get('request_id'))}")
            st.text(f"Agent run id: {_display_value(event.get('agent_run_id'))}")
            st.text(f"Tool call id: {_display_value(event.get('tool_call_id'))}")
            st.text(f"Call index: {_display_value(event.get('call_index'))}")
            st.text(f"Approval id: {_display_value(event.get('approval_id'))}")
            st.text(f"Usage: {_trace_usage(event.get('usage'))}")
            st.text(f"Cost estimate: {_trace_estimate(event.get('estimate'))}")
            st.text(f"Metadata: {_trace_metadata(event.get('metadata'))}")
            if event.get("error_class") or event.get("error_code") or event.get("error_message"):
                st.text(f"Error class: {_display_value(event.get('error_class'))}")
                st.text(f"Error code: {_display_value(event.get('error_code'))}")
                st.text(f"Error message: {_safe_text(event.get('error_message'))}")
            approval_id = event.get("approval_id")
            if approval_id and st.button(
                "Open approval for this run",
                key=f"taskpilot_trace_approval_{event.get('event_id')}",
            ):
                st.session_state.taskpilot_selected_approval_id = str(approval_id)
                st.rerun()


def _render_tasks(client: TaskPilotClient) -> None:
    st.header("Tasks")
    mutation_in_flight = bool(st.session_state.get("taskpilot_mutation_in_flight"))
    with st.form("taskpilot_create"):
        title = st.text_input("Title", key="taskpilot_new_title")
        description = st.text_area("Description (optional)", key="taskpilot_new_description")
        submitted = st.form_submit_button("Create task", disabled=mutation_in_flight)
    if submitted:
        st.session_state.taskpilot_mutation_in_flight = True
        try:
            created = client.create_task(title, description or None)
            clear_task_descendants()
            st.session_state.taskpilot_selected_task_id = created.get("id")
            st.rerun()
        except TaskPilotClientError as error:
            if error.kind in {
                "timeout",
                "network",
                "service_unavailable",
                "malformed_response",
            }:
                st.warning(
                    "Create outcome is unknown. Inspect the refreshed task list before trying again."
                )
            _handle_error(error)
        finally:
            if "taskpilot_client" in st.session_state:
                st.session_state.taskpilot_mutation_in_flight = False
    st.button("Refresh tasks", key="taskpilot_refresh")
    try:
        with st.spinner("Loading tasks..."):
            tasks = client.list_tasks()
        st.session_state.taskpilot_tasks = tasks
    except TaskPilotClientError as error:
        _handle_error(error)
        return
    if not tasks:
        st.info("No tasks yet.")
        return
    labels = {str(task.get("id")): _task_value(task, "title", "Untitled task") for task in tasks}
    selected = st.session_state.get("taskpilot_selected_task_id")
    if selected is not None and str(selected) not in labels:
        clear_task_descendants()
        st.session_state.pop("taskpilot_selected_task_id", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    task_id = st.selectbox(
        "Task",
        options=list(labels),
        index=index,
        format_func=lambda value: labels[value],
        key="taskpilot_selected_task_id",
        placeholder="Select a task",
        on_change=clear_task_descendants,
    )
    if task_id is None:
        clear_task_descendants()
        return
    try:
        with st.spinner("Loading task..."):
            detail = client.get_task(task_id)
        st.session_state.taskpilot_task = detail
    except TaskPilotClientError as error:
        if error.status_code == 404:
            clear_task_descendants()
            st.session_state.pop("taskpilot_selected_task_id", None)
            st.info("That task is no longer available.")
        _handle_error(error)
        return
    st.subheader(_task_value(detail, "title", "Untitled task"))
    st.write(_task_value(detail, "description"))
    st.caption(
        f"Status: {_run_status(detail.get('status'))} · Created: {_task_value(detail, 'created_at')} · Updated: {_task_value(detail, 'updated_at')}"
    )
    _render_run_view(client, str(task_id), detail)


def render_product() -> None:
    """Render Product view and keep all state session-local."""

    client = _client()
    st.title("TaskPilot")
    if st.session_state.get("taskpilot_identity") is None:
        if st.session_state.get("taskpilot_org_ids"):
            _organization_login()
        else:
            _login()
        return
    with st.sidebar:
        identity = st.session_state.get("taskpilot_identity", {})
        st.write(f"Organization: {identity.get('organization_id', '')}")
        if st.button("Log out", key="taskpilot_logout"):
            try:
                client.logout()
            except TaskPilotClientError:
                st.warning("Local session cleared; server sign-out could not be confirmed.")
            finally:
                _clear_product_state()
            st.rerun()
    try:
        identity = client.session()
        previous = st.session_state.taskpilot_identity
        if (identity.get("user_id"), identity.get("organization_id")) != (
            previous.get("user_id"),
            previous.get("organization_id"),
        ):
            _clear_product_state()
            st.info("Account or organization changed. Please sign in again.")
            return
        st.session_state.taskpilot_identity = identity
        _render_tasks(client)
    except TaskPilotClientError as error:
        _handle_error(error)


__all__ = ["clear_task_descendants", "render_product"]
