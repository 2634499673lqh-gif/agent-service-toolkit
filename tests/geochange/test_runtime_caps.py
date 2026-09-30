import json
from uuid import uuid4

import pytest

from geochange.models import GeoChangeTask
from geochange.runtime_caps import (
    ResolveAOIRuntimeCapability,
    SearchSentinel2RuntimeCapability,
    SummarizeChangeRuntimeCapability,
)
from runtime.context import ContextBuilder
from runtime.planner import PlannerTaskInput
from schema.planner import PlanStep


def _context(task: GeoChangeTask, instruction: str):
    return ContextBuilder().build(
        PlannerTaskInput(title="vegetation change", description="Wuhan East Lake"),
        PlanStep(position=1, instruction=instruction),
        runtime_task_id=str(uuid4()),
        runtime_task_run_id=str(uuid4()),
        geochange_task=task,
    )


@pytest.mark.asyncio
async def test_capabilities_consume_validated_geochange_parameters():
    task = GeoChangeTask(
        period_a={"start": "2022-05-01", "end": "2022-05-07"},
        period_b={"start": "2025-09-01", "end": "2025-09-07"},
        cloud_threshold=7,
        decline_threshold=-0.1,
    )
    resolve = await ResolveAOIRuntimeCapability().execute(
        PlanStep(position=1, instruction="resolve_aoi"), _context(task, "resolve_aoi")
    )
    assert json.loads(resolve.output)["catalog_key"] == task.aoi_key

    search = await SearchSentinel2RuntimeCapability().execute(
        PlanStep(position=1, instruction="search_sentinel2"), _context(task, "search_sentinel2")
    )
    evidence = json.loads(search.output)
    assert evidence["period_a_date"] == "2022-05-07"
    assert evidence["period_b_date"] == "2025-09-07"
    assert evidence["period_a_cloud_cover"] == "7.0"

    context = _context(task, "summarize_change")
    context = context.model_copy(update={"geochange_evidence": evidence, "geochange_aoi_evidence": json.loads(resolve.output)})
    summary = await SummarizeChangeRuntimeCapability().execute(
        PlanStep(position=1, instruction="summarize_change"), context
    )
    assert json.loads(summary.output)["metrics"]["decline_threshold"] == -0.1
