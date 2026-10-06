"""The small static Planner → Capability → Verifier TaskPilot graph."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, runtime_checkable
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.llm import get_model
from core.settings import settings
from geochange.artifacts import load_dynamic_evidence
from geochange.llm import GeoChangeLLM, extract_explicit_parameters
from geochange.models import GeoChangeTask
from geochange.skill import (
    URBAN_CHANGE_NDBI,
    VEGETATION_CHANGE_NDVI,
    WATER_CHANGE_NDWI,
    SkillSpec,
    SkillValidationError,
    validate_terminal_result,
    validate_unconfirmed_geochange_route,
)
from schema.planner import PlanStep

from .capabilities import DeterministicFixtureCapability
from .capability import CapabilityDispatcher, CapabilityMetadata
from .context import ContextBuilder, ContextEnvelope
from .executor import (
    RUNTIME_OUTPUT_MAX_LENGTH,
    TRUSTED_DYNAMIC_RUNTIME_OUTPUT_MAX_LENGTH,
    ExecutionResult,
    Executor,
    TrustedDynamicExecutionResult,
)
from .failure import FailureClassifier, RuntimeFailure
from .observability import (
    duration_ms,
    normalize_provider_metadata,
    normalize_provider_usage,
    normalize_utc_timestamp,
    sanitize_error_text,
)
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


class RuntimeObservation(BaseModel):
    """One bounded observation emitted at the runtime capability boundary.

    This is an in-process handoff only.  The service that owns the durable
    TaskRun decides whether and when to persist the observation; nothing in
    this model is checkpointed or used as authority.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: UUID | None = None
    task_run_id: str
    replan_count: int = Field(ge=0, le=1)
    step_position: int = Field(ge=0, le=7)
    retry_count: int = Field(ge=0, le=1)
    agent_name: str = Field(min_length=1, max_length=128)
    agent_status: Literal["running", "succeeded", "failed", "waiting_approval", "unknown"]
    tool_name: str | None = Field(default=None, max_length=128)
    call_index: int | None = Field(default=None, ge=0, le=999)
    tool_status: Literal["running", "succeeded", "failed", "unknown"] | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_class: Literal["RETRY", "REPLAN", "TERMINAL", "UNKNOWN"] | None = None
    error_code: str | None = Field(default=None, max_length=64)
    error_message: str | None = Field(default=None, max_length=500)
    usage: dict[str, Any] | None = None
    provider_metadata: dict[str, str] | None = None
    started_at: datetime
    finished_at: datetime | None = None

    @field_validator("task_run_id", mode="before")
    @classmethod
    def canonical_task_run_id(cls, value: object) -> str:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            raise ValueError("observation task_run_id must be a valid UUID") from None

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None, info: Any) -> datetime | None:
        if value is None:
            return None
        return normalize_utc_timestamp(value, info.field_name)

    @field_validator("error_message")
    @classmethod
    def error_message_must_be_sanitized(cls, value: str | None) -> str | None:
        return sanitize_error_text(value, field_name="error_message", maximum=500)

    @field_validator("error_code")
    @classmethod
    def error_code_must_be_bounded(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("error_code must not be blank")
        return sanitize_error_text(value, field_name="error_code", maximum=64)

    @field_validator("usage")
    @classmethod
    def usage_must_be_normalized(cls, value: object) -> dict[str, Any] | None:
        return None if value is None else normalize_provider_usage(value)

    @field_validator("provider_metadata")
    @classmethod
    def provider_metadata_must_be_bounded(cls, value: object) -> dict[str, str] | None:
        return normalize_provider_metadata(value)

    @model_validator(mode="after")
    def finish_must_follow_start(self) -> "RuntimeObservation":
        duration_ms(self.started_at, self.finished_at)
        error_present = any(
            value is not None for value in (self.error_class, self.error_code, self.error_message)
        )
        if self.agent_status in {"running", "succeeded", "waiting_approval"} and error_present:
            raise ValueError("normal AgentRun observations cannot carry error fields")
        if self.tool_status in {"running", "succeeded"} and error_present:
            raise ValueError("normal ToolCall observations cannot carry error fields")
        return self

    @property
    def duration_ms(self) -> int | None:
        """Return bounded duration derived from the two event timestamps."""

        return duration_ms(self.started_at, self.finished_at)


@runtime_checkable
class RuntimeObservationSink(Protocol):
    """Small explicit sink used by the graph; persistence stays in the service."""

    async def record(self, observation: RuntimeObservation) -> None: ...


class RuntimeGraphContext(BaseModel):
    """Per-invocation service callback; values never enter checkpoint state."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    approval_gate: ApprovalGate | None = None
    observation_sink: RuntimeObservationSink | None = None
    request_id: UUID | None = None
    skill: SkillSpec | None = None


class _DefaultPlannerModel:
    async def __call__(self, request: Any) -> object:
        text = f"{request.task_input.title} {request.task_input.description or ''}".casefold()
        skill = (
            VEGETATION_CHANGE_NDVI
            if any(token in text for token in ("vegetation", "ndvi", "植被"))
            else WATER_CHANGE_NDWI
            if any(token in text for token in ("water", "ndwi", "水体", "水域"))
            else URBAN_CHANGE_NDBI
            if any(token in text for token in ("urban", "ndbi", "built-up", "建成区", "城市"))
            else VEGETATION_CHANGE_NDVI
        )
        if any(
            token in text
            for token in (
                "vegetation",
                "ndvi",
                "east lake",
                "东湖",
                "water",
                "ndwi",
                "水体",
                "水域",
                "urban",
                "ndbi",
                "built-up",
                "建成区",
                "城市",
            )
        ):
            return {
                "steps": [
                    {"position": position, "instruction": capability}
                    for position, capability in enumerate(skill.capabilities, 1)
                ]
            }
        return {
            "steps": [
                {"position": 1, "instruction": f"Complete the task: {request.task_input.title}"}
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
    from geochange.runtime_caps import runtime_capabilities

    if capability_dispatcher is not None and executor is not None:
        raise ValueError("executor and capability_dispatcher are mutually exclusive")
    if capability_dispatcher is None:
        if executor is None:
            capability_name = _DEFAULT_CAPABILITY_NAME
            capabilities: dict[str, Any] = {capability_name: DeterministicFixtureCapability()}
            capabilities.update(runtime_capabilities())
            capability_dispatcher = CapabilityDispatcher(
                capabilities,
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

    async def plan_initial(
        state: AgentState,
        runtime: Runtime[RuntimeGraphContext],
    ) -> dict[str, object]:
        runtime_context = getattr(runtime, "context", None)
        if runtime_context is None or runtime_context.skill is None:
            try:
                validate_unconfirmed_geochange_route(
                    state.geochange_task,
                    state.plan,
                    title=state.task_input.title,
                    description=state.task_input.description,
                )
            except SkillValidationError:
                return {"failure": _failure(failure_classifier, "skill_confirmation_required")}
        if state.plan is not None:
            return {"failure": None}
        geochange_task = state.geochange_task
        started_at = datetime.now(UTC)
        runtime_context = getattr(runtime, "context", None)
        observation_sink = getattr(runtime_context, "observation_sink", None)
        request_id = getattr(runtime_context, "request_id", None)
        try:
            plan = await planner_node(state.task_input)
            if runtime_context is not None and runtime_context.skill is not None:
                runtime_context.skill.validate_plan(plan)
            else:
                validate_unconfirmed_geochange_route(
                    state.geochange_task,
                    plan,
                    title=state.task_input.title,
                    description=state.task_input.description,
                )
            if geochange_task is not None or _is_geochange_request(state.task_input):
                if geochange_task is None:
                    if settings.GEOCHANGE_LIVE_LLM and not settings.USE_FAKE_MODEL:
                        geochange_task = await GeoChangeLLM(
                            get_model(settings.DEFAULT_MODEL)
                        ).parse_task(state.task_input.description or state.task_input.title)
                    else:
                        geochange_task = _offline_geochange_task(
                            state.task_input,
                            skill=runtime_context.skill if runtime_context is not None else None,
                        )
                if geochange_task.analysis_type not in {
                    "vegetation_change",
                    "water_change",
                    "urban_change",
                }:
                    raise ValueError("unsupported GeoChange analysis type")
                if runtime_context is None or runtime_context.skill is None:
                    validate_unconfirmed_geochange_route(
                        geochange_task,
                        plan,
                        title=state.task_input.title,
                        description=state.task_input.description,
                    )
        except PlannerOutputInvalidError as error:
            failure = _failure(failure_classifier, error.code)
            if observation_sink is not None:
                await observation_sink.record(
                    _planner_observation(state, request_id, started_at, failure)
                )
            return {"failure": failure}
        except SkillValidationError as error:
            code = (
                "skill_confirmation_required"
                if str(error) == "confirmed intent is required for this Skill"
                else "skill_plan_invalid"
            )
            failure = _failure(failure_classifier, code)
            if observation_sink is not None:
                await observation_sink.record(
                    _planner_observation(state, request_id, started_at, failure)
                )
            return {"failure": failure}
        except Exception:
            failure = _failure(failure_classifier, "planner_execution_failed")
            if observation_sink is not None:
                await observation_sink.record(
                    _planner_observation(state, request_id, started_at, failure)
                )
            return {"failure": failure}
        geochange_task_data = (
            None if geochange_task is None else geochange_task.model_dump(mode="json")
        )
        return {
            "plan": plan.model_dump(mode="json"),
            "geochange_task": geochange_task_data,
            "failure": None,
        }

    async def execute_step(
        state: AgentState,
        runtime: Runtime[RuntimeGraphContext],
    ) -> dict[str, object]:
        started_at = datetime.now(UTC)
        runtime_context = getattr(runtime, "context", None)
        observation_sink = getattr(runtime_context, "observation_sink", None)
        request_id = getattr(runtime_context, "request_id", None)

        async def observe(
            *,
            agent_status: Literal["running", "succeeded", "failed", "waiting_approval", "unknown"],
            context: ContextEnvelope | None = None,
            tool_status: Literal["running", "succeeded", "failed", "unknown"] | None = None,
            result: dict[str, Any] | None = None,
            error: RuntimeFailure | None = None,
            tool_name: str | None = None,
            usage: dict[str, Any] | None = None,
            provider_metadata: dict[str, str] | None = None,
        ) -> None:
            if observation_sink is None:
                return
            if state.plan is None:
                return
            await observation_sink.record(
                RuntimeObservation(
                    request_id=request_id,
                    task_run_id=state.task_run_id,
                    replan_count=state.replan_count,
                    step_position=state.plan_position,
                    retry_count=state.retry_count,
                    agent_name="executor",
                    agent_status=agent_status,
                    tool_name=tool_name,
                    call_index=0 if tool_name is not None else None,
                    tool_status=tool_status,
                    arguments=({} if context is None else context.model_dump(mode="json")),
                    result=result,
                    error_class=None if error is None else error.classification,
                    error_code=None if error is None else error.code,
                    error_message=None if error is None else error.sanitized_message,
                    usage=usage,
                    provider_metadata=provider_metadata,
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
            )

        if runtime_context is None or runtime_context.skill is None:
            try:
                validate_unconfirmed_geochange_route(
                    state.geochange_task,
                    state.plan,
                    title=state.task_input.title,
                    description=state.task_input.description,
                )
            except SkillValidationError:
                failure = _failure(failure_classifier, "skill_confirmation_required")
                await observe(agent_status="failed", error=failure)
                return {"failure": failure}

        if state.execution_result is not None and state.pending_approval is None:
            return {"capability_context": None, "failure": None}
        if state.plan is None or state.plan_position >= len(state.plan.steps):
            return {"failure": _failure(failure_classifier, "runtime_plan_position_invalid")}
        step = state.plan.steps[state.plan_position]
        try:
            context = context_builder.build(
                task_input=state.task_input,
                current_step=step,
                runtime_task_id=state.task_id,
                runtime_task_run_id=state.task_run_id,
                runtime_replan_count=state.replan_count,
                geochange_task=state.geochange_task,
                geochange_aoi_evidence=state.geochange_aoi_evidence,
                geochange_evidence=state.geochange_evidence,
            )
        except Exception:
            await observe(
                agent_status="failed",
                error=_failure(failure_classifier, "capability_context_invalid"),
            )
            return {"failure": _failure(failure_classifier, "capability_context_invalid")}
        selected_capability = capability_name
        instruction_name = step.instruction.strip().split()[0]
        if runtime_context is not None and runtime_context.skill is not None:
            try:
                selected_capability = runtime_context.skill.validate_step(
                    step, plan_position=state.plan_position
                )
            except SkillValidationError:
                failure = _failure(failure_classifier, "skill_step_invalid")
                await observe(agent_status="failed", context=context, error=failure)
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": failure,
                }
        elif (
            instruction_name in VEGETATION_CHANGE_NDVI.capabilities
            or instruction_name in WATER_CHANGE_NDWI.capabilities
        ):
            if not isinstance(capability_dispatcher.metadata_for(instruction_name), RuntimeFailure):
                selected_capability = instruction_name
        metadata = capability_dispatcher.metadata_for(selected_capability)
        if isinstance(metadata, RuntimeFailure):
            await observe(agent_status="failed", context=context, error=metadata)
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure_classifier.classify(metadata.code).model_dump(mode="json"),
            }
        risk_route = classify_action(metadata, context, expected_name=selected_capability)
        if risk_route is RiskRoute.INVALID:
            failure = _failure(failure_classifier, "risk_classifier_invalid")
            await observe(agent_status="failed", context=context, error=failure)
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure,
            }
        if risk_route is RiskRoute.BLOCKED:
            failure = _failure(failure_classifier, "risk_level_blocked")
            await observe(agent_status="failed", context=context, error=failure)
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure,
            }
        if risk_route is RiskRoute.APPROVAL_REQUIRED:
            approval_gate = getattr(runtime_context, "approval_gate", None)
            if approval_gate is None:
                failure = _failure(failure_classifier, "approval_boundary_missing")
                await observe(agent_status="failed", context=context, error=failure)
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": failure,
                }
            try:
                pending_approval = PendingApprovalReference.model_validate(
                    await approval_gate(metadata, context, state)
                )
            except Exception:
                failure = _failure(failure_classifier, "approval_boundary_failed")
                await observe(agent_status="failed", context=context, error=failure)
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": failure,
                }
            if (
                pending_approval.replan_count != state.replan_count
                or pending_approval.step_position != state.plan_position
            ):
                failure = _failure(failure_classifier, "approval_reference_invalid")
                await observe(agent_status="failed", context=context, error=failure)
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "failure": failure,
                }
            await observe(
                agent_status="waiting_approval",
                context=context,
            )
            return {
                "capability_context": None,
                "pending_approval": pending_approval.model_dump(mode="json"),
                "execution_result": None,
                "verification": None,
                "failure": None,
            }
        try:
            raw_result = await capability_dispatcher.dispatch(selected_capability, step, context)
        except Exception:
            failure = _failure(failure_classifier, "capability_execution_failed")
            await observe(
                agent_status="failed",
                context=context,
                tool_status="failed",
                error=failure,
                tool_name=selected_capability,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure,
            }
        if isinstance(raw_result, RuntimeFailure):
            failure = failure_classifier.classify(
                raw_result.code,
                raw_result.sanitized_message,
            )
            await observe(
                agent_status="failed",
                context=context,
                tool_status="failed",
                error=failure,
                tool_name=selected_capability,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure.model_dump(mode="json"),
            }
        try:
            result = (
                raw_result
                if isinstance(raw_result, TrustedDynamicExecutionResult)
                else ExecutionResult.model_validate(raw_result)
            )
        except Exception:
            failure = _failure(failure_classifier, "capability_output_invalid")
            await observe(
                agent_status="failed",
                context=context,
                tool_status="failed",
                error=failure,
                tool_name=selected_capability,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "failure": failure,
            }
        if result.step_position != step.position:
            failure = _failure(failure_classifier, "capability_output_invalid")
            await observe(
                agent_status="failed",
                context=context,
                tool_status="failed",
                result=_observation_result(result),
                error=failure,
                tool_name=selected_capability,
                usage=result.usage,
                provider_metadata=result.provider_metadata,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "execution_result": result.model_dump(mode="json"),
                "failure": failure,
            }
        if (
            selected_capability == "summarize_change"
            and runtime_context is not None
            and runtime_context.skill is not None
            and result.success
        ):
            try:
                candidate = json.loads(result.output or "")
            except (TypeError, ValueError):
                candidate = None
            try:
                if state.geochange_task is None:
                    raise ValueError("trusted GeoChange task is missing")
                if isinstance(result, TrustedDynamicExecutionResult):
                    if (
                        load_dynamic_evidence(state.task_id, state.task_run_id)
                        != result.canonical_evidence
                    ):
                        raise ValueError("dynamic terminal binder is not server-owned")
                validate_terminal_result(
                    runtime_context.skill,
                    candidate,
                    task=state.geochange_task,
                    aoi_evidence=state.geochange_aoi_evidence,
                    scene_evidence_values=state.geochange_evidence,
                    canonical_dynamic_evidence=(
                        result.canonical_evidence
                        if isinstance(result, TrustedDynamicExecutionResult)
                        else None
                    ),
                )
            except (SkillValidationError, ValueError, TypeError):
                failure = _failure(failure_classifier, "skill_result_invalid")
                await observe(
                    agent_status="failed",
                    context=context,
                    tool_status="failed",
                    result=_observation_result(result),
                    error=failure,
                    tool_name=selected_capability,
                )
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "execution_result": result.model_dump(mode="json"),
                    "failure": failure,
                }
        if not result.success:
            failure = failure_classifier.classify(
                result.error_code or "executor_failed", result.error_message
            )
            await observe(
                agent_status="failed",
                context=context,
                tool_status="failed",
                result=_observation_result(result),
                error=failure,
                tool_name=capability_name,
                usage=result.usage,
                provider_metadata=result.provider_metadata,
            )
            return {
                "capability_context": context.model_dump(mode="json"),
                "execution_result": result.model_dump(mode="json"),
                "failure": failure.model_dump(mode="json"),
            }
        await observe(
            agent_status="succeeded",
            context=context,
            tool_status="succeeded",
            result=_observation_result(result),
            tool_name=selected_capability,
            usage=result.usage,
            provider_metadata=result.provider_metadata,
        )
        state_update: dict[str, object] = {
            "capability_context": context.model_dump(mode="json"),
            "execution_result": result.model_dump(mode="json"),
            "pending_approval": None,
            "failure": None,
        }
        if isinstance(result, TrustedDynamicExecutionResult):
            if not isinstance(result.canonical_evidence, dict):
                failure = _failure(failure_classifier, "capability_output_invalid")
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "execution_result": result.model_dump(mode="json"),
                    "failure": failure.model_dump(mode="json"),
                }
            if load_dynamic_evidence(state.task_id, state.task_run_id) != result.canonical_evidence:
                failure = _failure(failure_classifier, "capability_output_invalid")
                return {
                    "capability_context": context.model_dump(mode="json"),
                    "execution_result": result.model_dump(mode="json"),
                    "failure": failure.model_dump(mode="json"),
                }
            state_update["trusted_dynamic_evidence"] = result.canonical_evidence
        parsed_output = _bounded_json_object(
            result.output,
            max_length=(
                TRUSTED_DYNAMIC_RUNTIME_OUTPUT_MAX_LENGTH
                if isinstance(result, TrustedDynamicExecutionResult)
                else RUNTIME_OUTPUT_MAX_LENGTH
            ),
        )
        if parsed_output is not None:
            if selected_capability == "resolve_aoi":
                state_update["geochange_aoi_evidence"] = _bounded_string_map(parsed_output)
            elif selected_capability == "search_sentinel2":
                state_update["geochange_evidence"] = _bounded_string_map(parsed_output)
        return state_update

    async def approval_wait(state: AgentState) -> dict[str, object]:
        """End this graph invocation with a durable pending reference."""

        if state.pending_approval is None:
            return {"failure": _failure(failure_classifier, "approval_reference_invalid")}
        return {}

    async def verify_step(state: AgentState) -> dict[str, object]:
        if state.plan is None or state.execution_result is None:
            return {"failure": _failure(failure_classifier, "runtime_verification_input_invalid")}
        if isinstance(state.execution_result, TrustedDynamicExecutionResult):
            if (
                state.trusted_dynamic_evidence != state.execution_result.canonical_evidence
                or load_dynamic_evidence(state.task_id, state.task_run_id)
                != state.execution_result.canonical_evidence
            ):
                return {"failure": _failure(failure_classifier, "skill_result_invalid")}
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
            "trusted_dynamic_evidence": None,
            "verification": None,
        }

    async def replan_step(
        state: AgentState,
        runtime: Runtime[RuntimeGraphContext],
    ) -> dict[str, object]:
        if state.failure is None:
            return {"failure": _failure(failure_classifier, "replan_without_failure")}
        decision = consume_replan(state.failure, state.replan_count)
        if not decision.replan_allowed:
            return {"failure": _failure(failure_classifier, "replan_budget_exhausted")}
        try:
            replacement_plan = await planner_node(state.task_input)
            runtime_context = getattr(runtime, "context", None)
            if runtime_context is not None and runtime_context.skill is not None:
                runtime_context.skill.validate_plan(replacement_plan)
            else:
                validate_unconfirmed_geochange_route(
                    state.geochange_task,
                    replacement_plan,
                    title=state.task_input.title,
                    description=state.task_input.description,
                )
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
        except SkillValidationError as error:
            code = (
                "skill_confirmation_required"
                if str(error) == "confirmed intent is required for this Skill"
                else "skill_plan_invalid"
            )
            return {"failure": _failure(failure_classifier, code)}
        except Exception:
            return {"failure": _failure(failure_classifier, "planner_execution_failed")}
        return {
            "capability_context": None,
            "geochange_task": state.geochange_task.model_dump(mode="json")
            if state.geochange_task
            else None,
            "geochange_aoi_evidence": state.geochange_aoi_evidence,
            "geochange_evidence": state.geochange_evidence,
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

    def route_after_replan(state: AgentState) -> Literal["executor", "terminal"]:
        return "terminal" if state.failure is not None else "executor"

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
    builder.add_conditional_edges(
        REPLAN_NODE,
        route_after_replan,
        {"executor": "executor", "terminal": TERMINAL_NODE},
    )
    builder.add_edge("advance", "executor")
    builder.add_edge("succeed", END)
    builder.add_edge(TERMINAL_NODE, END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)


def _failure(classifier: FailureClassifier, code: str) -> RuntimeFailure:
    return classifier.classify(code)


def _planner_observation(
    state: AgentState,
    request_id: UUID | None,
    started_at: datetime,
    failure: RuntimeFailure,
) -> RuntimeObservation:
    return RuntimeObservation(
        request_id=request_id,
        task_run_id=state.task_run_id,
        replan_count=state.replan_count,
        step_position=0,
        retry_count=state.retry_count,
        agent_name="planner",
        agent_status="failed",
        error_class=failure.classification,
        error_code=failure.code,
        error_message=failure.sanitized_message,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def _is_geochange_request(task_input: Any) -> bool:
    text = f"{task_input.title} {task_input.description or ''}".casefold()
    return any(
        token in text
        for token in (
            "vegetation",
            "ndvi",
            "east lake",
            "东湖",
            "water",
            "ndwi",
            "水体",
            "水域",
            "urban",
            "ndbi",
            "built-up",
            "建成区",
        )
    )


def _default_geochange_task() -> GeoChangeTask:
    return GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )


def _offline_geochange_task(
    task_input: Any,
    *,
    skill: SkillSpec | None = None,
) -> GeoChangeTask:
    """Build the fake/offline task while preserving T139 text authority."""

    source_text = f"{task_input.title} {task_input.description or ''}"
    explicit = extract_explicit_parameters(source_text)
    updates: dict[str, object] = {}
    if skill is not None and skill.result_type in {"water_change", "urban_change"}:
        updates.update(
            analysis_type=skill.analysis_type,
            indicator=skill.indicator,
            decline_threshold=None,
            decline_threshold_source=None,
        )
    if explicit.cloud_threshold is not None:
        updates.update(cloud_threshold=explicit.cloud_threshold, cloud_threshold_source="user_text")
    if explicit.decline_threshold is not None:
        updates.update(
            decline_threshold=explicit.decline_threshold,
            decline_threshold_source="user_text",
        )
    return GeoChangeTask.model_validate({**_default_geochange_task().model_dump(), **updates})


def _observation_result(result: ExecutionResult) -> dict[str, object]:
    """Keep dynamic terminal observations compact and free of duplicate binders."""

    if isinstance(result, TrustedDynamicExecutionResult):
        return {
            "step_position": result.step_position,
            "success": result.success,
            "output": "[server-owned dynamic terminal result]",
        }
    return result.model_dump(mode="json")


def _bounded_json_object(
    output: str | None,
    *,
    max_length: int = RUNTIME_OUTPUT_MAX_LENGTH,
) -> dict[str, Any] | None:
    if not output or len(output) > max_length:
        return None
    try:
        value = json.loads(output)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _bounded_string_map(value: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(key, str) and isinstance(item, str) and len(key) <= 64 and len(item) <= 256
    }


__all__ = [
    "RuntimeGraphContext",
    "RuntimeObservation",
    "RuntimeObservationSink",
    "build_runtime_graph",
]
