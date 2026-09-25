"""Repeatable local runner for the Phase 8 fixture suite."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from runtime import (
    AgentState,
    CapabilityDispatcher,
    CapabilityMetadata,
    ContextBuilder,
    DeterministicExecutor,
    ExecutionResult,
    PendingApprovalReference,
    PlannerNode,
    PlannerRequest,
    VerifierNode,
    build_runtime_graph,
    classify_action,
)
from runtime.context import ContextEnvelope
from runtime.executor import Executor
from schema import PlanStep

from .fixtures import (
    DEFAULT_FIXTURES,
    SUITE_ID,
    SUITE_VERSION,
    EvaluationFixture,
    FixtureSuite,
)


class RunnerStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class FixtureApprovalSignal:
    """An in-memory fixture signal, never a Phase 6 approval authority."""

    case_id: str
    satisfied: Literal[True] = True


class CaseResult(BaseModel):
    """Bounded case result shared by the runner and deterministic metrics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: StrictStr = Field(min_length=1, max_length=64)
    case_version: StrictInt = Field(ge=0)
    status: Literal["pass", "fail", "error"]
    evidence_codes: tuple[StrictStr, ...] = Field(max_length=16)
    failure_code: Literal["assertion_failed", "case_error", "invalid_result"] | None = None

    @field_validator("evidence_codes", mode="before")
    @classmethod
    def canonical_codes(cls, value: object) -> object:
        values = tuple(value) if isinstance(value, (list, tuple)) else value
        if not isinstance(values, tuple):
            raise ValueError("evidence_codes must be an array")
        if len(set(values)) != len(values):
            raise ValueError("evidence_codes must be unique")
        if any(not isinstance(code, str) or not code.strip() for code in values):
            raise ValueError("evidence_codes must contain non-empty strings")
        return tuple(sorted(values))

    @field_validator("case_id")
    @classmethod
    def case_id_utf8_bound(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 64:
            raise ValueError("case_id exceeds the 64-byte bound")
        return value

    @field_validator("evidence_codes")
    @classmethod
    def evidence_utf8_bounds(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value.encode("utf-8")) > 64 for value in values):
            raise ValueError("evidence code exceeds the 64-byte bound")
        return values

    @model_validator(mode="after")
    def status_matches_failure_code(self) -> "CaseResult":
        if (
            (self.status == "pass" and self.failure_code is not None)
            or (self.status == "fail" and self.failure_code != "assertion_failed")
            or (
                self.status == "error" and self.failure_code not in {"case_error", "invalid_result"}
            )
        ):
            raise ValueError("status and failure_code do not match")
        return self


