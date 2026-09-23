"""The small static Planner → Capability → Verifier TaskPilot graph."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict

from schema.planner import PlanStep

from .capabilities import DeterministicFixtureCapability
from .capability import CapabilityDispatcher, CapabilityMetadata
from .context import ContextBuilder, ContextEnvelope
from .executor import ExecutionResult, Executor
from .failure import FailureClassifier, RuntimeFailure
from .planner import PlannerNode, PlannerOutputInvalidError
from .replan import ReplanState, apply_replacement_plan, consume_replan
from .retry import consume_retry
from .risk import RiskRoute, classify_action
from .state import AgentState, PendingApprovalReference
from .verifier import VerifierNode, VerifierOutputInvalidError

RETRY_NODE = "retry"
REPLAN_NODE = "replan"
TERMINAL_NODE = "terminal"
_DEFAULT_CAPABILITY_NAME = "deterministic_fixture"
_EXECUTOR_ADAPTER_NAME = "executor_adapter"

ApprovalGate = Callable[
    [CapabilityMetadata, ContextEnvelope, AgentState],
    Awaitable[PendingApprovalReference],
]


class RuntimeGraphContext(BaseModel):
    """Per-invocation service callback; values never enter checkpoint state."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    approval_gate: ApprovalGate | None = None


class _DefaultPlannerModel:
    async def __call__(self, request: Any) -> object:
        return {
            "steps": [
                {
                    "position": 1,
                    "instruction": f"Complete the task: {request.task_input.title}",
                }
            ]
        }


class _DefaultVerifierModel:
    async def __call__(self, request: Any) -> object:
        return {
            "verdict": "PASS",
            "reason": "The deterministic execution result is valid.",
            "evidence": [f"Step {request.step.position} returned a result."],
        }


class _ExecutorCapabilityAdapter:
    """Keep the approved Phase 4 executor injection behind the dispatcher."""

    metadata = CapabilityMetadata(
        name=_EXECUTOR_ADAPTER_NAME,
        description="Adapts the existing bounded executor for runtime tests.",
        read_only=True,
        deterministic=True,
        side_effect_free=True,
    )

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    async def execute(
        self,
        step: PlanStep,
        context: ContextEnvelope,
    ) -> ExecutionResult:
        return await self._executor.execute(step, context.task_input)


