"""GeoChange extension of the existing bounded evaluation runner and metrics."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from pydantic import ValidationError

from core.settings import settings
from geochange import runtime_caps
from geochange.aoi import resolve_aoi
from geochange.models import GeoChangeTask
from runtime import AgentState, CapabilityDispatcher, PlannerNode, build_runtime_graph
from runtime.executor import ExecutionResult
from runtime.graph import RuntimeObservation

from .metrics import pass_rate
from .runner import CaseResult

SEQUENCE = ("resolve_aoi", "search_sentinel2", "compute_vegetation_change", "summarize_change")
CASE_IDS = (
    "geochange.normal_success", "geochange.parameters", "geochange.unsupported_aoi",
    "geochange.invalid_config", "geochange.replan_success", "geochange.replan_exhausted",
    "geochange.incomplete_evidence", "geochange.artifact_completeness",
)


def task(**overrides: Any) -> GeoChangeTask:
    values = {
        "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
        "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
    }
    values.update(overrides)
    return GeoChangeTask.model_validate(values)


class _Planner:
    def __init__(self, malformed: bool = False) -> None:
        self.calls = 0
        self.malformed = malformed

    async def __call__(self, request: Any) -> object:
        self.calls += 1
        return {"steps": []} if self.malformed else {
            "steps": [{"position": index, "instruction": name} for index, name in enumerate(SEQUENCE, 1)]
        }


class _Observations:
    def __init__(self) -> None:
        self.events: list[RuntimeObservation] = []

    async def record(self, observation: RuntimeObservation) -> None:
        self.events.append(observation)


class _BrokenSearch(runtime_caps.SearchSentinel2RuntimeCapability):
    def __init__(self, *, incomplete: bool = False) -> None:
        self.incomplete = incomplete

    async def execute(self, step: Any, context: Any) -> ExecutionResult:
        if not self.incomplete:
            return ExecutionResult(step_position=step.position, success=False,
                                   error_code="geochange_quality_failed", error_message="fixture quality failure")
        result = await super().execute(step, context)
        evidence = json.loads(result.output or "{}")
        evidence.pop("period_b_nir")
        return self._result(step, evidence)


async def _execute(root: Path, configuration: GeoChangeTask | None = None, *,
                   replan: bool = False, exhausted: bool = False,
                   incomplete: bool = False, malformed: bool = False):
    planner, observations = _Planner(malformed), _Observations()
    capabilities = cast(Any, runtime_caps.runtime_capabilities())
    if exhausted or incomplete:
        capabilities["search_sentinel2"] = _BrokenSearch(incomplete=incomplete)
    graph = build_runtime_graph(
        MemorySaver(), planner=PlannerNode(planner),
        capability_dispatcher=CapabilityDispatcher(capabilities),
    )
    initial = AgentState.initial(task_id=uuid4(), task_run_id=uuid4(),
                                 title="Wuhan East Lake vegetation change", description="July 2023 versus July 2024")
    if configuration is not None:
        initial.geochange_task = configuration
    with patch.object(settings, "GEOCHANGE_LIVE_STAC", False), \
         patch.object(settings, "GEOCHANGE_LIVE_LLM", False), \
         patch.object(settings, "USE_FAKE_MODEL", True), \
         patch.object(settings, "GEOCHANGE_TEST_REPLAN", replan), \
         patch.object(runtime_caps, "ARTIFACT_ROOT", root):
        raw = await graph.ainvoke(initial.model_dump(mode="json"),
                                 config={"configurable": {"thread_id": initial.task_run_id}},
                                 context={"observation_sink": observations})
    state = AgentState.model_validate(raw)
    payload = json.loads(state.execution_result.output) if state.execution_result and state.execution_result.output else {}
    return state, payload, planner, observations.events


def _case(case_id: str, checks: dict[str, bool]) -> CaseResult:
    passed = all(checks.values())
    return CaseResult(case_id=case_id, case_version=1, status="pass" if passed else "fail",
                      evidence_codes=tuple(sorted(key for key, value in checks.items() if value)),
                      failure_code=None if passed else "assertion_failed")


async def run_geochange_evaluation() -> tuple[CaseResult, ...]:
    """Run eight cases; reuse CaseResult and pass_rate without altering frozen V1 reports."""
    cases: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="taskpilot-geochange-eval-") as directory:
        root = Path(directory)
        state, payload, _, events = await _execute(root)
        sequence = tuple(e.tool_name for e in events if e.tool_name and e.tool_status == "succeeded")
        cases.append(_case(CASE_IDS[0], {
            "plan_valid": state.plan is not None and len(state.plan.steps) == 4,
            "capability_sequence": sequence == SEQUENCE,
            "verifier_correct": payload.get("verifier_status") == "passed",
            "terminal_correct": state.terminal_outcome == "SUCCEEDED",
            "task_parsed": state.geochange_task == task(),
        }))
        configured = task(period_a={"start": "2023-07-01", "end": "2023-07-31"},
                          period_b={"start": "2024-07-01", "end": "2024-07-31"},
                          cloud_threshold=30, decline_threshold=-0.01)
        other, output, _, _ = await _execute(root, configured)
        evidence = output.get("selected_scene_evidence", {})
        cases.append(_case(CASE_IDS[1], {
            "periods_propagated": evidence.get("period_a_date") == "2023-07-28" and evidence.get("period_b_date") == "2024-07-30",
            "cloud_propagated": evidence.get("period_a_cloud_cover") == "22.900553" and evidence.get("period_b_cloud_cover") == "8.247093",
            "threshold_propagated": output.get("metrics", {}).get("decline_threshold") == -0.01 and output.get("metrics", {}).get("decline_percentage") != payload.get("metrics", {}).get("decline_percentage"),
            "terminal_correct": other.terminal_outcome == "SUCCEEDED",
        }))
        rejected = 0
        for action in (lambda: task(aoi_key="unsupported"), lambda: resolve_aoi("unsupported")):
            try:
                action()
            except (ValidationError, ValueError):
                rejected += 1
        cases.append(_case(CASE_IDS[2], {"unsupported_rejected": rejected == 2}))
        invalid = 0
        for overrides in ({"cloud_threshold": 101}, {"decline_threshold": 1},
                          {"period_a": {"start": "2024-07-01", "end": "2023-07-01"}}):
            try:
                task(**overrides)
            except ValidationError:
                invalid += 1
        bad, _, planner, _ = await _execute(root, malformed=True)
        cases.append(_case(CASE_IDS[3], {"configuration_rejected": invalid == 3,
                                       "plan_rejected": planner.calls == 2 and bad.plan is None,
                                       "terminal_correct": bad.terminal_outcome == "FAILED"}))
        recovered, output, planner, events = await _execute(root, replan=True)
        cases.append(_case(CASE_IDS[4], {
            "quality_failure_observed": any(e.error_code == "geochange_quality_failed" for e in events),
            "replacement_plan": planner.calls == 2,
            "recovery_correct": recovered.replan_count == 1 and recovered.terminal_outcome == "SUCCEEDED",
            "verifier_correct": output.get("verifier_status") == "passed",
        }))
        exhausted, _, planner, events = await _execute(root, exhausted=True)
        cases.append(_case(CASE_IDS[5], {
            "budget_correct": exhausted.replan_count == 1 and planner.calls == 2,
            "terminal_correct": exhausted.terminal_outcome == "FAILED" and exhausted.failure is not None and exhausted.failure.code == "replan_budget_exhausted",
        }))
        incomplete, _, _, events = await _execute(root, incomplete=True)
        cases.append(_case(CASE_IDS[6], {
            "verifier_correct": any(e.tool_status == "failed" and e.error_code == "geochange_provenance_invalid" for e in events),
            "terminal_correct": incomplete.terminal_outcome == "FAILED",
        }))
        artifacts = payload.get("artifacts", {})
        artifact_dir = root / state.task_id / state.task_run_id
        cases.append(_case(CASE_IDS[7], {
            "artifact_complete": set(artifacts) == {"ndvi_before", "ndvi_after", "ndvi_change"} and all((artifact_dir / f"{name}.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for name in artifacts),
            "evidence_complete": len(payload.get("selected_scene_evidence", {})) == 13 and bool(payload.get("metrics")) and bool(payload.get("summary")),
            "payload_bounded": len(json.dumps(payload)) <= 2000 and not any(key in payload for key in ("checkpoint", "raw_model", "raw_stac", "prompt", "path")),
        }))
    return tuple(sorted(cases, key=lambda case: case.case_id))


def main() -> int:
    cases = asyncio.run(run_geochange_evaluation())
    measured = pass_rate(cases)
    print("MVP deterministic evaluation evidence")
    print(json.dumps({"suite_id": "taskpilot.geochange.v1", "cases": [c.model_dump() for c in cases],
                      "pass_rate": measured.model_dump()}, indent=2))
    return 0 if all(case.status == "pass" for case in cases) else 1


if __name__ == "__main__":
    raise SystemExit(main())
