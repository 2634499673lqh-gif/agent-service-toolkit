"""Cheap provider/network/database-free CI smoke Evaluation."""

from __future__ import annotations

import asyncio
import sys

from .fixtures import DEFAULT_FIXTURES
from .report import build_machine_report, serialize_report
from .runner import EvaluationRunner

SMOKE_CASE_IDS = (
    "deterministic.fixture_baseline",
    "recovery.retry_then_pass",
    "approval.l2_requires_approval",
)


def smoke_suite():
    return DEFAULT_FIXTURES


async def run_smoke():
    run = await EvaluationRunner(smoke_suite(), case_ids=SMOKE_CASE_IDS).run()
    report = build_machine_report(run)
    return run, report


def smoke_exit_code(run, report) -> int:
    runner_status = getattr(run.runner_status, "value", run.runner_status)
    if runner_status in {"error", "partial"}:
        return 1
    if any(case.status != "pass" for case in run.cases):
        return 1
    comparison_status = getattr(report.comparison.status, "value", report.comparison.status)
    if comparison_status == "incomparable":
        return 1
    return 0


def main() -> int:
    run, report = asyncio.run(run_smoke())
    try:
        sys.stdout.buffer.write(serialize_report(report))
    except ValueError:
        return 1
    return smoke_exit_code(run, report)


if __name__ == "__main__":
    raise SystemExit(main())

__all__ = ["SMOKE_CASE_IDS", "main", "run_smoke", "smoke_exit_code", "smoke_suite"]
