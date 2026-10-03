"""Streamlit Product views for persisted TaskPilot evidence.

The Product view is deliberately a read-through UI. The API owns identity,
tenant visibility, lifecycle and approval authority; Streamlit stores only
navigation and display snapshots for the current browser session.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any
from uuid import UUID

import streamlit as st

from client.taskpilot import TaskPilotClient, TaskPilotClientError

_ACTIVE_POLL_MAX_ATTEMPTS = 4
_ACTIVE_POLL_INTERVAL_SECONDS = 0.2
_STATUS_LABELS = {
    "DRAFT": "Draft",
    "PENDING": "Queued",
    "QUEUED": "Queued",
    "RUNNING": "In progress",
    "SUCCEEDED": "Completed",
    "FAILED": "Failed",
    "CANCELLED": "Cancelled",
    "NOT_STARTED": "Not started",
    "NOT_RUN": "Not run",
    "PASSED": "Passed",
}

# The API keeps stable English enum values.  These labels are deliberately kept
# in the UI layer so neither the runtime nor the persistence contract has to
# know about presentation language.
_STATUS_LABELS_ZH = {
    "DRAFT": "草稿",
    "PENDING": "等待执行",
    "QUEUED": "排队中",
    "RUNNING": "执行中",
    "SUCCEEDED": "已完成",
    "FAILED": "失败",
    "CANCELLED": "已取消",
    "NOT_STARTED": "未开始",
    "NOT_RUN": "未运行",
    "PASSED": "已通过",
}

_STAGE_LABELS_ZH = {"planner": "规划", "execution": "执行", "verifier": "验证"}

_METRIC_LABELS_ZH = {
    "mean_ndvi_period_a": "时段 A 平均 NDVI",
    "mean_ndvi_period_b": "时段 B 平均 NDVI",
    "mean_delta_ndvi": "NDVI 变化",
    "significant_decline_area_m2": "显著下降面积（平方米）",
    "decline_percentage": "植被下降比例",
    "valid_analysis_area_m2": "有效分析面积（平方米）",
    "valid_pixels": "有效像元数",
    "decline_threshold": "下降判定阈值",
    "mean_ndwi_period_a": "时段 A 平均 NDWI",
    "mean_ndwi_period_b": "时段 B 平均 NDWI",
    "mean_delta_ndwi": "NDWI 变化",
    "mean_ndbi_period_a": "时段 A 平均 NDBI",
    "mean_ndbi_period_b": "时段 B 平均 NDBI",
    "mean_delta_ndbi": "NDBI 变化",
}


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
        st.error("登录会话已过期，请重新登录。 Your session has expired. Please sign in again.")
        return
    st.error(str(error))


def _password_input(scope: str) -> tuple[str, str]:
    """Use a new widget key after each attempt so Streamlit clears the value."""

    attempt = int(st.session_state.get(f"{scope}_attempt", 0))
    key = f"{scope}_password_{attempt}"
    return key, st.text_input("密码", type="password", key=key)


def _login() -> None:
    st.subheader("Sign in to TaskPilot")
    st.caption("登录分析工作台，查看和执行你的遥感分析任务。")
    with st.form("taskpilot_login", clear_on_submit=True):
        email = st.text_input("邮箱", key="taskpilot_email")
        _, password = _password_input("taskpilot_login")
        submitted = st.form_submit_button(
            "登录",
            key="FormSubmitter:taskpilot_login-Sign in",
            help="使用 TaskPilot 账号登录",
        )
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
            st.success("登录成功。")
            st.rerun()
    except TaskPilotClientError as error:
        _handle_error(error)


def _organization_login() -> None:
    ids: list[UUID] = st.session_state.get("taskpilot_org_ids", [])
    st.subheader("选择工作空间")
    st.caption("你的账号属于多个组织，请选择本次分析使用的工作空间。")
    choice = st.selectbox(
        "工作空间", options=ids, index=None, format_func=str, key="taskpilot_org_choice"
    )
    with st.form("taskpilot_org_login", clear_on_submit=True):
        st.text_input("邮箱", key="taskpilot_email_selection")
        _, password = _password_input("taskpilot_org_login")
        submitted = st.form_submit_button(
            "登录所选工作空间",
            key="FormSubmitter:taskpilot_org_login-Sign in to selected organization",
        )
    if submitted:
        if choice is None:
            st.error("请先选择工作空间。")
            return
        st.session_state.taskpilot_org_login_attempt = (
            int(st.session_state.get("taskpilot_org_login_attempt", 0)) + 1
        )
        try:
            result = _client().login(
                st.session_state.get("taskpilot_email_selection", ""), password, choice
            )
            if result.requires_organization:
                st.error("工作空间选择未被接受，请重试。")
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


def _human_status(value: object) -> str:
    normalized = _run_status(value)
    return _STATUS_LABELS.get(normalized, normalized.replace("_", " ").title())


def _human_status_zh(value: object) -> str:
    """Return a user-facing Chinese status while preserving API enum values."""

    normalized = _run_status(value)
    return _STATUS_LABELS_ZH.get(normalized, normalized.replace("_", " "))


def _stage_status(stage_status: object) -> dict[str, str]:
    if not isinstance(stage_status, dict):
        return {}
    return {
        stage: _human_status(stage_status.get(stage, "not_started"))
        for stage in ("planner", "execution", "verifier")
    }


def _stage_status_zh(stage_status: object) -> dict[str, str]:
    if not isinstance(stage_status, dict):
        return {}
    return {
        stage: _human_status_zh(stage_status.get(stage, "not_started"))
        for stage in ("planner", "execution", "verifier")
    }


def _period_label(period: object) -> str:
    """Format a persisted period/evidence object without assuming one schema."""

    if isinstance(period, dict):
        start = period.get("start") or period.get("date")
        end = period.get("end")
        if start and end and str(start) != str(end):
            return f"{start} 至 {end}"
        if start:
            return str(start)
    if isinstance(period, str) and "/" in period:
        start, end = period.split("/", 1)
        return f"{start} 至 {end}"
    return _display_value(period, "未记录")


def _analysis_periods(metadata: dict[str, Any]) -> tuple[str, str]:
    """Project persisted Period A/B fields, with scene dates as a safe fallback."""

    evidence = metadata.get("selected_scene_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    periods = metadata.get("analysis_periods")
    periods = periods if isinstance(periods, dict) else {}
    period_a = None
    period_b = None
    if periods:
        period_a = periods.get("period_a")
        period_b = periods.get("period_b")
    if period_a is None:
        period_a = evidence.get("period_a_date")
    if period_b is None:
        period_b = evidence.get("period_b_date")
    return _period_label(period_a), _period_label(period_b)


def _analysis_area(metadata: dict[str, Any]) -> str:
    """Return the AOI identifier explicitly persisted in result metadata."""

    value = metadata.get("analysis_area")
    if isinstance(value, str) and value:
        return value.replace("_", " ")
    provenance = metadata.get("provenance")
    if isinstance(provenance, dict):
        value = provenance.get("aoi_key")
        if isinstance(value, str) and value:
            return value.replace("_", " ")
    return "未记录"


def _format_metric(key: str, value: object) -> str:
    """Format common GeoChange metrics for a compact, readable summary."""

    if value is None:
        return "未记录"
    if not isinstance(value, (int, float, str)):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if key == "decline_percentage":
        return f"{number:.1f}%"
    if key in {"mean_ndvi_period_a", "mean_ndvi_period_b", "mean_delta_ndvi", "decline_threshold"}:
        return f"{number:.3f}"
    if key.endswith("_m2"):
        return f"{number:,.0f}"
    if key == "valid_pixels":
        return f"{number:,.0f}"
    return f"{number:g}"


def _provenance_label_zh(execution_mode: object) -> str:
    mode = str(execution_mode or "unknown")
    labels = {
        "CACHED_REAL_SENTINEL2_RASTER": "真实 Sentinel-2 栅格（本地缓存计算）",
        "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE": "实时 STAC 元数据 + 本地栅格计算",
        "REAL_STAC_LOCAL_FIXTURE": "本地场景与栅格样例",
        "CACHED_REAL_METADATA": "缓存的 Sentinel-2 场景数据",
        "deterministic_fixture": "确定性本地样例",
    }
    return labels.get(mode, "数据来源未记录")


def _runtime_profile(metadata: dict[str, Any]) -> dict[str, str]:
    persisted_profile = metadata.get("runtime_profile")
    profile = persisted_profile if isinstance(persisted_profile, dict) else {}
    provider = (
        metadata.get("provider")
        if "provider" in metadata
        else profile.get("provider") or metadata.get("provider_metadata")
    )
    if isinstance(provider, dict):
        provider_name = provider.get("provider")
        model = provider.get("model")
    else:
        provider_name = provider
        model = metadata.get("model", profile.get("model"))
    if "live_provider" in metadata:
        live: object = metadata["live_provider"]
    elif "live_provider" in profile:
        live = profile["live_provider"]
    elif "live" in metadata:
        live = metadata["live"]
    elif "live" in profile:
        live = profile["live"]
    else:
        live = None
    return {
        "execution_mode": str(metadata.get("execution_mode") or "Unavailable / not observed"),
        "provider": _display_value(provider_name),
        "model": _display_value(model),
        "live": _display_value(live),
    }


def _provenance(metadata: dict[str, Any]) -> dict[str, Any]:
    value = metadata.get("provenance")
    return value if isinstance(value, dict) else {}


def _scene_labels(metadata: dict[str, Any]) -> list[str]:
    evidence = metadata.get("selected_scene_evidence")
    if not isinstance(evidence, dict):
        return []
    labels: list[str] = []
    for period in ("period_a", "period_b"):
        item_id = evidence.get(f"{period}_item_id")
        date = evidence.get(f"{period}_date")
        if item_id is not None or date is not None:
            labels.append(
                f"{period}: {_display_value(item_id, 'scene selected')} · "
                f"{_display_value(date, 'date unavailable')}"
            )
    if labels:
        return labels
    for key, value in evidence.items():
        if isinstance(value, dict):
            scene_id = value.get("id") or value.get("item_id") or value.get("scene_id")
            date = value.get("date") or value.get("acquisition_date")
            if scene_id or date:
                labels.append(
                    f"{key}: {_display_value(scene_id, 'scene selected')} · "
                    f"{_display_value(date, 'date unavailable')}"
                )
    return labels


def _provenance_label(execution_mode: object) -> str:
    mode = str(execution_mode or "unknown")
    labels = {
        "CACHED_REAL_SENTINEL2_RASTER": (
            "Cached real Sentinel-2 raster · deterministic local computation "
            "(no live raster processing)"
        ),
        "REAL_STAC_LIVE_METADATA_LOCAL_FIXTURE": (
            "Live STAC metadata · cached/local raster · deterministic local computation "
            "(no live raster processing)"
        ),
        "REAL_STAC_LOCAL_FIXTURE": "Local metadata/raster fixture · deterministic local computation",
        "CACHED_REAL_METADATA": "Cached Sentinel-2 metadata/raster · deterministic local computation",
        "deterministic_fixture": "Deterministic local fixture computation",
    }
    return labels.get(mode, "Execution provenance is unavailable")


def _poll_attempt_key(run_id: str) -> str:
    return f"taskpilot_active_poll_attempts:{run_id}"


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

    st.subheader("执行记录")
    status = _run_status(task.get("status"))
    can_start = status in {"DRAFT", "FAILED"}
    action_label = "重新执行" if status == "FAILED" else "开始分析"
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
        "执行记录由 TaskPilot 运行时产生；刷新后可查看最新的持久化状态。 "
        "Runs execute in the TaskPilot runtime."
    )
    if st.button("刷新执行记录", key="taskpilot_refresh_runs"):
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
        st.info("暂时没有执行记录。确认分析范围后，点击“开始分析”。")
        return

    labels: dict[str, str] = {}
    for run in runs:
        run_id = str(run.get("id", ""))
        labels[run_id] = (
            f"Run {run.get('run_number', '?')} · 执行记录 · {_human_status_zh(run.get('status'))} · "
            f"{_display_value(run.get('created_at'))}"
        )
    selected = st.session_state.get("taskpilot_selected_run_id")
    if selected is not None and str(selected) not in labels:
        _clear_run_children()
        st.session_state.pop("taskpilot_selected_run_id", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    run_id = st.selectbox(
        "执行记录",
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

    st.markdown("### 执行状态")
    overview = st.columns(4)
    overview[0].metric("任务状态", _human_status_zh(task.get("status")))
    overview[1].metric("执行状态", _human_status_zh(run.get("status")))
    overview[2].metric("执行次数", _display_value(run.get("run_number")))
    overview[3].metric(
        "重新规划次数", _display_value((run.get("result_metadata") or {}).get("replan_count", 0))
    )
    st.caption(
        f"创建于 {_display_value(run.get('created_at'))} · 更新于 {_display_value(run.get('updated_at'))}"
    )
    run_status = _run_status(run.get("status"))
    if run_status == "RUNNING":
        poll_key = _poll_attempt_key(str(run_id))
        attempts = int(st.session_state.get(poll_key, 0))
        if attempts < _ACTIVE_POLL_MAX_ATTEMPTS:
            st.session_state[poll_key] = attempts + 1
            st.info(
                f"分析正在执行，正在检查最新状态（{attempts + 1}/{_ACTIVE_POLL_MAX_ATTEMPTS}）。"
            )
            time.sleep(_ACTIVE_POLL_INTERVAL_SECONDS)
            st.rerun()
        st.warning("已完成本轮自动检查。点击“刷新执行记录”继续查看。")
    else:
        st.session_state.pop(_poll_attempt_key(str(run_id)), None)
    if run_status == "SUCCEEDED":
        st.success("分析已完成。")
    elif run_status == "FAILED":
        st.error("分析失败。可展开下方技术追踪查看受限的失败证据。")
    result_metadata = run.get("result_metadata")
    if isinstance(result_metadata, dict):
        st.markdown("### 分析结果")
        if result_metadata.get("analysis_type") == "vegetation_change":
            metrics = result_metadata.get("metrics", {})
            metrics = metrics if isinstance(metrics, dict) else {}
            mode = str(result_metadata.get("execution_mode") or "unknown")
            data_source = result_metadata.get("data_source")
            source_label = (
                str(data_source)
                if isinstance(data_source, str) and data_source
                else _provenance_label_zh(mode)
            )
            st.info(f"数据来源：{source_label}")

            # Start with the answer a non-technical user needs: where, when,
            # and whether vegetation changed.  The persisted metadata remains
            # the only source of truth; this is a display projection only.
            period_a, period_b = _analysis_periods(result_metadata)
            context_columns = st.columns(4)
            context_columns[0].metric("分析区域", _analysis_area(result_metadata))
            context_columns[1].metric("时段 A", period_a)
            context_columns[2].metric("时段 B", period_b)
            verifier = _human_status_zh(result_metadata.get("verifier_status"))
            context_columns[3].metric("证据验证", verifier)

            decline = metrics.get("decline_percentage")
            delta = metrics.get("mean_delta_ndvi")
            if decline is not None or delta is not None:
                decline_text = _format_metric("decline_percentage", decline)
                delta_text = _format_metric("mean_delta_ndvi", delta)
                st.markdown("#### 结果解读")
                st.write(
                    f"本次分析覆盖 **{_analysis_area(result_metadata)}**，"
                    f"比较 **{period_a}** 与 **{period_b}**。"
                    f"平均 NDVI 变化为 **{delta_text}**，"
                    f"显著下降比例为 **{decline_text}**。"
                )
                if isinstance(decline, (int, float)):
                    st.progress(
                        min(max(float(decline) / 100, 0.0), 1.0),
                        text=f"植被下降比例：{decline_text}",
                    )

            profile = _runtime_profile(result_metadata)
            with st.expander("运行信息", expanded=False):
                profile_columns = st.columns(4)
                profile_columns[0].metric("执行模式", profile["execution_mode"])
                profile_columns[1].metric("模型提供方", profile["provider"])
                profile_columns[2].metric("模型", profile["model"])
                profile_columns[3].metric("实时提供方", profile["live"])
            stages = _stage_status_zh(result_metadata.get("stage_status"))
            if stages:
                st.markdown("#### 分析流程")
                stage_columns = st.columns(3)
                for column, stage in zip(
                    stage_columns, ("planner", "execution", "verifier"), strict=True
                ):
                    column.metric(_STAGE_LABELS_ZH[stage], stages[stage])
            scene_labels = _scene_labels(result_metadata)
            if scene_labels:
                st.markdown("#### 场景证据")
                for scene in scene_labels:
                    st.write(
                        f"- {scene.replace('period_a', '时段 A').replace('period_b', '时段 B')}"
                    )
            provenance = _provenance(result_metadata)
            provenance_summary = result_metadata.get("provenance_summary")
            if provenance or provenance_summary:
                st.markdown("#### 数据与计算说明")
                if isinstance(provenance_summary, str) and provenance_summary:
                    st.write(_safe_text(provenance_summary))
            if provenance:
                st.caption(
                    " · ".join(
                        f"{key.replace('_', ' ').title()}: {value}"
                        for key, value in provenance.items()
                    )
                )
            labels = _METRIC_LABELS_ZH
            cards = st.columns(3)
            for index, (key, label) in enumerate(labels.items()):
                if key in metrics:
                    cards[index % 3].metric(label, _format_metric(key, metrics[key]))
            with st.expander("技术证据（开发者视图）", expanded=False):
                st.json(_safe_object(result_metadata))
            st.markdown("#### NDVI 前后对比")
            image_columns = st.columns(3)
            for column, (name, label) in zip(
                image_columns,
                (("ndvi_before", "分析前"), ("ndvi_after", "分析后"), ("ndvi_change", "变化结果")),
                strict=True,
            ):
                try:
                    column.image(
                        client.get_artifact(task_id, run_id, name), caption=f"NDVI {label}"
                    )
                except TaskPilotClientError:
                    column.info(f"NDVI {label} 图像暂不可用。")
            if result_metadata.get("summary"):
                st.markdown("#### 分析说明")
                st.write(_safe_text(result_metadata.get("summary")))
        elif result_metadata.get("analysis_type") == "water_change":
            metrics = result_metadata.get("metrics", {})
            metrics = metrics if isinstance(metrics, dict) else {}
            mode = str(result_metadata.get("execution_mode") or "unknown")
            source = result_metadata.get("data_source") or _provenance_label_zh(mode)
            st.warning("探索性 NDWI 结果：用于指数对比，不代表已确认水体面积或水体扩张。")
            st.info(f"数据来源：{source}")
            period_a, period_b = _analysis_periods(result_metadata)
            context_columns = st.columns(4)
            context_columns[0].metric("分析区域", _analysis_area(result_metadata))
            context_columns[1].metric("时段 A", period_a)
            context_columns[2].metric("时段 B", period_b)
            context_columns[3].metric(
                "证据验证", _human_status_zh(result_metadata.get("verifier_status"))
            )
            delta = metrics.get("mean_delta_ndwi")
            if isinstance(delta, (int, float)):
                direction = "上升" if delta > 0 else "下降" if delta < 0 else "无变化"
                st.write(
                    f"平均 NDWI 变化为 **{_format_metric('mean_delta_ndwi', delta)}**（{direction}）。"
                )
            cards = st.columns(3)
            for index, key in enumerate(
                (
                    "valid_pixels",
                    "valid_analysis_area_m2",
                    "mean_ndwi_period_a",
                    "mean_ndwi_period_b",
                    "mean_delta_ndwi",
                )
            ):
                if key in metrics:
                    cards[index % 3].metric(
                        _METRIC_LABELS_ZH[key], _format_metric(key, metrics[key])
                    )
            provenance_summary = result_metadata.get("provenance_summary")
            if isinstance(provenance_summary, str) and provenance_summary:
                st.caption(_safe_text(provenance_summary))
            with st.expander("技术证据（开发者视图）", expanded=False):
                st.json(_safe_object(result_metadata))
            st.markdown("#### NDWI 前后对比")
            image_columns = st.columns(3)
            for column, (name, label) in zip(
                image_columns,
                (("ndwi_before", "分析前"), ("ndwi_after", "分析后"), ("ndwi_change", "变化结果")),
                strict=True,
            ):
                try:
                    column.image(
                        client.get_artifact(task_id, run_id, name), caption=f"NDWI {label}"
                    )
                except TaskPilotClientError:
                    column.info(f"NDWI {label} 图像暂不可用。")
            if result_metadata.get("summary"):
                st.markdown("#### 分析说明")
                st.write(_safe_text(result_metadata.get("summary")))
        elif result_metadata.get("analysis_type") == "urban_change":
            metrics = result_metadata.get("metrics", {})
            metrics = metrics if isinstance(metrics, dict) else {}
            st.warning("探索性 NDBI 结果：用于指数对比，不代表已确认建成区或城市扩张。")
            st.info("数据来源：已验证的本地 Sentinel-2 NDBI fixture；B11 原生分辨率为 20 m。")
            period_a, period_b = _analysis_periods(result_metadata)
            context_columns = st.columns(4)
            context_columns[0].metric("分析区域", _analysis_area(result_metadata))
            context_columns[1].metric("时段 A", period_a)
            context_columns[2].metric("时段 B", period_b)
            context_columns[3].metric(
                "证据验证", _human_status_zh(result_metadata.get("verifier_status"))
            )
            delta = metrics.get("mean_delta_ndbi")
            if isinstance(delta, (int, float)):
                direction = "上升" if delta > 0 else "下降" if delta < 0 else "无变化"
                st.write(
                    f"平均 NDBI 变化为 **{_format_metric('mean_delta_ndbi', delta)}**（{direction}）。"
                )
            cards = st.columns(3)
            for index, key in enumerate(
                (
                    "valid_pixels",
                    "valid_analysis_area_m2",
                    "mean_ndbi_period_a",
                    "mean_ndbi_period_b",
                    "mean_delta_ndbi",
                )
            ):
                if key in metrics:
                    cards[index % 3].metric(
                        _METRIC_LABELS_ZH[key], _format_metric(key, metrics[key])
                    )
            provenance_summary = result_metadata.get("provenance_summary")
            if isinstance(provenance_summary, str) and provenance_summary:
                st.caption(_safe_text(provenance_summary))
            with st.expander("技术证据（开发者视图）", expanded=False):
                st.json(_safe_object(result_metadata))
            st.markdown("#### NDBI 前后对比")
            image_columns = st.columns(3)
            for column, (name, label) in zip(
                image_columns,
                (("ndbi_before", "分析前"), ("ndbi_after", "分析后"), ("ndbi_change", "变化结果")),
                strict=True,
            ):
                try:
                    column.image(
                        client.get_artifact(task_id, run_id, name), caption=f"NDBI {label}"
                    )
                except TaskPilotClientError:
                    column.info(f"NDBI {label} 图像暂不可用。")
            if result_metadata.get("summary"):
                st.markdown("#### 分析说明")
                st.write(_safe_text(result_metadata.get("summary")))
        else:
            st.json(_safe_object(result_metadata))
    elif run_status in {"PENDING", "QUEUED"}:
        st.info("暂时还没有分析结果。刷新执行记录后可查看已保存的证据。")
    elif run_status == "FAILED":
        st.error("执行记录显示分析失败；技术追踪可能包含受限的失败证据。")
    else:
        st.info("这条历史执行记录没有可展示的 GeoChange 分析结果。")
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

    st.markdown("#### 审批详情")
    st.write(f"动作：{_display_value(approval.get('action_name'))}")
    st.write(f"动作版本：{_display_value(approval.get('action_version'))}")
    st.write(f"风险等级：{_display_value(approval.get('risk_level'))}")
    st.write(f"状态：{_human_status_zh(approval.get('status'))}")
    st.write(f"申请成员：{_display_value(approval.get('requester_membership_id'))}")
    st.write(f"决策成员：{_display_value(approval.get('decider_membership_id'))}")
    st.write(f"创建于：{_display_value(approval.get('created_at'))}")
    st.write(f"更新于：{_display_value(approval.get('updated_at'))}")
    st.write(f"决策时间：{_display_value(approval.get('decided_at'))}")
    st.write(f"决策理由：{_safe_text(approval.get('decision_reason'))}")
    st.write(f"重新规划次数：{_display_value(approval.get('replan_count'))}")
    st.write(f"步骤位置：{_display_value(approval.get('step_position'))}")
    st.write("拟执行动作（不可变）：")
    proposed = approval.get("proposed_action")
    if isinstance(proposed, dict):
        st.json(_safe_object(proposed))
    else:
        st.write("未记录")

    role = str(st.session_state.get("taskpilot_identity", {}).get("role", "")).lower()
    pending = str(approval.get("status", "")).lower() == "pending"
    if role not in {"owner", "admin"}:
        st.info(
            "成员可以查看审批，但没有决策权限。Members can inspect approvals but cannot decide them."
        )
        return
    if not pending:
        st.info("此审批已经完成决策。")
        return

    reason = st.text_area(
        "决策理由（可选，最多 500 个字符）",
        key=f"taskpilot_approval_reason_{approval_id}",
        max_chars=500,
    )
    disabled = bool(st.session_state.get("taskpilot_mutation_in_flight"))
    approve, reject = st.columns(2)
    approve_clicked = approve.button("同意", key="taskpilot_approve", disabled=disabled)
    reject_clicked = reject.button("拒绝", key="taskpilot_reject", disabled=disabled)
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
            st.success(
                "审批决定已记录；它不会直接执行或恢复这条执行记录。 "
                "Approval decision recorded; it does not execute or resume this run."
            )
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
    st.subheader("本次执行的审批")
    if st.button("刷新审批", key="taskpilot_refresh_approvals"):
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
        st.info("本次执行不需要审批。")
        return
    labels = {str(item.get("id")): _approval_label(item) for item in approvals}
    selected = st.session_state.get("taskpilot_selected_approval_id")
    if selected is not None and str(selected) not in labels:
        st.session_state.pop("taskpilot_selected_approval_id", None)
        st.session_state.pop("taskpilot_approval", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    approval_id = st.selectbox(
        "审批记录",
        options=list(labels),
        index=index,
        format_func=lambda value: labels[value],
        key="taskpilot_selected_approval_id",
        placeholder="Select an approval",
    )
    if approval_id is not None:
        _render_approval_detail(client, task_id, run_id, str(approval_id))


def _render_trace(client: TaskPilotClient, task_id: str, run_id: str) -> None:
    with st.expander("技术追踪（开发者视图）", expanded=False):
        _render_trace_details(client, task_id, run_id)


def _render_trace_details(client: TaskPilotClient, task_id: str, run_id: str) -> None:
    st.caption("这里展示受限的执行证据；内部标识仅用于调试。")
    limit = st.selectbox("追踪记录上限", options=[100, 500], key="taskpilot_trace_limit")
    if st.button("刷新技术追踪", key="taskpilot_refresh_trace"):
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


def _render_dashboard_summary(tasks: list[dict[str, Any]]) -> None:
    """Render the lightweight landing dashboard from the already-fetched tasks."""

    counts = {
        "total": len(tasks),
        "active": sum(
            _run_status(task.get("status")) in {"PENDING", "QUEUED", "RUNNING"} for task in tasks
        ),
        "completed": sum(_run_status(task.get("status")) == "SUCCEEDED" for task in tasks),
        "draft": sum(_run_status(task.get("status")) == "DRAFT" for task in tasks),
    }
    dashboard = st.columns(4)
    dashboard[0].metric("分析任务", counts["total"])
    dashboard[1].metric("进行中", counts["active"])
    dashboard[2].metric("已完成", counts["completed"])
    dashboard[3].metric("待开始", counts["draft"])
    if not tasks:
        st.info(
            "欢迎来到 AI 遥感分析工作台。创建第一个分析任务，系统会比较不同时段的 NDVI、NDWI 或 NDBI 指数。"
            "（No tasks yet.）"
        )
    else:
        st.caption("选择一个分析任务以查看执行状态、遥感结果和可追溯证据。")


def _render_tasks(client: TaskPilotClient) -> None:
    st.header("AI 遥感分析工作台")
    st.caption("从分析区域和时间范围出发，查看可解释的 GeoChange 指数变化结果。")
    _render_conversation(client)
    mutation_in_flight = bool(st.session_state.get("taskpilot_mutation_in_flight"))
    with st.form("taskpilot_create"):
        st.markdown("#### 新建分析任务")
        title = st.text_input(
            "任务名称",
            key="taskpilot_new_title",
            placeholder="例如：东湖 NDVI / NDWI / NDBI 变化分析",
        )
        description = st.text_area(
            "分析需求（可选）",
            key="taskpilot_new_description",
            placeholder="例如：比较 2023 年和 2024 年 7 月的 NDVI、NDWI 或 NDBI 变化。",
        )
        submitted = st.form_submit_button(
            "创建分析任务",
            key="FormSubmitter:taskpilot_create-Create task",
            disabled=mutation_in_flight,
        )
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
    st.button("刷新分析任务", key="taskpilot_refresh")
    try:
        with st.spinner("正在加载分析任务…"):
            tasks = client.list_tasks()
        st.session_state.taskpilot_tasks = tasks
    except TaskPilotClientError as error:
        _handle_error(error)
        return
    _render_dashboard_summary(tasks)
    if not tasks:
        return
    labels = {str(task.get("id")): _task_value(task, "title", "Untitled task") for task in tasks}
    selected = st.session_state.get("taskpilot_selected_task_id")
    if selected is not None and str(selected) not in labels:
        clear_task_descendants()
        st.session_state.pop("taskpilot_selected_task_id", None)
        selected = None
    index = list(labels).index(str(selected)) if selected is not None else None
    task_id = st.selectbox(
        "分析任务",
        options=list(labels),
        index=index,
        format_func=lambda value: labels[value],
        key="taskpilot_selected_task_id",
        placeholder="选择一个分析任务",
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
            st.info("该分析任务已不可用。")
        _handle_error(error)
        return
    st.subheader(_task_value(detail, "title", "Untitled task"))
    st.markdown("#### 分析需求")
    st.write(_task_value(detail, "description", "未提供分析需求。"))
    st.caption(
        f"任务状态：{_human_status_zh(detail.get('status'))} · "
        f"Task status: {_human_status(detail.get('status'))} · "
        f"创建于：{_task_value(detail, 'created_at')} · 更新于：{_task_value(detail, 'updated_at')} · "
        f"Updated: {_task_value(detail, 'updated_at')}"
    )
    _render_run_view(client, str(task_id), detail)


def _render_conversation(client: TaskPilotClient) -> None:
    """Offer a review-first conversational entry point over the existing API."""

    with st.expander("用自然语言描述分析需求", expanded=True):
        st.caption("Agent 只生成提案；确认后才会创建草稿任务，执行仍由任务页面控制。")
        with st.form("taskpilot_conversation"):
            message = st.text_area(
                "你想分析什么？",
                key="taskpilot_conversation_message",
                placeholder="例如：帮我分析武汉东湖最近几年植被有没有变化。",
            )
            submitted = st.form_submit_button("生成任务提案")
        if submitted and message.strip():
            try:
                response = client.converse(message)
                st.session_state.taskpilot_conversation_response = response
            except TaskPilotClientError as error:
                _handle_error(error)
        response = st.session_state.get("taskpilot_conversation_response")
        if isinstance(response, dict):
            kind = response.get("kind")
            if kind in {"proposal", "clarification"} and isinstance(response.get("proposal"), dict):
                proposal = response["proposal"]
                st.info(response.get("message", "请确认任务提案。"))
                st.json(proposal)
                st.caption(
                    "请选择两个明确、先后不重叠的比较时段；年份或‘最近几年’不能代替比较时段。"
                )
                dates = [
                    st.date_input(label, value=None, key=f"taskpilot_intent_date_{index}")
                    for index, label in enumerate(
                        ("时段 A 开始", "时段 A 结束", "时段 B 开始", "时段 B 结束")
                    )
                ]
                if st.button("确认并创建草稿任务", key="taskpilot_confirm_proposal"):
                    if any(value is None for value in dates):
                        st.error("请完整填写两个比较时段。")
                        return
                    proposal = dict(proposal)
                    proposal["period_a"] = {"start": str(dates[0]), "end": str(dates[1])}
                    proposal["period_b"] = {"start": str(dates[2]), "end": str(dates[3])}
                    try:
                        created = client.confirm_conversation_task(proposal)
                        st.session_state.taskpilot_conversation_response = None
                        clear_task_descendants()
                        st.session_state.taskpilot_selected_task_id = created.get("result", {}).get(
                            "task_id"
                        )
                        st.rerun()
                    except TaskPilotClientError as error:
                        _handle_error(error)
            elif kind == "history":
                st.info(response.get("message", "历史分析任务"))
                for item in response.get("tasks", []):
                    if isinstance(item, dict):
                        st.write(
                            f"{item.get('title', '未命名')} · "
                            f"{_human_status_zh(item.get('status'))} · "
                            f"{item.get('created_at', '')}"
                        )
            elif kind == "result" and isinstance(response.get("result"), dict):
                result = response["result"]
                if result.get("task_id"):
                    st.session_state.taskpilot_selected_task_id = str(result["task_id"])
                if result.get("task_run_id"):
                    st.session_state.taskpilot_selected_run_id = str(result["task_run_id"])
                st.success("已找到历史结果，请在下方任务详情中查看可视化。")
                st.caption(
                    f"任务：{result.get('task_title', '')} · Run：{result.get('task_run_id', '')}"
                )


def render_product() -> None:
    """Render Product view and keep all state session-local."""

    client = _client()
    st.markdown(
        """
    <style>
    .block-container { max-width: 1180px; padding-top: 2rem; }
    [data-testid="stMetric"] { background: #f6f8fa; border: 1px solid #e5e7eb; padding: .7rem; border-radius: .5rem; }
    </style>
    """,
        unsafe_allow_html=True,
    )
    st.title("TaskPilot · AI 遥感分析工作台")
    st.caption("面向非技术用户的遥感指数变化分析：结果、图像和证据一目了然。")
    if st.session_state.get("taskpilot_identity") is None:
        if st.session_state.get("taskpilot_org_ids"):
            _organization_login()
        else:
            _login()
        return
    with st.sidebar:
        identity = st.session_state.get("taskpilot_identity", {})
        st.markdown("### 工作台导航")
        st.write(f"当前工作空间：{identity.get('organization_id', '')}")
        st.caption("分析任务 → 执行记录 → 分析结果 → 技术证据")
        if st.button("退出登录", key="taskpilot_logout"):
            try:
                client.logout()
            except TaskPilotClientError:
                st.warning(
                    "本地会话已清除，但服务器退出状态暂时无法确认。"
                    " Local session cleared; server sign-out could not be confirmed."
                )
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
            st.info("账号或工作空间已变化，请重新登录。 Account or organization changed.")
            return
        st.session_state.taskpilot_identity = identity
        _render_tasks(client)
    except TaskPilotClientError as error:
        _handle_error(error)


__all__ = ["clear_task_descendants", "render_product"]