class EvaluationRun(BaseModel):
    """The bounded in-memory result of one suite execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    suite_id: str
    suite_version: int = Field(ge=0)
    runner_status: RunnerStatus
    cases: tuple[CaseResult, ...] = Field(max_length=5)

    @model_validator(mode="after")
    def cases_are_canonical(self) -> "EvaluationRun":
        ordered = tuple(sorted(self.cases, key=lambda case: (case.case_id, case.case_version)))
        if self.cases != ordered or len(
            {(case.case_id, case.case_version) for case in self.cases}
        ) != len(self.cases):
            raise ValueError("case results must be unique and canonically ordered")
        if not self.cases:
            if self.runner_status is not RunnerStatus.ERROR:
                raise ValueError("an empty suite result must be runner error")
            return self
        expected_status = (
            RunnerStatus.PARTIAL
            if any(case.status == "error" for case in self.cases)
            else RunnerStatus.COMPLETED
        )
        if self.runner_status != expected_status:
            raise ValueError("runner_status does not match case results")
        return self

    @classmethod
    def from_suite(
        cls,
        suite: FixtureSuite,
        cases: tuple[CaseResult, ...],
        *,
        runner_status: RunnerStatus,
    ) -> "EvaluationRun":
        return cls(
            suite_id=suite.suite_id,
            suite_version=suite.suite_version,
            runner_status=runner_status,
            cases=cases,
        )


class _FixedPlanner:
    def __init__(self, fixture: EvaluationFixture) -> None:
        self.fixture = fixture

    async def __call__(self, request: PlannerRequest) -> object:
        del request
        return {"steps": [self.fixture.expected_step]}


class _PassVerifier:
    async def __call__(self, request: Any) -> object:
        return {
            "verdict": "PASS",
            "reason": "The deterministic fixture result is valid.",
            "evidence": ["fixture result validated"],
        }


class _ReplanExecutor:
    def __init__(self, output: str) -> None:
        self.output = output
        self.attempts = 0

    async def execute(self, step: PlanStep, task_input: Any) -> ExecutionResult:
        del task_input
        self.attempts += 1
        if self.attempts == 1:
            return ExecutionResult(
                step_position=step.position,
                success=False,
                error_code="recoverable_plan_inadequacy",
                error_message="The initial plan needs one bounded replan.",
            )
        return ExecutionResult(step_position=step.position, success=True, output=self.output)


class _RiskFixtureCapability:
    def __init__(self, risk_level: Literal["L2", "L3"], output: str) -> None:
        self.executions = 0
        self.output = output
        self.metadata = CapabilityMetadata(
            name="deterministic_fixture",
            description="Bounded evaluation fixture capability.",
            risk_level=risk_level,
            read_only=True,
            deterministic=True,
            side_effect_free=True,
        )

    async def execute(self, step: PlanStep, context: object) -> ExecutionResult:
        del context
        self.executions += 1
        return ExecutionResult(step_position=step.position, success=True, output=self.output)


class EvaluationRunner:
    """Execute the fixed suite without a provider, database, or network."""

    def __init__(self, suite: FixtureSuite | None = None) -> None:
        self.suite = suite or DEFAULT_FIXTURES

    async def run(self) -> EvaluationRun:
        try:
            suite = FixtureSuite.model_validate(self.suite)
            if not suite.cases:
                return self._error_run()
        except Exception:
            return self._error_run()
        self.suite = suite
        results: list[CaseResult] = []
        for fixture in self.suite.cases:
            try:
                results.append(await self._run_case(fixture))
            except ValueError:
                results.append(_error_result(fixture, "invalid_result"))
            except Exception:
                # Provider/network payloads are intentionally discarded.  The
                # bounded error code is the only error evidence exposed.
                results.append(_error_result(fixture, "case_error"))
        if not results:
            return self._error_run()
        status = (
            RunnerStatus.PARTIAL
            if any(result.status == "error" for result in results)
            else RunnerStatus.COMPLETED
        )
        return EvaluationRun.from_suite(self.suite, tuple(results), runner_status=status)

    @staticmethod
    def _error_run() -> EvaluationRun:
        return EvaluationRun(
            suite_id=SUITE_ID,
            suite_version=SUITE_VERSION,
            runner_status=RunnerStatus.ERROR,
            cases=(),
        )

    async def _run_case(self, fixture: EvaluationFixture) -> CaseResult:
        if fixture.tag == "baseline":
            return await self._baseline(fixture)
        if fixture.tag == "retry-then-pass":
            return await self._retry(fixture)
        if fixture.tag == "replan-then-pass":
            return await self._replan(fixture)
        if fixture.tag == "l2-approval":
            return await self._approval(fixture, "L2")
        if fixture.tag == "l3-blocked":
            return await self._approval(fixture, "L3")
        raise ValueError("unknown fixture tag")

    async def _graph(
        self,
        fixture: EvaluationFixture,
        *,
        executor: Executor | None = None,
        dispatcher: CapabilityDispatcher[ContextEnvelope] | None = None,
        approval_gate: Callable[..., Awaitable[PendingApprovalReference]] | None = None,
    ) -> AgentState:
        graph = build_runtime_graph(
            MemorySaver(),
            planner=PlannerNode(_FixedPlanner(fixture)),
            executor=executor,
            capability_dispatcher=dispatcher,
            verifier=VerifierNode(_PassVerifier()),
        )
        task_id = uuid4()
        task_run_id = uuid4()
        initial = AgentState.initial(
            task_id=task_id,
            task_run_id=task_run_id,
            title=fixture.input["title"],
            description=fixture.input.get("description"),
        )
        context = None if approval_gate is None else {"approval_gate": approval_gate}
        invoke_kwargs: dict[str, Any] = {
            "config": {"configurable": {"thread_id": str(task_run_id)}},
        }
        if context is not None:
            invoke_kwargs["context"] = context
        raw = await graph.ainvoke(initial.model_dump(mode="json"), **invoke_kwargs)
        return AgentState.model_validate(raw)

    async def _baseline(self, fixture: EvaluationFixture) -> CaseResult:
        state = await self._graph(fixture)
        step = state.plan.steps[0] if state.plan is not None else None
        result = state.execution_result
        evidence: list[str] = []
        if step is not None and step.model_dump(mode="json") == fixture.expected_step:
            evidence.append("capability_selected")
        if result is not None and result.success:
            evidence.append("execution_succeeded")
        if result is not None and result.output == fixture.expected_output:
            evidence.append("output_exact")
        if (
            state.verification is not None
            and state.verification.verdict == fixture.expected_verdict
        ):
            evidence.append("verification_passed")
        return _assert_result(fixture, evidence, state.terminal_outcome == "SUCCEEDED")

    async def _retry(self, fixture: EvaluationFixture) -> CaseResult:
        state = await self._graph(fixture, executor=DeterministicExecutor(failure_mode="fail_once"))
        evidence = ["retry_observed", "recovery_pass"] if state.retry_count == 1 else []
        passed = state.terminal_outcome == "SUCCEEDED" and state.execution_result is not None
        return _assert_result(fixture, evidence, passed)

    async def _replan(self, fixture: EvaluationFixture) -> CaseResult:
        state = await self._graph(fixture, executor=_ReplanExecutor(fixture.expected_output or ""))
        evidence = ["replan_observed", "recovery_pass"] if state.replan_count == 1 else []
        passed = (
            state.terminal_outcome == "SUCCEEDED"
            and state.execution_result is not None
            and state.execution_result.output == fixture.expected_output
        )
        return _assert_result(fixture, evidence, passed)

    async def _approval(self, fixture: EvaluationFixture, risk: Literal["L2", "L3"]) -> CaseResult:
        capability = _RiskFixtureCapability(risk, fixture.expected_output or "")
        dispatcher: CapabilityDispatcher[ContextEnvelope] = CapabilityDispatcher(
            {"deterministic_fixture": capability}
        )
        approval_id = uuid4()

        async def approval_gate(
            metadata: CapabilityMetadata, context: ContextEnvelope, state: AgentState
        ):
            del metadata, context
            return PendingApprovalReference(
                approval_id=approval_id,
                replan_count=state.replan_count,
                step_position=state.plan_position,
            )

        state = await self._graph(fixture, dispatcher=dispatcher, approval_gate=approval_gate)
        step = state.plan.steps[0] if state.plan is not None else None
        evidence: list[str] = []
        if risk == "L3":
            if state.terminal_outcome == "FAILED" and capability.executions == 0:
                evidence.append("blocked_before_execution")
            return _assert_result(fixture, evidence, bool(evidence))

        # The graph has reached the server approval boundary and persisted only
        # a bounded reference. Evaluation requires a separate, explicit local
        # signal before it applies the deterministic mock action; the signal
        # cannot mutate or impersonate durable Phase 6 approval authority.
        context = (
            ContextBuilder().build(
                task_input=state.task_input,
                current_step=step,
            )
            if step is not None
            else None
        )
        route = classify_action(capability.metadata, context, expected_name="deterministic_fixture")
        signal = self._post_approval_signal(fixture, state, capability)
        if state.pending_approval is not None and route.value == "approval_required":
            evidence.append("approval_required")
        if (
            state.pending_approval is not None
            and route.value == "approval_required"
            and signal is not None
            and signal.satisfied
            and signal.case_id == fixture.case_id
        ):
            approved_result = (
                await capability.execute(step, context)
                if step is not None and context is not None
                else None
            )
            if (
                approved_result is not None
                and capability.executions == 1
                and approved_result.success
                and approved_result.output == fixture.expected_output
            ):
                evidence.extend(("approval_satisfied", "execution_succeeded"))
        return _assert_result(fixture, evidence, set(fixture.required_evidence).issubset(evidence))

    @staticmethod
    def _post_approval_signal(
        fixture: EvaluationFixture,
        state: AgentState,
        capability: _RiskFixtureCapability,
    ) -> FixtureApprovalSignal | None:
        """Return the explicit local signal only after a zero-count boundary."""

        if state.pending_approval is None or capability.executions != 0:
            return None
        return FixtureApprovalSignal(case_id=fixture.case_id)


async def run_evaluation(suite: FixtureSuite | None = None) -> EvaluationRun:
    """Convenience entry point for the deterministic V1 suite."""

    return await EvaluationRunner(suite).run()


def _assert_result(fixture: EvaluationFixture, evidence: list[str], passed: bool) -> CaseResult:
    codes = tuple(sorted(set(evidence)))
    if passed and set(fixture.required_evidence).issubset(codes):
        return CaseResult(
            case_id=fixture.case_id,
            case_version=fixture.case_version,
            status="pass",
            evidence_codes=codes,
            failure_code=None,
        )
    return CaseResult(
        case_id=fixture.case_id,
        case_version=fixture.case_version,
        status="fail",
        evidence_codes=codes,
        failure_code="assertion_failed",
    )


def _error_result(
    fixture: EvaluationFixture, code: Literal["case_error", "invalid_result"]
) -> CaseResult:
    return CaseResult(
        case_id=fixture.case_id,
        case_version=fixture.case_version,
        status="error",
        evidence_codes=(),
        failure_code=code,
    )


__all__ = [
    "CaseResult",
    "EvaluationRun",
    "EvaluationRunner",
    "FixtureApprovalSignal",
    "RunnerStatus",
    "run_evaluation",
]
