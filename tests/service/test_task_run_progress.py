"""Focused snapshot and terminal-projection regressions for TaskRun progress."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

import service.task_run_progress as progress_module
from persistence.models import TaskRunStatus
from schema.task_run_api import TaskRunResponse
from service.task_run_progress import (
    PHASES,
    ProgressAttemptFenceError,
    ProgressSnapshot,
    TaskRunProgressReporter,
    TaskRunProgressStore,
    load_progress_snapshot,
    progress_snapshot_path,
    project_progress,
)


@pytest.fixture
def progress_root(tmp_path, monkeypatch):
    monkeypatch.setattr(progress_module, "ARTIFACT_ROOT", tmp_path)
    return tmp_path


def _snapshot(task_id, run_id, *, phases=None, replan_count=0):
    phase_state = {phase: "pending" for phase in PHASES}
    if phases is not None:
        phase_state.update(phases)
    return ProgressSnapshot(
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=uuid4(),
        attempt_number=1,
        retry_count=0,
        replan_count=replan_count,
        reset_reason="start",
        phases=phase_state,
        run_updated_at=datetime.now(UTC),
        event_sequence=0,
        last_event="attempt.reset",
        updated_at=datetime.now(UTC),
    )


def _write_snapshot(task_id, run_id, snapshot):
    path = progress_snapshot_path(task_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot.model_dump_json(), encoding="utf-8")
    return path


def _run(status, metadata=None):
    return SimpleNamespace(status=status, result_metadata=metadata)


def _success_metadata():
    return {
        "execution_mode": "real_stac_landsat_local",
        "verifier_status": "passed",
        "replan_count": 0,
        "stage_status": {"planner": "passed", "execution": "passed", "verifier": "passed"},
    }


def test_legacy_task_run_response_defaults_progress_to_null():
    now = datetime.now(UTC)
    response = TaskRunResponse(
        id=uuid4(),
        task_id=uuid4(),
        run_number=1,
        status=TaskRunStatus.SUCCEEDED,
        created_at=now,
        updated_at=now,
    )

    assert response.progress is None
    assert response.model_dump(mode="json")["progress"] is None


@pytest.mark.asyncio
async def test_attempt_leases_pin_late_callbacks_to_the_superseded_identity():
    class Store:
        def __init__(self):
            self.write_attempts = [True, True, False]
            self.events = []

        async def begin_attempt(self, **_kwargs):
            return self.write_attempts.pop(0)

        async def phase_event(self, **event):
            self.events.append(event)
            return False

    store = Store()
    reporter = TaskRunProgressReporter(
        store,
        organization_id=uuid4(),
        task_id=uuid4(),
        task_run_id=uuid4(),
    )
    await reporter.reset_attempt(reset_reason="start", retry_count=0, replan_count=0)
    first = reporter.current_attempt()
    await reporter.reset_attempt(reset_reason="retry", retry_count=1, replan_count=0)
    second = reporter.current_attempt()

    assert first is not None and second is not None
    second_attempt_id = getattr(second, "attempt_id")
    await first.complete_phase("preparation")
    assert store.events[-1]["attempt_id"] != second_attempt_id

    await reporter.reset_attempt(reset_reason="replan", retry_count=1, replan_count=1)
    assert reporter.current_attempt() is None
    assert len(store.events) == 1


@pytest.mark.asyncio
async def test_authority_fence_failure_is_not_swallowed_by_reporter():
    class Store:
        async def begin_attempt(self, **_kwargs):
            raise ProgressAttemptFenceError("authority update failed")

    reporter = TaskRunProgressReporter(
        Store(),
        organization_id=uuid4(),
        task_id=uuid4(),
        task_run_id=uuid4(),
    )
    with pytest.raises(ProgressAttemptFenceError, match="authority update"):
        await reporter.reset_attempt(reset_reason="retry", retry_count=1, replan_count=0)
    assert reporter.current_attempt() is None


def test_missing_corrupt_oversized_and_identity_mismatched_snapshots_are_unavailable(
    progress_root,
):
    task_id, run_id = uuid4(), uuid4()
    assert load_progress_snapshot(task_id, run_id) is None
    path = progress_snapshot_path(task_id, run_id)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-json")
    assert load_progress_snapshot(task_id, run_id) is None

    path.write_bytes(b"x" * 8_193)
    assert load_progress_snapshot(task_id, run_id) is None

    wrong_identity = _snapshot(uuid4(), run_id)
    _write_snapshot(task_id, run_id, wrong_identity)
    assert load_progress_snapshot(task_id, run_id) is None


def test_failed_reset_tombstone_hides_previous_attempt_snapshot(progress_root, monkeypatch):
    task_id, run_id = uuid4(), uuid4()
    path = _write_snapshot(
        task_id,
        run_id,
        _snapshot(task_id, run_id, phases={"planning": "succeeded"}),
    )
    assert load_progress_snapshot(task_id, run_id) is not None

    # Model the failure after reset fencing but before the replacement write.
    TaskRunProgressStore._write_unavailable(path)
    monkeypatch.setattr(
        TaskRunProgressStore,
        "_write",
        staticmethod(lambda *_args: (_ for _ in ()).throw(OSError("disk full"))),
    )
    with pytest.raises(OSError):
        TaskRunProgressStore._write(path, _snapshot(task_id, run_id))

    assert load_progress_snapshot(task_id, run_id) is None
    assert path.read_bytes() == b'{"schema_version":1,"state":"unavailable"}'


def test_phase_map_rejects_non_monotonic_and_forged_finalization(progress_root):
    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "pending" for phase in PHASES}
    phases["planning"] = "pending"
    phases["computation"] = "succeeded"
    with pytest.raises(ValueError, match="completed prefix"):
        _snapshot(task_id, run_id, phases=phases)

    phases = {phase: "succeeded" for phase in PHASES}
    with pytest.raises(ValueError, match="derived from committed TaskRun"):
        _snapshot(task_id, run_id, phases=phases)


def test_running_finalization_never_reaches_100_percent_before_terminal_commit(progress_root):
    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "succeeded" for phase in PHASES[:4]}
    phases["finalization"] = "running"
    _write_snapshot(task_id, run_id, _snapshot(task_id, run_id, phases=phases))

    running = _run(TaskRunStatus.RUNNING, _success_metadata())
    progress = project_progress(task_id, run_id, running)
    assert progress is not None
    assert progress.state == "running"
    assert progress.completed_phase_count == 4
    assert progress.phase_completion_pct == 80
    assert progress.phases[-1].status == "running"


def test_running_run_with_snapshot_older_than_lifecycle_transition_is_unavailable(progress_root):
    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "pending" for phase in PHASES}
    phases["planning"] = "running"
    snapshot = _snapshot(task_id, run_id, phases=phases)
    _write_snapshot(task_id, run_id, snapshot)
    run = _run(TaskRunStatus.RUNNING)
    run.updated_at = snapshot.run_updated_at + timedelta(seconds=1)

    assert project_progress(task_id, run_id, run) is None


def test_superseded_snapshot_is_unavailable_after_new_attempt_succeeds(progress_root):
    """A terminal status cannot authorize a snapshot from an older fence."""

    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "succeeded" for phase in PHASES[:4]}
    phases["finalization"] = "running"
    snapshot = _snapshot(task_id, run_id, phases=phases)
    _write_snapshot(task_id, run_id, snapshot)

    running = _run(TaskRunStatus.RUNNING, _success_metadata())
    running.updated_at = snapshot.run_updated_at
    observed = project_progress(task_id, run_id, running)
    assert observed is not None and observed.phase_completion_pct == 80

    # Attempt B has durably fenced A, but its replacement snapshot failed.
    # A fresh process can still read A's file; terminal projection must not
    # turn that stale history into B's 100% success.
    succeeded = _run(TaskRunStatus.SUCCEEDED, _success_metadata())
    succeeded.updated_at = snapshot.run_updated_at + timedelta(seconds=1)
    assert project_progress(task_id, run_id, succeeded) is None


def test_terminal_progress_requires_provable_attempt_identity(progress_root):
    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "succeeded" for phase in PHASES[:4]}
    _write_snapshot(task_id, run_id, _snapshot(task_id, run_id, phases=phases))

    assert (
        project_progress(task_id, run_id, _run(TaskRunStatus.RUNNING, _success_metadata()))
        is not None
    )
    # The TaskRun status remains authoritative, while its terminal transition
    # has no persisted attempt identity that can bind this optional snapshot.
    assert (
        project_progress(task_id, run_id, _run(TaskRunStatus.SUCCEEDED, _success_metadata()))
        is None
    )

    invalid = _success_metadata()
    invalid["stage_status"] = {"planner": "passed", "execution": "passed", "verifier": "failed"}
    assert project_progress(task_id, run_id, _run(TaskRunStatus.SUCCEEDED, invalid)) is None


@pytest.mark.parametrize(
    "status",
    [TaskRunStatus.FAILED, TaskRunStatus.CANCELLED],
)
def test_failed_and_cancelled_terminal_progress_is_unavailable_without_identity(
    progress_root, status
):
    task_id, run_id = uuid4(), uuid4()
    phases = {phase: "succeeded" for phase in PHASES[:4]}
    phases["finalization"] = "running"
    _write_snapshot(task_id, run_id, _snapshot(task_id, run_id, phases=phases))

    assert project_progress(task_id, run_id, _run(status)) is None
