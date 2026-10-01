from runtime.graph import _offline_geochange_task
from runtime.planner import PlannerTaskInput


def test_offline_geochange_preserves_explicit_thresholds() -> None:
    task = _offline_geochange_task(
        PlannerTaskInput(
            title="Compare Wuhan East Lake vegetation change",
            description=(
                "July 2023 versus July 2024; maximum Sentinel-2 cloud cover "
                "threshold of 18% and NDVI decline threshold of -0.15."
            ),
        )
    )

    assert task.cloud_threshold == 18.0
    assert task.decline_threshold == -0.15
    assert task.cloud_threshold_source == "user_text"
    assert task.decline_threshold_source == "user_text"


def test_offline_geochange_uses_frozen_defaults_when_omitted() -> None:
    task = _offline_geochange_task(
        PlannerTaskInput(
            title="Compare Wuhan East Lake vegetation change",
            description="July 2023 versus July 2024.",
        )
    )

    assert task.cloud_threshold == 30.0
    assert task.decline_threshold == -0.2
    assert task.cloud_threshold_source == "server_default"
    assert task.decline_threshold_source == "server_default"
