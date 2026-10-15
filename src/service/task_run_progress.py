"""Server-owned, observational progress for the real Jianghan Landsat run."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from geochange.artifacts import (
    ALLOWED_ARTIFACTS,
    ARTIFACT_ROOT,
    CANONICAL_ARTIFACT_FILENAMES,
    attempt_artifact_root,
    current_attempt_id,
    invalidate_attempt_pointer,
    publish_attempt_pointer,
    write_dynamic_evidence,
)
from geochange.landsat_ndvi import (
    MASK_ARTIFACTS,
    MAX_DISPLAY_ARTIFACT_BYTES,
    MAX_NUMERIC_ARTIFACT_BYTES,
    NDVI_ARTIFACTS,
    PNG_ARTIFACTS,
    artifact_sha256,
)
from persistence.models import TaskRun, TaskRunStatus, TaskStatus
from persistence.repositories import TaskRepository, TaskRunRepository
from runtime.progress import ProgressPhase, ProgressResetReason, RuntimeProgressSink
from schema.task_run_api import (
    TaskRunProgressPhase,
    TaskRunProgressPhaseStatus,
    TaskRunProgressResponse,
    TaskRunProgressState,
)

logger = logging.getLogger(__name__)

PHASES: tuple[ProgressPhase, ...] = (
    "planning",
    "preparation",
    "computation",
    "validation",
    "finalization",
)
_SNAPSHOT_MAX_BYTES = 8_192
_UNAVAILABLE_SNAPSHOT = b'{"schema_version":1,"state":"unavailable"}'
_PHASE_STATES = frozenset({"pending", "running", "succeeded", "failed"})
SnapshotPhaseState = Literal["pending", "running", "succeeded", "failed"]


class ProgressSnapshot(BaseModel):
    """Small file snapshot; it is never a lifecycle or scientific authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    task_id: UUID
    task_run_id: UUID
    attempt_id: UUID
    attempt_number: int = Field(ge=1, le=1_000_000)
    retry_count: int = Field(ge=0, le=1)
    replan_count: int = Field(ge=0, le=1)
    reset_reason: ProgressResetReason
    phases: dict[ProgressPhase, SnapshotPhaseState]
    run_updated_at: datetime
    event_sequence: int = Field(ge=0, le=100_000)
    last_event: str = Field(min_length=1, max_length=64)
    updated_at: datetime

    @field_validator("phases")
    @classmethod
    def validate_phases(
        cls, value: dict[ProgressPhase, SnapshotPhaseState]
    ) -> dict[ProgressPhase, SnapshotPhaseState]:
        if set(value) != set(PHASES) or any(state not in _PHASE_STATES for state in value.values()):
            raise ValueError("progress phase map is invalid")
        if value["finalization"] == "succeeded":
            raise ValueError("finalization completion is derived from committed TaskRun state")
        states = [value[phase] for phase in PHASES]
        first_open = next((index for index, state in enumerate(states) if state != "succeeded"), 5)
        if any(state == "succeeded" for state in states[first_open + 1 :]):
            raise ValueError("progress phases are not a completed prefix")
        if sum(state == "running" for state in states) > 1:
            raise ValueError("only one progress phase may be running")
        if any(state in {"running", "failed"} for state in states[first_open + 1 :]):
            raise ValueError("progress phases cannot advance past an open phase")
        return value

    @field_validator("updated_at")
    @classmethod
    def timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("progress timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("run_updated_at")
    @classmethod
    def lifecycle_timestamp_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("TaskRun lifecycle timestamp must be timezone-aware")
        return value.astimezone(UTC)

    @field_validator("last_event")
    @classmethod
    def event_is_bounded(cls, value: str) -> str:
        allowed = {"attempt.reset"}
        allowed.update(
            f"{phase}.{event}" for phase in PHASES for event in ("started", "succeeded", "failed")
        )
        if value not in allowed:
            raise ValueError("progress event is invalid")
        return value

    @model_validator(mode="after")
    def sequence_matches_phase_state(self) -> ProgressSnapshot:
        if self.event_sequence == 0 and self.last_event != "attempt.reset":
            raise ValueError("initial progress event is invalid")
        return self


def progress_snapshot_path(task_id: UUID | str, task_run_id: UUID | str) -> Path:
    """Resolve a deterministic, separately named file under the run artifact root."""

    task = str(UUID(str(task_id)))
    run = str(UUID(str(task_run_id)))
    root = ARTIFACT_ROOT.resolve()
    run_root = (root / task / run).resolve()
    if root not in run_root.parents:
        raise ValueError("progress identity escapes artifact root")
    return run_root / ".execution_progress.json"


def load_progress_snapshot(
    task_id: UUID | str,
    task_run_id: UUID | str,
) -> ProgressSnapshot | None:
    """Return only a bounded, valid identity-matched snapshot."""

    try:
        task = UUID(str(task_id))
        run = UUID(str(task_run_id))
        path = progress_snapshot_path(task, run)
        if not path.is_file() or path.stat().st_size > _SNAPSHOT_MAX_BYTES:
            return None
        with path.open("rb") as source:
            raw = source.read(_SNAPSHOT_MAX_BYTES + 1)
        if len(raw) > _SNAPSHOT_MAX_BYTES:
            return None
        # The reset may install this bounded read-side tombstone.  It
        # deliberately fails validation as a ProgressSnapshot; the durable
        # TaskRun generation is the authority when tombstone publication fails.
        if raw == _UNAVAILABLE_SNAPSHOT:
            return None
        snapshot = ProgressSnapshot.model_validate_json(raw)
        if snapshot.task_id != task or snapshot.task_run_id != run:
            return None
        return snapshot
    except (OSError, TypeError, ValueError):
        return None


def project_progress(
    task_id: UUID,
    task_run_id: UUID,
    run: TaskRun,
) -> TaskRunProgressResponse | None:
    """Project running progress; unbound terminal snapshots fail closed."""

    snapshot = load_progress_snapshot(task_id, task_run_id)
    if snapshot is None:
        return None

    if run.status is TaskRunStatus.RUNNING:
        # TaskRun.updated_at is the durable current-attempt generation.  The
        # snapshot is display data and is readable only when it proves that
        # it belongs to the committed running attempt.
        lifecycle_updated_at = getattr(run, "updated_at", None)
        if isinstance(lifecycle_updated_at, datetime):
            if lifecycle_updated_at.tzinfo is None or lifecycle_updated_at.utcoffset() is None:
                return None
            if snapshot.run_updated_at != lifecycle_updated_at.astimezone(UTC):
                return None

    run_state = cast(TaskRunProgressState, run.status.value)
    if run.status in {
        TaskRunStatus.SUCCEEDED,
        TaskRunStatus.FAILED,
        TaskRunStatus.CANCELLED,
    }:
        # TaskRun terminal state is authoritative for the run status, but the
        # existing persistence contract does not record which fenced attempt
        # committed that terminal state.  ``updated_at`` changes again during
        # the terminal transition, and the artifact pointer can still name a
        # superseded attempt after a failed reset publication.  Without a
        # durable terminal-attempt binding, projecting this snapshot could
        # present another attempt's history as current.  Keep the genuine
        # terminal status and results in the TaskRun response while making the
        # optional observational progress unavailable.
        return None

    phase_status: dict[ProgressPhase, TaskRunProgressPhaseStatus] = dict(snapshot.phases)

    phases = [TaskRunProgressPhase(name=phase, status=phase_status[phase]) for phase in PHASES]
    completed = sum(phase.status == "succeeded" for phase in phases)
    current = next((phase.name for phase in phases if phase.status != "succeeded"), None)
    return TaskRunProgressResponse(
        attempt_id=snapshot.attempt_id,
        attempt_number=snapshot.attempt_number,
        retry_count=snapshot.retry_count,
        replan_count=snapshot.replan_count,
        reset_reason=snapshot.reset_reason,
        state=run_state,
        current_phase=current,
        completed_phase_count=completed,
        total_phase_count=5,
        phase_completion_pct=completed * 100 // len(PHASES),
        phases=phases,
        last_event=snapshot.last_event,
        last_event_at=snapshot.updated_at,
    )


class ProgressAttemptFenceError(RuntimeError):
    """The durable current-attempt generation could not be committed."""


class TaskRunProgressStore:
    """Serialize short progress updates with lifecycle transitions via Task-first locks."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def begin_attempt(
        self,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        attempt_id: UUID,
        reset_reason: ProgressResetReason,
        retry_count: int,
        replan_count: int,
        completed_phases: tuple[ProgressPhase, ...] = (),
    ) -> bool:
        if completed_phases != PHASES[: len(completed_phases)]:
            return False
        async with self.session_factory() as session:
            try:
                run = await self._lock_active_run(session, organization_id, task_id, task_run_id)
                if run is None:
                    await session.rollback()
                    return False
                path = progress_snapshot_path(task_id, task_run_id)
                previous = load_progress_snapshot(task_id, task_run_id)
                if previous is not None and (
                    previous.task_id != task_id or previous.task_run_id != task_run_id
                ):
                    await session.rollback()
                    return False
                # TaskRun.updated_at is the existing durable server-owned
                # generation for the current running attempt.  Commit this
                # fence before touching the progress file.  A failed file
                # publication therefore leaves any older snapshot with a
                # provably stale generation after restart.
                try:
                    authority_updated_at = _next_attempt_fence(run.updated_at)
                    run.updated_at = authority_updated_at
                    await session.flush()
                    await session.commit()
                except Exception as error:
                    await session.rollback()
                    raise ProgressAttemptFenceError(
                        "durable authority update for progress attempt could not be committed"
                    ) from error
                phases: dict[ProgressPhase, SnapshotPhaseState] = {
                    phase: "pending" for phase in PHASES
                }
                for phase in completed_phases:
                    phases[phase] = "succeeded"
                snapshot = ProgressSnapshot(
                    task_id=task_id,
                    task_run_id=task_run_id,
                    attempt_id=attempt_id,
                    attempt_number=1 if previous is None else previous.attempt_number + 1,
                    retry_count=retry_count,
                    replan_count=replan_count,
                    reset_reason=reset_reason,
                    phases=phases,
                    run_updated_at=authority_updated_at,
                    event_sequence=0,
                    last_event="attempt.reset",
                    updated_at=datetime.now(UTC),
                )
                # The database fence above is authoritative.  The tombstone
                # remains a bounded best-effort read-side marker, while a
                # failed replacement is still fail-closed through the
                # committed TaskRun generation.
                self._write_unavailable(path)
                invalidate_attempt_pointer(str(task_id), str(task_run_id))
                self._write(path, snapshot)
                await session.commit()
                return True
            except BaseException:
                await session.rollback()
                raise

    async def phase_event(
        self,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        attempt_id: UUID,
        phase: ProgressPhase,
        event: Literal["started", "succeeded", "failed"],
    ) -> bool:
        async with self.session_factory() as session:
            try:
                run = await self._lock_active_run(session, organization_id, task_id, task_run_id)
                if run is None:
                    await session.rollback()
                    return False
                path = progress_snapshot_path(task_id, task_run_id)
                snapshot = load_progress_snapshot(task_id, task_run_id)
                if (
                    snapshot is None
                    or snapshot.attempt_id != attempt_id
                    or snapshot.run_updated_at != _utc_timestamp(run.updated_at)
                ):
                    await session.rollback()
                    return False
                states = dict(snapshot.phases)
                index = PHASES.index(phase)
                if event == "started":
                    if states[phase] == "running":
                        await session.rollback()
                        return True
                    if states[phase] != "pending" or any(
                        states[previous] != "succeeded" for previous in PHASES[:index]
                    ):
                        await session.rollback()
                        return False
                    states[phase] = "running"
                elif event == "succeeded":
                    if states[phase] == "succeeded":
                        await session.rollback()
                        return True
                    if states[phase] != "running":
                        await session.rollback()
                        return False
                    states[phase] = "succeeded"
                else:
                    if states[phase] == "failed":
                        await session.rollback()
                        return True
                    if states[phase] != "running":
                        await session.rollback()
                        return False
                    states[phase] = "failed"
                updated = snapshot.model_copy(
                    update={
                        "phases": states,
                        "event_sequence": snapshot.event_sequence + 1,
                        "last_event": f"{phase}.{event}",
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._write(path, updated)
                await session.commit()
                return True
            except BaseException:
                await session.rollback()
                raise

    async def publish_attempt_artifacts(
        self,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        attempt_id: UUID,
        source_dir: Path,
        canonical_evidence: dict[str, object],
    ) -> bool:
        """Fence and expose one fully verified attempt under a short DB lock."""

        expected_dir = attempt_artifact_root(str(task_id), str(task_run_id), attempt_id).resolve()
        try:
            if source_dir.resolve() != expected_dir or not expected_dir.is_dir():
                return False
        except OSError:
            return False
        artifacts = canonical_evidence.get("artifacts")
        required = set(NDVI_ARTIFACTS + MASK_ARTIFACTS + PNG_ARTIFACTS)
        provenance = canonical_evidence.get("provenance")
        checksums = provenance.get("artifact_checksums") if isinstance(provenance, dict) else None
        if (
            not isinstance(artifacts, dict)
            or set(artifacts) != required
            or not isinstance(checksums, dict)
            or set(checksums) != required
        ):
            return False
        for name, artifact_id in artifacts.items():
            if not isinstance(name, str) or name not in ALLOWED_ARTIFACTS or artifact_id != name:
                return False
            path = (expected_dir / CANONICAL_ARTIFACT_FILENAMES[name]).resolve()
            if expected_dir not in path.parents or not path.is_file():
                return False
            limit = (
                MAX_DISPLAY_ARTIFACT_BYTES if name in PNG_ARTIFACTS else MAX_NUMERIC_ARTIFACT_BYTES
            )
            if not 0 < path.stat().st_size <= limit or artifact_sha256(path) != checksums[name]:
                return False
        async with self.session_factory() as session:
            try:
                run = await self._lock_active_run(session, organization_id, task_id, task_run_id)
                if run is None:
                    await session.rollback()
                    return False
                snapshot = load_progress_snapshot(task_id, task_run_id)
                if (
                    snapshot is None
                    or snapshot.attempt_id != attempt_id
                    or snapshot.run_updated_at != _utc_timestamp(run.updated_at)
                ):
                    await session.rollback()
                    return False
                write_dynamic_evidence(
                    str(task_id),
                    str(task_run_id),
                    canonical_evidence,
                    attempt_id=attempt_id,
                )
                publish_attempt_pointer(str(task_id), str(task_run_id), attempt_id)
                await session.commit()
                return True
            except BaseException:
                await session.rollback()
                raise

    async def activate_attempt_artifacts(
        self,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
        attempt_id: UUID,
    ) -> bool:
        """Expose a copied, previously verified artifact set for resume."""

        async with self.session_factory() as session:
            try:
                run = await self._lock_active_run(session, organization_id, task_id, task_run_id)
                snapshot = load_progress_snapshot(task_id, task_run_id)
                if (
                    run is None
                    or snapshot is None
                    or snapshot.attempt_id != attempt_id
                    or snapshot.run_updated_at != _utc_timestamp(run.updated_at)
                ):
                    await session.rollback()
                    return False
                publish_attempt_pointer(str(task_id), str(task_run_id), attempt_id)
                await session.commit()
                return True
            except BaseException:
                await session.rollback()
                raise

    async def _lock_active_run(
        self,
        session: AsyncSession,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
    ) -> TaskRun | None:
        task = await TaskRepository(session).get_for_update_in_principal_tenant(
            task_id, organization_id
        )
        if task is None or task.status is not TaskStatus.RUNNING:
            return None
        run = await TaskRunRepository(session).get_for_update_for_task_in_principal_tenant(
            task_id, task_run_id, organization_id
        )
        if run is None or run.status is not TaskRunStatus.RUNNING:
            return None
        return run

    @staticmethod
    def _write(path: Path, snapshot: ProgressSnapshot) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = snapshot.model_dump_json().encode("utf-8")
        if len(encoded) > _SNAPSHOT_MAX_BYTES:
            raise ValueError("progress snapshot exceeds the bounded limit")
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(encoded)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_unavailable(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(_UNAVAILABLE_SNAPSHOT)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _utc_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        # SQLite test databases may drop timezone metadata; TaskPilot persists
        # these lifecycle timestamps as UTC.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _next_attempt_fence(value: datetime) -> datetime:
    """Return a strictly newer UTC generation for a new running attempt."""

    current = _utc_timestamp(value)
    candidate = datetime.now(UTC)
    if candidate <= current:
        candidate = current + timedelta(microseconds=1)
    return candidate


class TaskRunProgressReporter:
    """Own the current immutable attempt lease for one trusted Landsat run."""

    def __init__(
        self,
        store: TaskRunProgressStore,
        *,
        organization_id: UUID,
        task_id: UUID,
        task_run_id: UUID,
    ) -> None:
        self.store = store
        self.organization_id = organization_id
        self.task_id = task_id
        self.task_run_id = task_run_id
        self._current_attempt: _TaskRunProgressAttempt | None = None

    async def reset_attempt(
        self,
        *,
        reset_reason: ProgressResetReason,
        retry_count: int,
        replan_count: int,
        completed_phases: tuple[ProgressPhase, ...] = (),
    ) -> None:
        previous_attempt_id = current_attempt_id(str(self.task_id), str(self.task_run_id))
        # Revoke the old lease before awaiting persistence. A failed reset makes
        # later phase callbacks unavailable instead of reusing a prior attempt.
        self._current_attempt = None
        attempt_id = uuid4()
        try:
            written = await self.store.begin_attempt(
                organization_id=self.organization_id,
                task_id=self.task_id,
                task_run_id=self.task_run_id,
                attempt_id=attempt_id,
                reset_reason=reset_reason,
                retry_count=retry_count,
                replan_count=replan_count,
                completed_phases=completed_phases,
            )
            if written:
                self._current_attempt = _TaskRunProgressAttempt(self, attempt_id)
                if previous_attempt_id is not None and len(completed_phases) >= 3:
                    source = attempt_artifact_root(
                        str(self.task_id), str(self.task_run_id), previous_attempt_id
                    )
                    destination = self._current_attempt.artifact_dir()
                    try:
                        await asyncio.to_thread(shutil.copytree, source, destination)
                        activated = await self.store.activate_attempt_artifacts(
                            organization_id=self.organization_id,
                            task_id=self.task_id,
                            task_run_id=self.task_run_id,
                            attempt_id=attempt_id,
                        )
                        if not activated:
                            self._current_attempt = None
                            self.store._write_unavailable(
                                progress_snapshot_path(self.task_id, self.task_run_id)
                            )
                    except Exception:
                        self._current_attempt = None
                        self.store._write_unavailable(
                            progress_snapshot_path(self.task_id, self.task_run_id)
                        )
                        self._log_failure("artifacts.resume_copy")
        except ProgressAttemptFenceError:
            self._log_failure("attempt.fence")
            raise
        except Exception:
            self._log_failure("attempt.reset")
        # Even when optional snapshot persistence fails, execution must retain
        # a private output directory. Publication still checks the durable
        # generation and snapshot, and therefore fails closed if unavailable.
        # Returning None here would enable the legacy shared-directory writer.
        if self._current_attempt is None:
            self._current_attempt = _TaskRunProgressAttempt(self, attempt_id)

    def current_attempt(self) -> RuntimeProgressSink | None:
        """Return a phase callback whose attempt identity cannot later change."""

        return self._current_attempt

    def _log_failure(self, event: str) -> None:
        logger.warning(
            "taskrun.progress.write_failed",
            extra={
                "task_id": str(self.task_id),
                "task_run_id": str(self.task_run_id),
                "event": event,
            },
        )


class _TaskRunProgressAttempt:
    """Phase writes pinned to one attempt, so late callbacks stay stale."""

    def __init__(self, reporter: TaskRunProgressReporter, attempt_id: UUID) -> None:
        self.reporter = reporter
        self.attempt_id = attempt_id

    async def start_phase(self, phase: ProgressPhase) -> None:
        await self._record(phase, "started")

    async def complete_phase(self, phase: ProgressPhase) -> None:
        await self._record(phase, "succeeded")

    async def fail_phase(self, phase: ProgressPhase) -> None:
        await self._record(phase, "failed")

    def artifact_dir(self) -> Path:
        return attempt_artifact_root(
            str(self.reporter.task_id), str(self.reporter.task_run_id), self.attempt_id
        )

    async def publish_artifacts(
        self, source_dir: Path, canonical_evidence: dict[str, object]
    ) -> bool:
        try:
            return await self.reporter.store.publish_attempt_artifacts(
                organization_id=self.reporter.organization_id,
                task_id=self.reporter.task_id,
                task_run_id=self.reporter.task_run_id,
                attempt_id=self.attempt_id,
                source_dir=source_dir,
                canonical_evidence=canonical_evidence,
            )
        except Exception:
            self.reporter._log_failure("artifacts.publish")
            return False

    async def _record(
        self,
        phase: ProgressPhase,
        event: Literal["started", "succeeded", "failed"],
    ) -> None:
        try:
            await self.reporter.store.phase_event(
                organization_id=self.reporter.organization_id,
                task_id=self.reporter.task_id,
                task_run_id=self.reporter.task_run_id,
                attempt_id=self.attempt_id,
                phase=phase,
                event=event,
            )
        except Exception:
            self.reporter._log_failure(f"{phase}.{event}")


__all__ = [
    "PHASES",
    "ProgressAttemptFenceError",
    "ProgressSnapshot",
    "TaskRunProgressReporter",
    "TaskRunProgressStore",
    "load_progress_snapshot",
    "project_progress",
    "progress_snapshot_path",
]
