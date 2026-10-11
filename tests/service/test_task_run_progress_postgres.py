"""PostgreSQL locking evidence for progress updates versus Task lifecycle transitions."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from psycopg import AsyncConnection, sql
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import service.task_run_progress as progress_module
from geochange.artifacts import (
    artifact_path,
    attempt_artifact_root,
    load_dynamic_evidence,
)
from persistence.engine import create_async_engine
from persistence.models import Organization, Task, TaskRun, TaskRunStatus, TaskStatus, User
from persistence.repositories import TaskRepository
from service.task_lifecycle import TaskLifecycleService
from service.task_run_progress import (
    PHASES,
    ProgressAttemptFenceError,
    TaskRunProgressStore,
    load_progress_snapshot,
    project_progress,
)

pytestmark = pytest.mark.postgres


def _base_url() -> str:
    value = os.environ.get("TASKPILOT_TEST_DATABASE_URL")
    if not value:
        pytest.skip("TASKPILOT_TEST_DATABASE_URL is not configured")
    parsed = make_url(value)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg"}:
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must use PostgreSQL")
    if "test" not in (parsed.database or "").casefold():
        pytest.fail("TASKPILOT_TEST_DATABASE_URL must name a disposable test database")
    return value


@asynccontextmanager
async def _isolated_database(base_url: str) -> AsyncIterator[str]:
    base = make_url(base_url)
    name = f"taskpilot_progress_{uuid4().hex}"
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(
        hide_password=False
    )
    isolated_url = base.set(database=name).render_as_string(hide_password=False)
    async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
        await connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        config = Config(Path(__file__).resolve().parents[2] / "alembic.ini")
        config.attributes["database_url"] = isolated_url
        await asyncio.to_thread(command.upgrade, config, "head")
        yield isolated_url
    finally:
        async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
            await connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
            )


@pytest_asyncio.fixture
async def running_pair(tmp_path, monkeypatch) -> AsyncIterator[dict[str, object]]:
    async with _isolated_database(_base_url()) as database_url:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        org = Organization(id=uuid4(), name="progress tenant")
        user = User(id=uuid4(), email=f"progress-{uuid4()}@example.com", password_hash="hash")
        task = Task(
            id=uuid4(),
            organization_id=org.id,
            created_by_user_id=user.id,
            title="Running Jianghan task",
            status=TaskStatus.RUNNING,
        )
        run = TaskRun(task_id=task.id, run_number=1, status=TaskRunStatus.RUNNING)
        async with factory() as session:
            async with session.begin():
                session.add_all([org, user])
                await session.flush()
                session.add_all([task, run])
        monkeypatch.setattr("service.task_run_progress.ARTIFACT_ROOT", tmp_path)
        try:
            yield {
                "factory": factory,
                "engine": engine,
                "organization_id": org.id,
                "task_id": task.id,
                "task_run_id": run.id,
                "store": TaskRunProgressStore(factory),
            }
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_progress_attempt_resets_identity_and_rejects_foreign_or_wrong_pair_events(
    running_pair,
):
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(store, TaskRunProgressStore)
    assert isinstance(org_id, UUID) and isinstance(task_id, UUID) and isinstance(run_id, UUID)

    first_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    assert await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="planning",
        event="started",
    )
    assert await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="planning",
        event="succeeded",
    )
    sequence = load_progress_snapshot(task_id, run_id).event_sequence
    assert not await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="planning",
        event="started",
    )
    assert load_progress_snapshot(task_id, run_id).event_sequence == sequence

    second_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=second_attempt,
        reset_reason="replan",
        retry_count=0,
        replan_count=1,
    )
    snapshot = load_progress_snapshot(task_id, run_id)
    assert snapshot.attempt_id == second_attempt
    assert snapshot.phases["planning"] == "pending"
    assert not await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="planning",
        event="succeeded",
    )
    retry_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=retry_attempt,
        reset_reason="retry",
        retry_count=1,
        replan_count=1,
        completed_phases=("planning",),
    )
    retry_snapshot = load_progress_snapshot(task_id, run_id)
    assert retry_snapshot.attempt_id == retry_attempt
    assert retry_snapshot.retry_count == 1
    assert retry_snapshot.reset_reason == "retry"
    assert retry_snapshot.phases["planning"] == "succeeded"
    assert retry_snapshot.phases["preparation"] == "pending"
    assert not await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=second_attempt,
        phase="planning",
        event="succeeded",
    )
    assert not await store.phase_event(
        organization_id=uuid4(),
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=retry_attempt,
        phase="planning",
        event="started",
    )
    assert not await store.phase_event(
        organization_id=org_id,
        task_id=uuid4(),
        task_run_id=run_id,
        attempt_id=retry_attempt,
        phase="planning",
        event="started",
    )


@pytest.mark.asyncio
async def test_failed_snapshot_publication_cannot_project_stale_attempt_after_restart(
    running_pair, monkeypatch
):
    """A committed attempt fence makes an old file unavailable after reset failure."""

    factory = running_pair["factory"]
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(factory, async_sessionmaker)
    assert isinstance(store, TaskRunProgressStore)

    first_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    for phase in PHASES[:4]:
        assert await store.phase_event(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=first_attempt,
            phase=phase,
            event="started",
        )
        assert await store.phase_event(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=first_attempt,
            phase=phase,
            event="succeeded",
        )

    async with factory() as session:
        before = await session.get(TaskRun, run_id)
    assert before is not None
    before_updated_at = before.updated_at

    def fail_tombstone(_path):
        raise OSError("injected tombstone publication failure")

    monkeypatch.setattr(TaskRunProgressStore, "_write_unavailable", staticmethod(fail_tombstone))
    second_attempt = uuid4()
    with pytest.raises(OSError, match="tombstone publication"):
        await store.begin_attempt(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=second_attempt,
            reset_reason="replan",
            retry_count=0,
            replan_count=1,
        )

    async with factory() as session:
        after_failed_reset = await session.get(TaskRun, run_id)
    assert after_failed_reset is not None
    assert after_failed_reset.updated_at > before_updated_at

    stale = load_progress_snapshot(task_id, run_id)
    assert stale is not None and stale.attempt_id == first_attempt
    # A fresh process has no in-memory reset state to consult.  The committed
    # TaskRun generation still rejects the surviving old file.
    assert project_progress(task_id, run_id, after_failed_reset) is None
    assert not await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="finalization",
        event="started",
    )

    # A second failure does not make the stale snapshot readable again.
    third_attempt = uuid4()
    with pytest.raises(OSError, match="tombstone publication"):
        await store.begin_attempt(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=third_attempt,
            reset_reason="retry",
            retry_count=1,
            replan_count=1,
        )
    async with factory() as session:
        after_second_failure = await session.get(TaskRun, run_id)
    assert after_second_failure is not None
    assert project_progress(task_id, run_id, after_second_failure) is None

    monkeypatch.undo()
    recovered_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=recovered_attempt,
        reset_reason="resume",
        retry_count=1,
        replan_count=1,
    )
    recovered = load_progress_snapshot(task_id, run_id)
    assert recovered is not None and recovered.attempt_id == recovered_attempt
    async with factory() as session:
        current = await session.get(TaskRun, run_id)
    assert current is not None
    projected = project_progress(task_id, run_id, current)
    assert projected is not None
    assert projected.attempt_id == recovered_attempt
    assert projected.completed_phase_count == 0


@pytest.mark.asyncio
async def test_failed_authority_update_does_not_establish_a_new_attempt(running_pair, monkeypatch):
    """A failed database fence leaves the previously committed attempt current."""

    factory = running_pair["factory"]
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(factory, async_sessionmaker)
    assert isinstance(store, TaskRunProgressStore)

    first_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    async with factory() as session:
        before = await session.get(TaskRun, run_id)
    assert before is not None

    def fail_fence(_value):
        raise OSError("injected authority update failure")

    monkeypatch.setattr(progress_module, "_next_attempt_fence", fail_fence)
    with pytest.raises(ProgressAttemptFenceError, match="authority update"):
        await store.begin_attempt(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=uuid4(),
            reset_reason="retry",
            retry_count=1,
            replan_count=0,
        )

    async with factory() as session:
        after = await session.get(TaskRun, run_id)
    assert after is not None
    assert after.updated_at == before.updated_at
    snapshot = load_progress_snapshot(task_id, run_id)
    assert snapshot is not None and snapshot.attempt_id == first_attempt
    projected = project_progress(task_id, run_id, after)
    assert projected is not None
    assert projected.attempt_id == first_attempt


@pytest.mark.asyncio
async def test_snapshot_replacement_failure_after_fence_is_unavailable(running_pair, monkeypatch):
    """A successful fence still fails closed when the replacement write fails."""

    factory = running_pair["factory"]
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(factory, async_sessionmaker)
    assert isinstance(store, TaskRunProgressStore)

    first_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    async with factory() as session:
        before = await session.get(TaskRun, run_id)
    assert before is not None

    def fail_replacement(*_args):
        raise OSError("injected replacement publication failure")

    monkeypatch.setattr(TaskRunProgressStore, "_write", staticmethod(fail_replacement))
    with pytest.raises(OSError, match="replacement publication"):
        await store.begin_attempt(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=uuid4(),
            reset_reason="replan",
            retry_count=0,
            replan_count=1,
        )

    async with factory() as session:
        after = await session.get(TaskRun, run_id)
    assert after is not None and after.updated_at > before.updated_at
    path = progress_module.progress_snapshot_path(task_id, run_id)
    assert path.read_bytes() == b'{"schema_version":1,"state":"unavailable"}'
    assert project_progress(task_id, run_id, after) is None


@pytest.mark.asyncio
async def test_terminal_success_cannot_project_superseded_snapshot(running_pair, monkeypatch):
    """A later terminal commit does not bind an older snapshot to success."""

    factory = running_pair["factory"]
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(factory, async_sessionmaker)
    assert isinstance(store, TaskRunProgressStore)

    first_attempt = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    for phase in PHASES[:4]:
        assert await store.phase_event(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=first_attempt,
            phase=phase,
            event="started",
        )
        assert await store.phase_event(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=first_attempt,
            phase=phase,
            event="succeeded",
        )
    assert await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first_attempt,
        phase="finalization",
        event="started",
    )
    stale = load_progress_snapshot(task_id, run_id)
    assert stale is not None and stale.attempt_id == first_attempt

    def fail_tombstone(_path):
        raise OSError("injected tombstone publication failure")

    monkeypatch.setattr(TaskRunProgressStore, "_write_unavailable", staticmethod(fail_tombstone))
    with pytest.raises(OSError, match="tombstone publication"):
        await store.begin_attempt(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=uuid4(),
            reset_reason="replan",
            retry_count=0,
            replan_count=0,
        )

    async with factory() as session:
        lifecycle = TaskLifecycleService(session)
        lifecycle.expected_task_run_id = run_id
        await lifecycle.succeed_run(
            task_id,
            org_id,
            result_metadata={
                "schema_version": "taskpilot.runtime.v1",
                "summary": "Runtime completed successfully",
                "metrics": {"plan_steps": 0},
                "verifier_status": "passed",
                "execution_mode": "deterministic_fixture",
                "stage_status": {
                    "planner": "passed",
                    "execution": "passed",
                    "verifier": "passed",
                },
                "replan_count": 0,
            },
        )

    async with factory() as session:
        run = await session.get(TaskRun, run_id)
    assert run is not None and run.status is TaskRunStatus.SUCCEEDED
    assert run.updated_at > stale.run_updated_at
    assert load_progress_snapshot(task_id, run_id) == stale
    assert project_progress(task_id, run_id, run) is None


@pytest.mark.asyncio
async def test_cancellation_lock_wins_over_concurrent_late_progress_write(running_pair):
    factory = running_pair["factory"]
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    assert isinstance(factory, async_sessionmaker)
    assert isinstance(store, TaskRunProgressStore)
    assert isinstance(org_id, UUID) and isinstance(task_id, UUID) and isinstance(run_id, UUID)

    attempt_id = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=attempt_id,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    assert await store.phase_event(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=attempt_id,
        phase="planning",
        event="started",
    )
    before = load_progress_snapshot(task_id, run_id)

    async with factory() as cancellation_session:
        blocker_pid = await cancellation_session.scalar(text("SELECT pg_backend_pid()"))
        await TaskRepository(cancellation_session).get_for_update_in_principal_tenant(
            task_id, org_id
        )
        late_write = asyncio.create_task(
            store.phase_event(
                organization_id=org_id,
                task_id=task_id,
                task_run_id=run_id,
                attempt_id=attempt_id,
                phase="planning",
                event="succeeded",
            )
        )
        async with asyncio.timeout(10):
            while True:
                async with factory() as observer:
                    waiting = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE :blocker = ANY(pg_blocking_pids(pid))"
                        ),
                        {"blocker": blocker_pid},
                    )
                if waiting:
                    break
                await asyncio.sleep(0.02)
        await TaskLifecycleService(cancellation_session).cancel_task(task_id, org_id)
        assert await asyncio.wait_for(late_write, timeout=10) is False

    after = load_progress_snapshot(task_id, run_id)
    assert after == before
    async with factory() as read_session:
        run = await read_session.get(TaskRun, run_id)
    assert run is not None and run.status is TaskRunStatus.CANCELLED
    assert project_progress(task_id, run_id, run) is None


@pytest.mark.asyncio
async def test_only_current_attempt_can_publish_artifacts(running_pair):
    store = running_pair["store"]
    org_id = running_pair["organization_id"]
    task_id = running_pair["task_id"]
    run_id = running_pair["task_run_id"]
    first = uuid4()
    second = uuid4()
    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first,
        reset_reason="start",
        retry_count=0,
        replan_count=0,
    )
    first_root = attempt_artifact_root(task_id, run_id, first)
    first_root.mkdir(parents=True, exist_ok=True)
    (first_root / "ndvi_before.png").write_bytes(b"attempt-a")
    evidence = {"artifacts": {"ndvi_before": "ndvi_before.png"}, "attempt": "a"}
    assert await store.publish_attempt_artifacts(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=first,
        source_dir=first_root,
        canonical_evidence=evidence,
    )
    assert artifact_path(str(task_id), str(run_id), "ndvi_before").read_bytes() == b"attempt-a"
    assert load_dynamic_evidence(str(task_id), str(run_id)) == evidence

    assert await store.begin_attempt(
        organization_id=org_id,
        task_id=task_id,
        task_run_id=run_id,
        attempt_id=second,
        reset_reason="replan",
        retry_count=0,
        replan_count=1,
    )
    second_root = attempt_artifact_root(task_id, run_id, second)
    second_root.mkdir(parents=True, exist_ok=True)
    (second_root / "ndvi_before.png").write_bytes(b"attempt-b")
    second_evidence = {"artifacts": {"ndvi_before": "ndvi_before.png"}, "attempt": "b"}
    current_publish, stale_publish = await asyncio.gather(
        store.publish_attempt_artifacts(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=second,
            source_dir=second_root,
            canonical_evidence=second_evidence,
        ),
        store.publish_attempt_artifacts(
            organization_id=org_id,
            task_id=task_id,
            task_run_id=run_id,
            attempt_id=first,
            source_dir=first_root,
            canonical_evidence=evidence,
        ),
    )
    assert current_publish is True
    assert stale_publish is False
    assert artifact_path(str(task_id), str(run_id), "ndvi_before").read_bytes() == b"attempt-b"
    assert load_dynamic_evidence(str(task_id), str(run_id)) == second_evidence
