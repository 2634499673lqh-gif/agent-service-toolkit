"""Streamlit Product shell for persisted TaskPilot tasks."""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

import streamlit as st

from client.taskpilot import TaskPilotClient, TaskPilotClientError


def _clear_product_state() -> None:
    for key in list(st.session_state):
        if isinstance(key, str) and key.startswith("taskpilot_"):
            st.session_state.pop(key, None)


def clear_task_descendants() -> None:
    """Drop display state owned by a previously selected task."""
    for key in (
        "taskpilot_task",
        "taskpilot_selected_run_id",
        "taskpilot_run",
        "taskpilot_selected_approval_id",
        "taskpilot_approval",
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
    if error.kind == "unauthorized":
        _clear_product_state()
        st.error("Your session has expired. Please sign in again.")
    else:
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


def _render_tasks(client: TaskPilotClient) -> None:
    st.header("Tasks")
    with st.form("taskpilot_create"):
        title = st.text_input("Title", key="taskpilot_new_title")
        description = st.text_area("Description (optional)", key="taskpilot_new_description")
        if st.form_submit_button("Create task"):
            try:
                created = client.create_task(title, description or None)
                clear_task_descendants()
                st.session_state.taskpilot_selected_task_id = created.get("id")
                st.rerun()
            except TaskPilotClientError as error:
                if error.kind in {"timeout", "network"}:
                    st.warning(
                        "Create outcome is unknown. Inspect the refreshed task list before trying again."
                    )
                _handle_error(error)
    st.button("Refresh tasks", key="taskpilot_refresh")
    try:
        # The snapshot is display-only; every render obtains fresh server truth.
        st.session_state.taskpilot_tasks = client.list_tasks()
    except TaskPilotClientError as error:
        _handle_error(error)
        return
    tasks = st.session_state.get("taskpilot_tasks", [])
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
        detail = client.get_task(task_id)
        st.session_state.taskpilot_task = detail
    except TaskPilotClientError as error:
        if error.status_code == 404:
            clear_task_descendants()
            st.session_state.pop("taskpilot_selected_task_id", None)
        _handle_error(error)
        return
    st.subheader(_task_value(detail, "title", "Untitled task"))
    st.write(_task_value(detail, "description"))
    st.caption(
        f"Status: {_task_value(detail, 'status')} · Created: {_task_value(detail, 'created_at')} · Updated: {_task_value(detail, 'updated_at')}"
    )


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
        st.write(f"Organization: {st.session_state.taskpilot_identity.get('organization_id', '')}")
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


__all__ = ["render_product"]
