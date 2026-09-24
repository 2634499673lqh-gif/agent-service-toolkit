from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy.ext.asyncio import AsyncSession

from persistence.models import Task, TaskRun, TaskRunStatus, TaskStatus
from runtime import DeterministicExecutor
from service.logging import reset_request_id, set_request_id
from service.task_runtime import TaskRuntimeService

ORG = UUID("11111111-1111-4111-8111-111111111111")
TASK_ID = UUID("22222222-2222-4222-8222-222222222222")
RUN_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")


class FakeRuns:
    def __init__(self, task: Task, run: TaskRun) -> None:
        self.task = task
        self.run = run

    async def get_task_and_run_in_principal_tenant(self, task_id, run_id, organization_id):
        if (
            task_id != self.task.id
            or run_id != self.run.id
            or organization_id != self.task.organization_id
        ):
            return None
        return self.task, self.run


class FakeLifecycle:
    def __init__(self, task: Task, run: TaskRun) -> None:
        self.begin_run = AsyncMock(side_effect=self._begin)
        self.succeed_run = AsyncMock(side_effect=self._succeed)
        self.fail_run = AsyncMock(side_effect=self._fail)
        self.task = task
        self.run = run

    async def _begin(self, *_args):
        self.task.status = TaskStatus.RUNNING
        self.run.status = TaskRunStatus.RUNNING

    async def _succeed(self, *_args):
        self.task.status = TaskStatus.SUCCEEDED
        self.run.status = TaskRunStatus.SUCCEEDED

    async def _fail(self, *_args):
        self.task.status = TaskStatus.FAILED
        self.run.status = TaskRunStatus.FAILED


class CapturingAgentRuns:
    rows: list[object] = []

    def __init__(self, _session) -> None:
        self.rows = CapturingAgentRuns.rows

    async def add(self, row, _organization_id):
        if row.id is None:
            row.id = uuid4()
        self.rows.append(row)
        return row


class CapturingToolCalls:
    rows: list[object] = []

    def __init__(self, _session) -> None:
        self.rows = CapturingToolCalls.rows

    async def add(self, row, _organization_id):
        if row.id is None:
            row.id = uuid4()
        self.rows.append(row)
        return row


def _pair() -> tuple[Task, TaskRun]:
    task = Task(
        id=TASK_ID,
        organization_id=ORG,
        created_by_user_id=USER_ID,
        title="Correlate this task",
        description="Keep the durable IDs separate.",
        status=TaskStatus.QUEUED,
    )
    return task, TaskRun(id=RUN_ID, task_id=TASK_ID, run_number=1, status=TaskRunStatus.PENDING)


def _session() -> AsyncSession:
    session = Mock(spec=AsyncSession)
    session.rollback = AsyncMock()
    session.commit = AsyncMock()
    return session


async def _execute(*, executor=None, request_id: UUID | None = None):
    task, run = _pair()
    lifecycle = FakeLifecycle(task, run)
    CapturingAgentRuns.rows = []
    CapturingToolCalls.rows = []
    token = None if request_id is None else set_request_id(str(request_id))
    try:
        with (
            patch("service.task_runtime.TaskRunRepository", return_value=FakeRuns(task, run)),
            patch("service.task_runtime.TaskLifecycleService", return_value=lifecycle),
            patch("service.task_runtime.AgentRunRepository", CapturingAgentRuns),
            patch("service.task_runtime.ToolCallRepository", CapturingToolCalls),
        ):
            result = await TaskRuntimeService(MemorySaver(), executor=executor).execute_run(
                _session(),
                organization_id=ORG,
                task_id=TASK_ID,
                task_run_id=RUN_ID,
            )
    finally:
        if token is not None:
            reset_request_id(token)
    return result, lifecycle, CapturingAgentRuns.rows, CapturingToolCalls.rows


@pytest.mark.asyncio
async def test_t093_success_wires_request_task_run_step_agent_and_tool() -> None:
    request_id = UUID("55555555-5555-4555-8555-555555555555")
    result, lifecycle, agents, calls = await _execute(request_id=request_id)

    assert result.terminal_outcome == "SUCCEEDED"
    assert lifecycle.succeed_run.await_count == 1
    assert len(agents) == len(calls) == 1
    agent = agents[0]
    call = calls[0]
    assert agent.request_id == request_id
    assert agent.task_run_id == RUN_ID
    assert (agent.replan_count, agent.step_position, agent.retry_count) == (0, 0, 0)
    assert agent.agent_name == "executor"
    assert call.agent_run_id == agent.id
    assert call.call_index == 0
    assert call.tool_name == "deterministic_fixture"
    assert not hasattr(agent, "task_id")
    assert not hasattr(agent, "organization_id")
    assert not hasattr(call, "task_id")
    assert not hasattr(call, "organization_id")


@pytest.mark.asyncio
async def test_t093_background_work_keeps_request_id_null_and_retry_coordinate() -> None:
    result, _lifecycle, agents, calls = await _execute(
        executor=DeterministicExecutor(failure_mode="fail_once")
    )

    assert result.state.retry_count == 1
    assert [agent.request_id for agent in agents] == [None, None]
    assert [(agent.replan_count, agent.step_position, agent.retry_count) for agent in agents] == [
        (0, 0, 0),
        (0, 0, 1),
    ]
    assert [call.agent_run_id for call in calls] == [agent.id for agent in agents]