def build_runtime_graph(
    checkpointer: Any,
    *,
    planner: PlannerNode | None = None,
    executor: Executor | None = None,
    capability_dispatcher: CapabilityDispatcher[ContextEnvelope] | None = None,
    verifier: VerifierNode | None = None,
    classifier: FailureClassifier | None = None,
    interrupt_before: list[str] | None = None,
) -> Any:
    """Compile the one bounded graph against an injected LangGraph saver.

    ``interrupt_before`` exists only to let integration tests inspect a real
    mid-run checkpoint. The approval gate is supplied per invocation through
    non-checkpointed runtime context; only its bounded reference enters state.
    """

    planner_node = planner or PlannerNode(_DefaultPlannerModel())
    if capability_dispatcher is not None and executor is not None:
        raise ValueError("executor and capability_dispatcher are mutually exclusive")
    if capability_dispatcher is None:
        if executor is None:
            capability_name = _DEFAULT_CAPABILITY_NAME
            capability_dispatcher = CapabilityDispatcher(
                {capability_name: DeterministicFixtureCapability()},
                classifier=classifier,
            )
        else:
            capability_name = _EXECUTOR_ADAPTER_NAME
            capability_dispatcher = CapabilityDispatcher(
                {capability_name: _ExecutorCapabilityAdapter(executor)},
                classifier=classifier,
            )
    else:
        capability_name = _DEFAULT_CAPABILITY_NAME
    context_builder = ContextBuilder()
    verifier_node = verifier or VerifierNode(_DefaultVerifierModel())
    failure_classifier = classifier or FailureClassifier()

    async def plan_initial(state: AgentState) -> dict[str, object]:
        if state.plan is not None:
            return {"failure": None}
        try:
            plan = await planner_node(state.task_input)
        except PlannerOutputInvalidError as error:
            return {"failure": _failure(failure_classifier, error.code)}
        except Exception:
            return {"failure": _failure(failure_classifier, "planner_execution_failed")}
        return {"plan": plan.model_dump(mode="json"), "failure": None}

    async def execute_step(
        state: AgentState,
        runtime: Runtime[RuntimeGraphContext],
    ) -> dict[str, object]:
        if state.plan is None or state.plan_position >= len(state.plan.steps):
            return {"failure": _failure(failure_classifier, "runtime_plan_position_invalid")}
        step = state.plan.steps[state.plan_position]
        try:
            context = context_builder.build(
                task_input=state.task_input,
                current_step=step,
            )
        except Exception:
            return {"failure": _failure(failure_classifier, "capability_context_invalid")}
        metadata = capability_dispatcher.metadata_for(capability_name)
        if isinstance(metadata, RuntimeFailure):
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure_classifier.classify(metadata.code).model_dump(mode="json"),
            }
        risk_route = classify_action(metadata, context, expected_name=capability_name)
        if risk_route is RiskRoute.INVALID:
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": _failure(failure_classifier, "risk_classifier_invalid"),
            }
        if risk_route is RiskRoute.BLOCKED:
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": _failure(failure_classifier, "risk_level_blocked"),
            }
        if risk_route is RiskRoute.APPROVAL_REQUIRED:
            runtime_context = getattr(runtime, "context", None)
            approval_gate = getattr(runtime_context, "approval_gate", None)
            if approval_gate is None:
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": _failure(failure_classifier, "approval_boundary_missing"),
                }
            try:
                pending_approval = PendingApprovalReference.model_validate(
                    await approval_gate(metadata, context, state)
                )
            except Exception:
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": _failure(failure_classifier, "approval_boundary_failed"),
                }
            if (
                pending_approval.replan_count != state.replan_count
                or pending_approval.step_position != state.plan_position
            ):
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": _failure(failure_classifier, "approval_reference_invalid"),
                }
            return {
                "capability_context": None,
                "pending_approval": pending_approval.model_dump(mode="json"),
                "execution_result": None,
                "verification": None,
                "failure": None,
            }
        try:
            raw_result = await capability_dispatcher.dispatch(capability_name, step, context)
        except Exception:
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": _failure(failure_classifier, "capability_execution_failed"),
            }
        if isinstance(raw_result, RuntimeFailure):
            failure = failure_classifier.classify(
                raw_result.code,
                raw_result.sanitized_message,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure.model_dump(mode="json"),
            }
        try:
            result = ExecutionResult.model_validate(raw_result)
        except Exception:
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": _failure(failure_classifier, "capability_output_invalid"),
            }
        if result.step_position != step.position:
            return {
                "capability_context": context.model_dump(mode="json"),
                "execution_result": result.model_dump(mode="json"),
                "failure": _failure(failure_classifier, "capability_output_invalid"),
            }
        if not result.success:
            failure = failure_classifier.classify(
                result.error_code or "executor_failed", result.error_message
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "execution_result": result.model_dump(mode="json"),
                "failure": failure.model_dump(mode="json"),
            }
        return {
            "capability_context": context.model_dump(mode="json"),
            "execution_result": result.model_dump(mode="json"),
            "pending_approval": None,
            "failure": None,
        }

    async def approval_wait(state: AgentState) -> dict[str, object]:
        """End this graph invocation with a durable pending reference."""

        if state.pending_approval is None:
            return {"failure": _failure(failure_classifier, "approval_reference_invalid")}
        return {}

    async def verify_step(state: AgentState) -> dict[str, object]:
        if state.plan is None or state.execution_result is None:
            return {"failure": _failure(failure_classifier, "runtime_verification_input_invalid")}
        step = state.plan.steps[state.plan_position]
        try:
            result = await verifier_node(
                state.task_input,
                step,
                state.execution_result,
            )
        except VerifierOutputInvalidError as error:
            return {"failure": _failure(failure_classifier, error.code)}
        except Exception:
            return {"failure": _failure(failure_classifier, "verifier_execution_failed")}
        if result.verdict == "FAIL":
            failure = failure_classifier.classify("recoverable_verifier_inadequacy", result.reason)
            return {
                "verification": result.model_dump(mode="json"),
                "failure": failure.model_dump(mode="json"),
            }
        return {"verification": result.model_dump(mode="json"), "failure": None}

    async def retry_step(state: AgentState) -> dict[str, object]:
        if state.failure is None:
            return {"failure": _failure(failure_classifier, "retry_without_failure")}
        decision = consume_retry(state.failure, state.retry_count)
        if not decision.retry_allowed:
            return {"failure": _failure(failure_classifier, "retry_budget_exhausted")}
        return {
            "retry_count": decision.retry_count,
            "execution_result": None,
            "verification": None,
        }

    async def replan_step(state: AgentState) -> dict[str, object]:
        if state.failure is None:
            return {"failure": _failure(failure_classifier, "replan_without_failure")}
        decision = consume_replan(state.failure, state.replan_count)
        if not decision.replan_allowed:
            return {"failure": _failure(failure_classifier, "replan_budget_exhausted")}
        try:
            replacement_plan = await planner_node(state.task_input)
            previous = ReplanState(
                plan=state.plan,
                plan_position=state.plan_position,
                execution_result=state.execution_result,
                verification=state.verification,
                failure=state.failure,
                retry_count=state.retry_count,
                replan_count=state.replan_count,
            )
            replacement = apply_replacement_plan(previous, replacement_plan, decision)
        except PlannerOutputInvalidError as error:
            return {"failure": _failure(failure_classifier, error.code)}
        except Exception:
            return {"failure": _failure(failure_classifier, "planner_execution_failed")}
        return {
            "capability_context": None,
            **replacement.model_dump(mode="json"),
        }

    async def advance_step(state: AgentState) -> dict[str, object]:
        if state.plan is None or state.plan_position + 1 >= len(state.plan.steps):
            return {"failure": _failure(failure_classifier, "runtime_plan_position_invalid")}
        return {
            "plan_position": state.plan_position + 1,
            "capability_context": None,
            "execution_result": None,
            "verification": None,
            "failure": None,
        }

    async def succeed(state: AgentState) -> dict[str, object]:  # noqa: ARG001
        return {"terminal_outcome": "SUCCEEDED", "failure": None}

    async def terminal(state: AgentState) -> dict[str, object]:
        failure = state.failure
        if failure is None:
            failure = _failure(failure_classifier, "runtime_terminal_failure")
        elif failure.classification == "RETRY":
            failure = _failure(failure_classifier, "retry_budget_exhausted")
        elif failure.classification == "REPLAN":
            failure = _failure(failure_classifier, "replan_budget_exhausted")
        return {"terminal_outcome": "FAILED", "failure": failure.model_dump(mode="json")}

    def route_after_plan(state: AgentState) -> Literal["executor", "terminal"]:
        return "terminal" if state.failure is not None else "executor"

    def route_after_execution(
        state: AgentState,
    ) -> Literal["verifier", "retry", "replan", "terminal", "approval_wait"]:
        if state.failure is None and state.pending_approval is not None:
            return "approval_wait"
        if state.failure is None:
            return "verifier"
        return route_failure(state)

    def route_after_verification(
        state: AgentState,
    ) -> Literal["advance", "succeed", "retry", "replan", "terminal"]:
        if state.failure is not None:
            return route_failure(state)
        if state.plan is None or state.verification is None:
            return "terminal"
        if state.plan_position + 1 < len(state.plan.steps):
            return "advance"
        return "succeed"

    def route_failure(state: AgentState) -> Literal["retry", "replan", "terminal"]:
        if state.failure is None:
            return "terminal"
        if state.failure.classification == "RETRY" and state.retry_count < 1:
            return "retry"
        if state.failure.classification == "REPLAN" and state.replan_count < 1:
            return "replan"
        return "terminal"

    builder = StateGraph(AgentState, context_schema=RuntimeGraphContext)
    builder.add_node("planner", plan_initial)
    builder.add_node("executor", execute_step)
    builder.add_node("approval_wait", approval_wait)
    builder.add_node("verifier", verify_step)
    builder.add_node(RETRY_NODE, retry_step)
    builder.add_node(REPLAN_NODE, replan_step)
    builder.add_node("advance", advance_step)
    builder.add_node("succeed", succeed)
    builder.add_node(TERMINAL_NODE, terminal)

    builder.add_edge(START, "planner")
    builder.add_conditional_edges(
        "planner", route_after_plan, {"executor": "executor", "terminal": TERMINAL_NODE}
    )
    builder.add_conditional_edges(
        "executor",
        route_after_execution,
        {
            "verifier": "verifier",
            RETRY_NODE: RETRY_NODE,
            REPLAN_NODE: REPLAN_NODE,
            TERMINAL_NODE: TERMINAL_NODE,
            "approval_wait": "approval_wait",
        },
    )
    builder.add_edge("approval_wait", END)
    builder.add_conditional_edges(
        "verifier",
        route_after_verification,
        {
            "advance": "advance",
            "succeed": "succeed",
            RETRY_NODE: RETRY_NODE,
            REPLAN_NODE: REPLAN_NODE,
            TERMINAL_NODE: TERMINAL_NODE,
        },
    )
    builder.add_edge(RETRY_NODE, "executor")
    builder.add_edge(REPLAN_NODE, "executor")
    builder.add_edge("advance", "executor")
    builder.add_edge("succeed", END)
    builder.add_edge(TERMINAL_NODE, END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def _failure(classifier: FailureClassifier, code: str) -> RuntimeFailure:
    return classifier.classify(code)


__all__ = ["build_runtime_graph"]
