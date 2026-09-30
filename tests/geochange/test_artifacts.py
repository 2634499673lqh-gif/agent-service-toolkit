
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image

from geochange.artifacts import artifact_path
from service import task_api


def test_artifact_identity_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("geochange.artifacts.ARTIFACT_ROOT", tmp_path)
    path = artifact_path("task-1", "run-1", "ndvi_before")
    assert path == (tmp_path / "task-1" / "run-1" / "ndvi_before.png").resolve()
    with pytest.raises(ValueError):
        artifact_path("..", "run-1", "ndvi_before")
    with pytest.raises(ValueError):
        artifact_path("task-1", "run-1", "../../secret")


@pytest.mark.asyncio
async def test_artifact_endpoint_authorizes_visible_run_and_rejects_unknown(
    tmp_path, monkeypatch
):
    task_id, run_id = uuid4(), uuid4()
    monkeypatch.setattr("geochange.artifacts.ARTIFACT_ROOT", tmp_path)
    path = tmp_path / str(task_id) / str(run_id)
    path.mkdir(parents=True)
    Image.new("L", (1, 1), color=0).save(path / "ndvi_before.png", format="PNG")

    class VisibleRuns:
        async def get_run(self, principal, requested_task, requested_run):
            if requested_task != task_id or requested_run != run_id:
                return None
            return SimpleNamespace(result_metadata={"artifact_references": {"ndvi_before": "ndvi_before"}})

    monkeypatch.setattr(task_api, "TaskRunService", lambda session: VisibleRuns())
    response = await task_api.get_task_artifact(
        task_id, run_id, "ndvi_before", principal=object(), session=object()
    )
    assert response.media_type == "image/png"
    with pytest.raises(HTTPException) as error:
        await task_api.get_task_artifact(
            task_id, run_id, "unknown", principal=object(), session=object()
        )
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await task_api.get_task_artifact(
            task_id, run_id, "ndvi_after", principal=object(), session=object()
        )
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        await task_api.get_task_artifact(
            uuid4(), run_id, "ndvi_before", principal=object(), session=object()
        )
    assert error.value.status_code == 404
