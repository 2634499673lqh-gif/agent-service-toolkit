"""Deterministic human representation derived solely from MachineReport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .report import MachineReport


def render_human_report(report: MachineReport | Mapping[str, Any]) -> str:
    value = report if isinstance(report, MachineReport) else MachineReport.model_validate(report)
    lines = [
        f"# Evaluation report ({value.schema_})",
        "",
        f"- Suite: `{value.suite_id}` v{value.suite_version}",
        f"- Runner: **{value.runner_status.value}**",
        "",
        "## Cases",
        "",
    ]
    for case in value.cases:
        detail = f" evidence={','.join(case.evidence_codes) or 'none'}"
        if case.failure_code is not None:
            detail += f" failure={case.failure_code}"
        lines.append(f"- **{case.status.upper()}** `{case.case_id}` v{case.case_version}{detail}")
    lines.extend(("", "## Metrics", ""))
    for name in ("pass_rate", "recovery_success", "approval_compliance", "evidence_completeness"):
        metric = value.metrics[name]
        rate = metric.rate if metric.rate is not None else "not_applicable"
        lines.append(
            f"- `{name}`: {metric.status.value if hasattr(metric.status, 'value') else metric.status} {rate} ({metric.numerator}/{metric.denominator})"
        )
    lines.extend(("", "## Comparison", "", f"- Status: **{value.comparison.status.value}**"))
    if value.comparison.reason is not None:
        lines.append(f"- Reason: `{value.comparison.reason.value}`")
    return "\n".join(lines) + "\n"


render_report = render_human_report

__all__ = ["render_human_report", "render_report"]
