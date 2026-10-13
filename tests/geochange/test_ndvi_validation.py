import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from geochange.ndvi_validation import _reference, validate_capture

_MASK_ARTIFACTS = (
    "ndvi_valid_before",
    "ndvi_valid_after",
    "ndvi_common_comparison",
)


def test_raster_affine_normalizes_rasterio_nine_coefficients(tmp_path: Path):
    rasterio = pytest.importorskip("rasterio")
    from rasterio.transform import Affine

    from geochange.ndvi_validation import _read_raster

    path = tmp_path / "grid.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=1,
        height=1,
        count=1,
        dtype="float32",
        crs=None,
        transform=Affine(30, 0, 1, 0, -30, 2),
        nodata=float("nan"),
    ) as dataset:
        dataset.write(np.ones((1, 1), dtype=np.float32), 1)
    assert (
        _read_raster(
            tmp_path,
            "grid.tif",
            (1, 1),
            grid={"crs": "None", "transform": [30, 0, 1, 0, -30, 2]},
        ).item()
        == 1.0
    )
    with pytest.raises(ValueError, match="affine"):
        _read_raster(
            tmp_path,
            "grid.tif",
            (1, 1),
            grid={"crs": "None", "transform": [31, 0, 1, 0, -30, 2]},
        )


def test_reference_handles_nan_inf_denominator_and_negative_reflectance():
    red = np.array([[1.0, np.nan, -0.2, 1.0]], dtype=np.float32)
    nir = np.array([[3.0, 1.0, 0.2, np.inf]], dtype=np.float32)
    valid = np.ones((1, 4), dtype=bool)
    values, mask = _reference(red, nir, valid, valid)
    assert mask.tolist() == [[True, False, False, False]]
    assert values[0, 0] == pytest.approx(0.5)


def test_capture_rejects_pickle_npz_and_unsafe_manifest(tmp_path: Path):
    np.savez(tmp_path / "bad.npz", payload=np.array([object()], dtype=object))
    (tmp_path / "manifest.json").write_text(
        json.dumps({"reference_policy": "taskpilot.ndvi.reference.v1", "input_file": "../bad.npz"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        validate_capture(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("aoi_hash", "x"),
        ("band_roles", {"red": "SR_B4", "nir": "SR_B5"}),
        (
            "periods",
            {
                "a": {
                    "period_id": "a",
                    "start_utc": "not-a-date",
                    "end_utc": "2023-08-01T00:00:00Z",
                },
                "b": {
                    "period_id": "b",
                    "start_utc": "2024-07-01T00:00:00Z",
                    "end_utc": "2024-08-01T00:00:00Z",
                },
            },
        ),
        (
            "grid",
            {"crs": "EPSG:32649", "dimensions": [296, 264], "transform": [30, 0, 1, 0, -30, 2]},
        ),
    ],
)
def test_manifest_identity_tampering_is_rejected(tmp_path: Path, field, value):
    source = Path(
        r"C:\Users\Lin\AppData\Local\Temp\taskpilot-review-f9cd0b4eb4ab414696fb3fc515ffeca1\capture"
    )
    if not (source / "manifest.json").is_file():
        pytest.skip("retained real capture unavailable")
    shutil.copytree(source, tmp_path / "capture")
    manifest_path = tmp_path / "capture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_capture(tmp_path / "capture")


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda m: m["periods"]["a"].update(start_utc="2023-07-01T08:00:00+08:00"), "period"),
        (lambda m: m["provenance"].pop("period_a"), "provenance"),
        (
            lambda m: m["provenance"]["period_a"].update(
                scene_ids=["LC08_L2SP_123039_20230728_02_T1"]
            ),
            "scene",
        ),
        (lambda m: m["grid"].update(resolution_m=29.0), "grid"),
        (
            lambda m: m["provenance"]["period_a"]["scale_offset"].update(red=[1.0, 0.0]),
            "calibration",
        ),
        (
            lambda m: m["provenance"]["period_a"]["asset_key_map"][0].update(SR_B4="SR_B5"),
            "mapping",
        ),
        (lambda m: m["artifact_sha256"].update(ndvi_before_raster="bad"), "fingerprint"),
    ],
)
def test_manifest_cross_field_contract_is_rejected(tmp_path: Path, mutation, expected):
    source = Path(
        r"C:\Users\Lin\AppData\Local\Temp\taskpilot-review-f9cd0b4eb4ab414696fb3fc515ffeca1\capture"
    )
    if not (source / "manifest.json").is_file():
        pytest.skip("retained real capture unavailable")
    shutil.copytree(source, tmp_path / "capture")
    manifest_path = tmp_path / "capture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutation(manifest)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match=expected):
        validate_capture(tmp_path / "capture")


@pytest.mark.parametrize(
    ("artifact_name", "invalid_value"),
    [*[(name, 2) for name in _MASK_ARTIFACTS], ("ndvi_valid_before", 255)],
)
def test_validator_rejects_non_binary_mask_domain(
    tmp_path: Path, artifact_name: str, invalid_value: int
):
    rasterio = pytest.importorskip("rasterio")
    source = Path(
        r"C:\Users\Lin\AppData\Local\Temp\taskpilot-review-f9cd0b4eb4ab414696fb3fc515ffeca1\capture"
    )
    if not (source / "manifest.json").is_file():
        pytest.skip("retained real capture unavailable")
    capture = tmp_path / "capture"
    shutil.copytree(source, capture)
    raster_path = capture / f"{artifact_name}.tif"
    with rasterio.open(raster_path, "r+") as dataset:
        values = dataset.read(1)
        locations = np.argwhere(values == 1)
        if not len(locations):
            pytest.skip("capture has no binary-one pixel in selected mask")
        row, column = locations[0]
        values[row, column] = invalid_value
        dataset.write(values, 1)
    manifest_path = capture / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_sha256"][artifact_name] = hashlib.sha256(
        raster_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "scripts/validate_ndvi_science.py", str(capture)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "ERROR"


def test_validator_rejects_mask_mutation_without_hash_update(tmp_path: Path):
    rasterio = pytest.importorskip("rasterio")
    source = Path(
        r"C:\Users\Lin\AppData\Local\Temp\taskpilot-review-f9cd0b4eb4ab414696fb3fc515ffeca1\capture"
    )
    if not (source / "manifest.json").is_file():
        pytest.skip("retained real capture unavailable")
    capture = tmp_path / "capture"
    shutil.copytree(source, capture)
    raster_path = capture / "ndvi_valid_before.tif"
    with rasterio.open(raster_path, "r+") as dataset:
        values = dataset.read(1)
        locations = np.argwhere(values == 1)
        if not len(locations):
            pytest.skip("capture has no binary-one pixel")
        row, column = locations[0]
        values[row, column] = 2
        dataset.write(values, 1)
    result = subprocess.run(
        [sys.executable, "scripts/validate_ndvi_science.py", str(capture)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "ERROR"
