import json

import pytest
from pydantic import ValidationError

from runtime import (
    MAX_CONTEXT_BYTES,
    MAX_CONTEXT_SOURCES,
    AgentState,
    ContextBuilder,
    ContextEnvelope,
    ContextSource,
)
from runtime.planner import PlannerTaskInput
from schema import PlanStep

TASK_INPUT = {
    "title": "Prepare the report",
    "description": "Use the selected task details.",
}
STEP = {"position": 1, "instruction": "Inspect the selected context"}


def _source(index: int = 1, *, content: str = "Relevant bounded text") -> dict[str, str]:
    return {
        "provenance": f"test-source-{index}",
        "selection_reason": "Selected explicitly for the current step.",
        "content": content,
    }


def test_builder_emits_only_the_bounded_json_context_shape() -> None:
    envelope = ContextBuilder().build(
        task_input=TASK_INPUT,
        current_step=STEP,
        sources=[_source()],
    )

    assert envelope.model_dump(mode="json") == {
        "task_input": TASK_INPUT,
        "current_step": STEP,
        "sources": [_source()],
    }
    assert isinstance(envelope.task_input, PlannerTaskInput)
    assert isinstance(envelope.current_step, PlanStep)
    assert json.loads(envelope.model_dump_json()) == envelope.model_dump(mode="json")


def test_builder_preserves_explicit_source_order_without_retrieval() -> None:
    sources = [_source(2), _source(1)]

    envelope = ContextBuilder().build(
        task_input=TASK_INPUT,
        current_step=STEP,
        sources=sources,
    )

    assert [source.provenance for source in envelope.sources] == [
        "test-source-2",
        "test-source-1",
    ]


@pytest.mark.parametrize(
    "invalid_source",
    [
        {**_source(), "extra": "rejected"},
        {**_source(), "organization_id": "tenant-a"},
        {**_source(), "api_key": "secret-token"},
        {**_source(), "content": {"role": "admin"}},
        {**_source(), "content": RuntimeError("do not retain me")},
        {**_source(), "content": "Traceback (most recent call last): secret"},
    ],
)
def test_builder_rejects_extra_authority_secret_and_runtime_values(
    invalid_source: object,
) -> None:
    with pytest.raises(ValidationError):
        ContextBuilder().build(
            task_input=TASK_INPUT,
            current_step=STEP,
            sources=[invalid_source],  # type: ignore[list-item]
        )


def test_builder_rejects_authority_fields_in_task_or_step_snapshots() -> None:
    with pytest.raises(ValidationError):
        ContextBuilder().build(
            task_input={**TASK_INPUT, "organization_id": "tenant-a"},
            current_step=STEP,
        )

    with pytest.raises(ValidationError):
        ContextBuilder().build(
            task_input=TASK_INPUT,
            current_step={**STEP, "provider_client": object()},
        )


def test_context_source_and_envelope_bounds_are_enforced() -> None:
    with pytest.raises(ValidationError):
        ContextSource(
            provenance="p" * 129,
            selection_reason="selected",
            content="content",
        )

    with pytest.raises(ValidationError):
        ContextSource(
            provenance="source",
            selection_reason="r" * 201,
            content="content",
        )

    with pytest.raises(ValidationError):
        ContextSource(
            provenance="source",
            selection_reason="selected",
            content="c" * 1_001,
        )

    with pytest.raises(ValidationError):
        ContextBuilder().build(
            task_input=TASK_INPUT,
            current_step=STEP,
            sources=[_source(index) for index in range(MAX_CONTEXT_SOURCES + 1)],
        )

    with pytest.raises(ValidationError):
        ContextEnvelope(
            task_input=TASK_INPUT,
            current_step=STEP,
            sources={"not": "an ordered JSON sequence"},  # type: ignore[arg-type]
        )


def test_envelope_rejects_serialized_utf8_budget_overflow() -> None:
    with pytest.raises(ValidationError, match="UTF-8 limit"):
        ContextEnvelope(
            task_input=TASK_INPUT,
            current_step=STEP,
            sources=[_source(content="é" * 1_000) for _ in range(MAX_CONTEXT_SOURCES)],
        )

    valid = ContextBuilder().build(task_input=TASK_INPUT, current_step=STEP)
    assert len(valid.model_dump_json().encode("utf-8")) <= MAX_CONTEXT_BYTES


def test_agent_state_checkpoint_contains_only_approved_context_fields() -> None:
    envelope = ContextBuilder().build(
        task_input=TASK_INPUT,
        current_step=STEP,
        sources=[_source()],
    )
    state = AgentState.initial(
        task_id="22222222-2222-4222-8222-222222222222",
        task_run_id="33333333-3333-4333-8333-333333333333",
        title=TASK_INPUT["title"],
        description=TASK_INPUT["description"],
    ).model_copy(update={"capability_context": envelope})

    checkpoint = state.checkpoint_data()
    encoded = json.dumps(checkpoint)
    context_data = checkpoint["capability_context"]

    assert set(context_data) == {"task_input", "current_step", "sources"}  # type: ignore[arg-type]
    assert "organization_id" not in encoded
    assert "api_key" not in encoded
    assert "provider_client" not in encoded
    assert "RuntimeError" not in encoded
    assert json.loads(encoded)["capability_context"] == envelope.model_dump(mode="json")


def test_agent_state_defaults_capability_context_to_null_and_forbids_extra_fields() -> None:
    state = AgentState.initial(
        task_id="22222222-2222-4222-8222-222222222222",
        task_run_id="33333333-3333-4333-8333-333333333333",
        title="Task",
        description=None,
    )

    assert state.capability_context is None
    with pytest.raises(ValidationError):
        AgentState.model_validate(state.checkpoint_data() | {"secret": "token"})
