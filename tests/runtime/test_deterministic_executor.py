import pytest

from runtime import DeterministicExecutor, Executor, PlannerTaskInput
from schema import PlanStep

STEP = PlanStep(position=2, instruction="Inspect the supplied task")
TASK_INPUT = PlannerTaskInput(
    title="Prepare the report",
    description="Use only the task details.",
)


@pytest.mark.asyncio
async def test_deterministic_executor_returns_stable_success() -> None:
    executor = DeterministicExecutor()

    first = await executor.execute(STEP, TASK_INPUT)
    second = await executor.execute(STEP, TASK_INPUT)

    assert first == second
    assert first.step_position == STEP.position
    assert first.success is True
    assert first.output == (
        "Task: Prepare the report\n"
        "Description: Use only the task details.\n"
        "Step 2: Inspect the supplied task"
    )
    assert first.error_code is None
    assert first.error_message is None


@pytest.mark.asyncio
async def test_deterministic_executor_output_is_bounded() -> None:
    executor = DeterministicExecutor()
    long_task_input = PlannerTaskInput(title="Task", description="x" * 5000)

    result = await executor.execute(STEP, long_task_input)

    assert result.success is True
    assert result.output is not None
    assert len(result.output) == 2000


@pytest.mark.asyncio
async def test_fail_once_returns_normalized_failure_then_succeeds() -> None:
    executor = DeterministicExecutor(failure_mode="fail_once")

    first = await executor.execute(STEP, TASK_INPUT)
    second = await executor.execute(STEP, TASK_INPUT)
    third = await executor.execute(STEP, TASK_INPUT)

    assert first.success is False
    assert first.step_position == STEP.position
    assert first.output is None
    assert first.error_code == "deterministic_execution_failed"
    assert first.error_message == "Deterministic execution failed."
    assert second.success is True
    assert third == second


@pytest.mark.asyncio
async def test_always_fail_returns_the_same_normalized_failure() -> None:
    executor = DeterministicExecutor(failure_mode="always_fail")

    results = [await executor.execute(STEP, TASK_INPUT) for _ in range(3)]

    assert results[0] == results[1] == results[2]
    assert all(result.success is False for result in results)
    assert all(result.output is None for result in results)
    assert all(result.error_code == "deterministic_execution_failed" for result in results)


@pytest.mark.asyncio
async def test_production_executor_satisfies_executor_protocol() -> None:
    executor: Executor = DeterministicExecutor()

    result = await executor.execute(STEP, TASK_INPUT)

    assert result.success is True
    assert set(STEP.model_dump()) == {"position", "instruction"}
    assert set(TASK_INPUT.model_dump()) == {"title", "description"}
