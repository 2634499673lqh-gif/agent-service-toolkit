import pytest

from geochange.llm import GeoChangeLLM, extract_explicit_parameters
from geochange.models import GeoChangeTask


class StructuredModel:
    def __init__(self, output):
        self.output = output

    def with_structured_output(self, _schema):
        return self

    async def ainvoke(self, _input):
        return self.output


def test_explicit_parameter_extractor_is_bounded_and_deterministic():
    values = extract_explicit_parameters(
        "Use a maximum Sentinel-2 cloud cover threshold of 30% and NDVI decline threshold of -0.15."
    )
    assert values.cloud_threshold == 30.0
    assert values.decline_threshold == -0.15


@pytest.mark.parametrize(
    "text",
    [
        "maximum cloud cover threshold of 130%",
        "maximum cloud cover threshold of 30 percent",
        "NDVI decline threshold of -1.2",
        "maximum cloud cover threshold of 30% and maximum cloud cover threshold of 20%",
        "NDVI decline threshold of -0.15%",
        "NDVI decline threshold of -0.15 %",
        "NDVI decline threshold of -0.15 percent",
        "NDVI decline threshold of -0.15junk",
        "NDVI decline threshold of -0.15e-2",
        "NDVI decline threshold of -0.15.9",
        "maximum Sentinel-2 cloud cover threshold of 30%%",
        "maximum Sentinel-2 cloud cover threshold of 30%junk",
    ],
)
def test_invalid_explicit_parameters_fail_closed(text):
    with pytest.raises(ValueError):
        extract_explicit_parameters(text)


@pytest.mark.asyncio
async def test_model_numbers_cannot_override_server_authority():
    model = StructuredModel(
        {
            "analysis_type": "vegetation_change",
            "version": 1,
            "aoi_key": "wuhan_east_lake",
            "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
            "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
            "cloud_threshold": 2,
            "decline_threshold": -0.9,
            "data_mode": "local_real_raster_fixture",
        }
    )
    task = await GeoChangeLLM(model).parse_task("Analyze vegetation decline")
    assert task.cloud_threshold == 30.0
    assert task.decline_threshold == -0.2
    assert task.cloud_threshold_source == "server_default"
    assert task.decline_threshold_source == "server_default"
    assert task.data_mode == "local_real_raster_fixture"


@pytest.mark.asyncio
async def test_structured_geochange_model_instance_is_accepted():
    structured = GeoChangeTask(
        period_a={"start": "2023-07-01", "end": "2023-07-31"},
        period_b={"start": "2024-07-01", "end": "2024-07-31"},
    )
    task = await GeoChangeLLM(StructuredModel(structured)).parse_task(
        "Analyze vegetation decline"
    )
    assert task == structured


@pytest.mark.asyncio
async def test_unsupported_model_data_mode_fails_closed():
    model = StructuredModel(
        {
            "analysis_type": "vegetation_change",
            "version": 1,
            "aoi_key": "wuhan_east_lake",
            "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
            "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
            "data_mode": "real_online",
        }
    )
    with pytest.raises(ValueError, match="data_mode"):
        await GeoChangeLLM(model).parse_task("Analyze vegetation decline")


@pytest.mark.asyncio
async def test_explicit_values_survive_model_conflict():
    model = StructuredModel(
        {
            "analysis_type": "vegetation_change",
            "version": 1,
            "aoi_key": "wuhan_east_lake",
            "period_a": {"start": "2023-07-01", "end": "2023-07-31"},
            "period_b": {"start": "2024-07-01", "end": "2024-07-31"},
            "cloud_threshold": 2,
            "decline_threshold": -0.9,
        }
    )
    task = await GeoChangeLLM(model).parse_task(
        "Analyze using a maximum Sentinel-2 cloud cover threshold of 30% and NDVI decline threshold of -0.15"
    )
    assert task.cloud_threshold == 30.0
    assert task.decline_threshold == -0.15
    assert task.cloud_threshold_source == "user_text"
    assert task.decline_threshold_source == "user_text"
