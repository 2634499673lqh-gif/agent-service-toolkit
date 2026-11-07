"""Durable PostgreSQL-backed worker for TaskPilot TaskRuns."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from dataclasses import dataclass
from uuid import UUID, uuid4

from persistence.engine import get_business_session_factory
from persistence.models import TaskRunStatus
from persistence.repositories import TaskRunRepository
from service.task_lifecycle import TaskLifecycleService, worker_failure_metadata
from service.task_runtime import TaskRuntimeService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ClaimedTaskRun:
    organization_id: UUID
    task_id: UUID
    task_run_id: UUID
    worker_attempts: int


class TaskRunWorker:
    """Poll, lease, execute and recover TaskRuns using durable PostgreSQL state.

    The worker provides at-least-once graph execution. A process crash leaves the
    lease to expire; a later worker can resume from the latest checkpoint. A
    duplicate graph invocation is possible and is bounded by the existing
    TaskRun terminal-winner checks. No exactly-once external effect is claimed.
    """

    def __init__(
        self,
        checkpointer: object,
        *,
        poll_interval: float,
        lease_seconds: float,
        max_attempts: int,
        worker_id: str | None = None,
    ) -> None:
        if poll_interval <= 0 or lease_seconds <= 0 or max_attempts < 1:
            raise ValueError("worker timing and attempt settings must be positive")
        self.checkpointer = checkpointer
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.worker_id = worker_id or self._default_worker_id()
        if not self.worker_id or len(self.worker_id) > 128:
            raise ValueError("worker_id must be a bounded non-empty string")
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @staticmethod
    def _default_worker_id() -> str:
        host = socket.gethostname()[:48] or "unknown-host"
        return f"{host}:{os.getpid()}:{uuid4().hex[:16]}"

    async def start(self) -> None:
        """Start one polling loop; repeated calls are idempotent."""

        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.create_task(self._run_loop(), name="taskpilot-runtime-worker")

    async def close(self) -> None:
        """Stop polling while allowing the current lease to expire safely."""

        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def run_once(self) -> bool:
        """Claim and execute at most one durable run; return whether work was found."""

        claimed = await self._claim()
        if claimed is None:
            return False
        execution = asyncio.create_task(
            self._execute(claimed),
            name=f"taskpilot-run-{claimed.task_run_id}",
        )
        heartbeat = asyncio.create_task(
            self._heartbeat(claimed.task_run_id),
            name=f"taskpilot-lease-{claimed.task_run_id}",
        )
        try:
            await execution
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            raise
        except Exception:
            logger.exception(
                "runtime.worker.execution_failed",
                extra={"task_run_id": str(claimed.task_run_id), "worker_id": self.worker_id},
            )
            await self._handle_execution_failure(claimed)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            await self._release(claimed.task_run_id)
            logger.info(
                "runtime.worker.released",
                extra={
                    "worker_id": self.worker_id,
                    "task_id": str(claimed.task_id),
                    "task_run_id": str(claimed.task_run_id),
                },
            )
        return True

    async def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("runtime.worker.loop_failed", extra={"worker_id": self.worker_id})
                worked = False
            if not worked:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                except TimeoutError:
                    pass

    async def _claim(self) -> ClaimedTaskRun | None:
        factory = get_business_session_factory()
        async with factory() as session:
            claimed = await TaskRunRepository(session).claim_next_for_worker(
                self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            if claimed is None:
                await session.rollback()
                return None
            task, run = claimed
            await session.commit()
            logger.info(
                "runtime.worker.claimed",
                extra={
                    "worker_id": self.worker_id,
                    "task_id": str(task.id),
                    "task_run_id": str(run.id),
                    "worker_attempts": run.worker_attempts,
                    "task_run_status": run.status.value,
                },
            )
            return ClaimedTaskRun(
                organization_id=task.organization_id,
                task_id=task.id,
                task_run_id=run.id,
                worker_attempts=run.worker_attempts,
            )

    async def _execute(self, claimed: ClaimedTaskRun) -> None:
        factory = get_business_session_factory()
        async with factory() as session:
            await TaskRuntimeService(self.checkpointer, session_factory=factory).execute_run(
                session,
                organization_id=claimed.organization_id,
                task_id=claimed.task_id,
                task_run_id=claimed.task_run_id,
            )

    async def _heartbeat(self, task_run_id: UUID) -> None:
        interval = max(self.lease_seconds / 3.0, 0.5)
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except TimeoutError:
                pass
            if self._stop.is_set():
                return
            try:
                factory = get_business_session_factory()
                async with factory() as session:
                    renewed = await TaskRunRepository(session).renew_worker_lease(
                        task_run_id,
                        self.worker_id,
                        lease_seconds=self.lease_seconds,
                    )
                    await session.commit()
                if not renewed:
                    logger.warning(
                        "runtime.worker.lease_lost",
                        extra={"task_run_id": str(task_run_id), "worker_id": self.worker_id},
                    )
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "runtime.worker.heartbeat_failed",
                    extra={"task_run_id": str(task_run_id), "worker_id": self.worker_id},
                )

    async def _handle_execution_failure(self, claimed: ClaimedTaskRun) -> None:
        if claimed.worker_attempts < self.max_attempts:
            return
        factory = get_business_session_factory()
        async with factory() as session:
            pair = await TaskRunRepository(session).get_task_and_run_in_principal_tenant(
                claimed.task_id,
                claimed.task_run_id,
                claimed.organization_id,
            )
            if pair is None:
                await session.rollback()
                return
            task, run = pair
            reason = "runtime_worker_attempts_exhausted"
            lifecycle = TaskLifecycleService(session)
            if run.status is TaskRunStatus.RUNNING and task.status.value == "running":
                await lifecycle.fail_run(
                    claimed.task_id,
                    claimed.organization_id,
                    claimed.task_run_id,
                    worker_failure_metadata(reason),
                )
            elif run.status is TaskRunStatus.PENDING:
                await lifecycle.fail_unstarted_run(
                    claimed.task_id,
                    claimed.organization_id,
                    claimed.task_run_id,
                    reason=reason,
                )
            else:
                await session.rollback()

    async def _release(self, task_run_id: UUID) -> None:
        try:
            factory = get_business_session_factory()
            async with factory() as session:
                await TaskRunRepository(session).release_worker_lease(
                    task_run_id,
                    self.worker_id,
                )
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "runtime.worker.lease_release_failed",
                extra={"task_run_id": str(task_run_id), "worker_id": self.worker_id},
            )


__all__ = ["ClaimedTaskRun", "TaskRunWorker"]
