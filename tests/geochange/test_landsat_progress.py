"""The progress phases follow real Landsat operations, not marker capabilities."""

import asyncio
import threading
from uuid import uuid4

import pytest

from geochange.models import GeoChangeTask
from geochange.runtime_caps import (
    ComputeVegetationRuntimeCapability,
    SearchSentinel2RuntimeCapability,
    SummarizeChangeRuntimeCapability,
)
from runtime.context import ContextBuilder
from runtime.planner import PlannerTaskInput
from schema.planner import PlanStep


class _RecordingProgress:
    def __init__(self) -> None:
        self.phases = {
            "planning": "succeeded",
            "preparation": "pending",
            "computation": "pending",
            "validation": "pending",
            "finalization": "pending",
        }
        self.events: list[tuple[str, str]] = []

    async def start_phase(self, phase):
        self.phases[phase] = "running"
        self.events.append((phase, "started"))

    async def complete_phase(self, phase):
        self.phases[phase] = "succeeded"
        self.events.append((phase, "succeeded"))

    async def fail_phase(self, phase):
        self.phases[phase] = "failed"
        self.events.append((phase, "failed"))

    async def reset_attempt(self, **_kwargs):
        raise AssertionError("this capability test does not replan")


@pytest.mark.asyncio
async def test_blocked_real_preparation_is_running_and_placeholder_markers_do_not_complete_it(
    monkeypatch,
):
    task = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
        aoi_key="jianghan_district_420103",
        data_mode="real_stac_landsat_local",
    )
    progress = _RecordingProgress()
    context = ContextBuilder().build(
        PlannerTaskInput(title="Jianghan NDVI", description="real Landsat"),
        PlanStep(position=1, instruction="summarize_change"),
        runtime_task_id=str(uuid4()),
        runtime_task_run_id=str(uuid4()),
        geochange_task=task,
    )
    # Invocation-only callbacks are excluded from capability observations and
    # checkpoint-shaped context serialization.
    assert "progress_sink" not in context.model_dump_json()

    marker = await SearchSentinel2RuntimeCapability().execute(
        PlanStep(position=1, instruction="search_sentinel2"), context
    )
    preparation_marker = await ComputeVegetationRuntimeCapability().execute(
        PlanStep(position=1, instruction="compute_vegetation_change"), context
    )
    assert marker.success and preparation_marker.success
    assert progress.events == []
    landsat_context = context.model_copy(update={"progress_sink": progress})
    assert "progress_sink" not in landsat_context.model_dump_json()

    started = threading.Event()
    release = threading.Event()

    def blocked_prepare(_aoi, _period_pair):
        started.set()
        if not release.wait(timeout=10):
            raise TimeoutError("test preparation gate was not released")
        return object()

    def fail_compute(_pair, *, artifact_dir):
        del artifact_dir
        raise ValueError("controlled computation stop")

    monkeypatch.setattr("geochange.runtime_caps.prepare_landsat_periods", blocked_prepare)
    monkeypatch.setattr("geochange.runtime_caps.compute_landsat_ndvi_product", fail_compute)
    capability = SummarizeChangeRuntimeCapability()
    running = asyncio.create_task(
        capability.execute(PlanStep(position=1, instruction="summarize_change"), landsat_context)
    )
    try:
        assert await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=6)
        await asyncio.sleep(0.01)
        assert progress.phases["preparation"] == "running"
        assert progress.phases["computation"] == "pending"
        assert progress.phases["validation"] == "pending"
        assert progress.events == [("preparation", "started")]
    finally:
        release.set()

    result = await asyncio.wait_for(running, timeout=6)
    assert not result.success
    assert progress.events == [
        ("preparation", "started"),
        ("preparation", "succeeded"),
        ("computation", "started"),
        ("computation", "failed"),
    ]
    assert progress.phases["validation"] == "pending"
