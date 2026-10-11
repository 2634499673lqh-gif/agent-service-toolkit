"""Attempt directories keep late uninterruptible workers away from current output."""

import asyncio
from uuid import uuid4

import pytest

import geochange.artifacts as artifacts


@pytest.mark.asyncio
async def test_late_attempt_writer_cannot_overwrite_current_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", tmp_path)
    task_id, run_id = uuid4(), uuid4()
    attempt_a, attempt_b = uuid4(), uuid4()
    root_a = artifacts.attempt_artifact_root(str(task_id), str(run_id), attempt_a)
    root_b = artifacts.attempt_artifact_root(str(task_id), str(run_id), attempt_b)
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def late_worker() -> None:
        started.set()
        await release.wait()
        (root_a / "ndvi_change.png").write_bytes(b"late-attempt-a")

    worker = asyncio.create_task(late_worker())
    await started.wait()
    (root_b / "ndvi_change.png").write_bytes(b"authoritative-attempt-b")
    artifacts.publish_attempt_pointer(str(task_id), str(run_id), attempt_b)
    release.set()
    await worker

    assert artifacts.artifact_path(str(task_id), str(run_id), "ndvi_change").read_bytes() == (
        b"authoritative-attempt-b"
    )
    assert (root_a / "ndvi_change.png").read_bytes() == b"late-attempt-a"


def test_invalid_current_attempt_pointer_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(artifacts, "ARTIFACT_ROOT", tmp_path)
    task_id, run_id = uuid4(), uuid4()
    run_root = tmp_path / str(task_id) / str(run_id)
    run_root.mkdir(parents=True)
    (run_root / ".current_attempt").write_text("corrupt", encoding="ascii")
    assert not artifacts.artifact_path(str(task_id), str(run_id), "ndvi_change").exists()
    assert artifacts.load_dynamic_evidence(str(task_id), str(run_id)) is None
