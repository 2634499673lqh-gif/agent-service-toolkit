import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

import geochange.aoi as aoi_module

SNAPSHOT = Path("data/geochange-aoi/jianghan_district_420103.geojson")


def _payload() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def _load_from(tmp_path, monkeypatch, payload):
    target = tmp_path / SNAPSHOT.name
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(aoi_module, "_TRUSTED_AOI_SNAPSHOT_CANDIDATES", (target,))
    aoi_module._load_trusted_aoi_template.cache_clear()
    return aoi_module.load_trusted_aoi()


def test_external_snapshot_pin_rejects_payload_with_recomputed_embedded_hash(tmp_path, monkeypatch):
    payload = _payload()
    payload["area_m2"] += 1.0
    payload["source_sha256"] = hashlib.sha256(
        aoi_module._canonical_snapshot_payload(payload)
    ).hexdigest()

    with pytest.raises(ValueError, match="approved Jianghan identity|integrity"):
        _load_from(tmp_path, monkeypatch, payload)


@pytest.mark.parametrize(
    "geometry",
    [
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]],
        },
        {
            "type": "Polygon",
            "coordinates": [
                [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
                [[0.5, 0.5], [1.5, 0.5], [0.5, 1.5], [0.5, 0.5]],
            ],
        },
        {
            "type": "MultiPolygon",
            "coordinates": [
                [[[0, 0], [1, 0], [1, 1], [0, 0]]],
                [[[2, 2], [3, 2], [3, 3], [2, 2]]],
            ],
        },
    ],
)
def test_geometry_validation_covers_all_parts_and_holes(geometry):
    aoi_module._validate_trusted_geometry({"geometry": geometry})


@pytest.mark.parametrize(
    "geometry",
    [
        {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [0, 1]]]},
        {
            "type": "Polygon",
            "coordinates": [[[0, 0], [1, 0], [0, 1], [0, 0]], [[0, 0], [2, 0], [2, 2], [0, 0]]],
        },
        {"type": "MultiPolygon", "coordinates": []},
        {"type": "Polygon", "coordinates": [[[0, 0], [float("nan"), 0], [1, 1], [0, 0]]]},
    ],
)
def test_geometry_validation_rejects_invalid_rings_parts_and_coordinates(geometry):
    with pytest.raises(ValueError):
        aoi_module._validate_trusted_geometry({"geometry": geometry})


def test_area_mismatch_and_pilot_sanity_are_independent_checks(tmp_path, monkeypatch):
    payload = _payload()
    # The external pin rejects this self-consistent forged payload before any
    # provider/user-controlled metadata can be trusted.
    payload["area_m2"] = 49_000_000.0
    payload["source_sha256"] = hashlib.sha256(
        aoi_module._canonical_snapshot_payload(payload)
    ).hexdigest()
    with pytest.raises(ValueError):
        _load_from(tmp_path, monkeypatch, payload)

    with pytest.raises(ValueError, match="pilot location"):
        aoi_module._validate_pilot_location(
            {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            }
        )


def test_canonical_geometry_is_not_mutable_across_loads():
    first = aoi_module.load_trusted_aoi()
    original = deepcopy(first.geometry)
    first.geometry["coordinates"][0][0][0] = -179.0
    second = aoi_module.load_trusted_aoi()
    assert second.geometry == original


def test_approved_metadata_and_independent_projected_area():
    loaded = aoi_module.load_trusted_aoi()
    assert loaded.source == "OpenStreetMap relation 3077256"
    assert loaded.source_version == 21
    assert loaded.admin_code == "420103"
    assert loaded.crs == "EPSG:4326"
    assert loaded.license == "ODbL 1.0"
    projected = aoi_module._projected_area_m2(loaded.geometry)
    assert (
        abs(projected - loaded.area_m2) / projected
        < aoi_module._TRUSTED_AOI_AREA_RELATIVE_TOLERANCE
    )
