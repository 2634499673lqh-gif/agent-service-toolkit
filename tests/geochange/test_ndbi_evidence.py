import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).parents[2] / "data/geochange-fixtures/real-sentinel2-ndbi-v1"
NDWI_ROOT = Path(__file__).parents[2] / "data/geochange-fixtures/real-sentinel2-ndwi-v5"
SCIENCE_PATH = Path(__file__).parents[2] / "scripts/validate_ndbi_science.py"
SPEC = importlib.util.spec_from_file_location("validate_ndbi_science", SCIENCE_PATH)
assert SPEC and SPEC.loader
SCIENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCIENCE)


def _manifest() -> dict:
    manifest_bytes = (ROOT / "manifest.json").read_bytes()
    assert (
        hashlib.sha256(manifest_bytes).hexdigest() == (ROOT / "manifest.sha256").read_text().strip()
    )
    return json.loads(manifest_bytes)


def _copy_evidence(tmp_path: Path) -> tuple[Path, Path]:
    ndbi = tmp_path / "ndbi"
    ndwi = tmp_path / "ndwi"
    shutil.copytree(ROOT, ndbi)
    shutil.copytree(NDWI_ROOT, ndwi)
    return ndbi, ndwi


def _rewrite_manifest(root: Path, mutate) -> None:
    path = root / "manifest.json"
    data = json.loads(path.read_text())
    mutate(data)
    raw = json.dumps(data, indent=2, sort_keys=True).encode() + b"\n"
    path.write_bytes(raw)
    (root / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")


def _rewrite_npz(path: Path, mutate) -> None:
    with np.load(path, allow_pickle=False) as bundle:
        arrays = {name: bundle[name] for name in bundle.files}
    mutate(arrays)
    np.savez_compressed(path, **arrays)


def _assert_science_rejects(ndbi: Path, ndwi: Path) -> None:
    with pytest.raises(ValueError):
        SCIENCE.assess(ndbi, ndwi_root=ndwi)


def test_ndbi_fixture_has_independent_integrity_and_source_binding():
    manifest = _manifest()
    assert manifest["fixture_version"] == "geochange.real-sentinel2-ndbi.v1"
    assert manifest["preparation"]["b11_to_target_resampling"] == "nearest_neighbour_2x2_block"
    assert manifest["preparation"]["target_grid"] == {
        "crs": "EPSG:32650",
        "transform": [10, 0, 199980, 0, -10, 3400020],
        "resolution_m": 10,
        "dimensions": [24, 24],
    }
    assert manifest["preparation"]["b11_source_grid"] == {
        "crs": "EPSG:32650",
        "transform": [20, 0, 199980, 0, -20, 3400020],
        "resolution_m": 20,
        "dimensions": [5490, 5490],
    }
    assert "Sentinel_Data_Legal_Notice" in manifest["source"]["license_url"]
    for period, scene in manifest["scenes"].items():
        assert (
            hashlib.sha256((ROOT / scene["fixture_file"]).read_bytes()).hexdigest()
            == scene["fixture_sha256"]
        )
        item = json.loads((ROOT / f"{period}.item.json").read_text())
        assert (scene["item_id"], scene["collection"], scene["acquisition_datetime"]) == (
            item["id"],
            item["collection"],
            item["properties"]["datetime"],
        )
        b11_item = item["assets"]["swir16"]
        b11 = scene["b11_asset_identity"]
        assert b11["href"] == b11_item["href"]
        assert b11["source_shape"] == b11_item["proj:shape"]
        assert b11["source_transform"] == b11_item["proj:transform"]
        assert b11["source_resolution_m"] == b11_item["gsd"]
        assert b11["scale"] == b11_item["raster:bands"][0]["scale"]
        assert b11["offset"] == b11_item["raster:bands"][0]["offset"]
        assert b11["nodata"] == b11_item["raster:bands"][0]["nodata"]
        with np.load(ROOT / scene["fixture_file"], allow_pickle=False) as bundle:
            assert set(bundle.files) == {"nir", "scl", "coverage", "swir16_native_20m"}
            assert bundle["nir"].shape == bundle["coverage"].shape == (24, 24)
            assert bundle["scl"].shape == bundle["swir16_native_20m"].shape == (12, 12)
            assert bundle["swir16_native_20m"].dtype == np.uint16
            assert (
                hashlib.sha256(bundle["swir16_native_20m"].tobytes()).hexdigest()
                == b11["crop_sha256"]
            )


def test_b11_alignment_and_nearest_resampling_preserve_native_blocks():
    manifest = _manifest()
    for period, scene in manifest["scenes"].items():
        with np.load(ROOT / scene["fixture_file"], allow_pickle=False) as bundle:
            native = bundle["swir16_native_20m"]
            expanded = np.repeat(np.repeat(native, 2, axis=0), 2, axis=1)
            assert expanded.shape == (24, 24)
            for row in range(12):
                for col in range(12):
                    np.testing.assert_array_equal(
                        expanded[row * 2 : row * 2 + 2, col * 2 : col * 2 + 2], native[row, col]
                    )
            assert scene["b11_asset_identity"]["window_pixel_origin"] == [2060, 620]
            assert scene["b08_asset_identity"]["window_pixel_origin"] == [4120, 1240]
            assert scene["b11_asset_identity"]["source_transform"][2:] == [199980, 0, -20, 3400020]


def _period(period: str):
    manifest = _manifest()
    with np.load(ROOT / manifest["scenes"][period]["fixture_file"], allow_pickle=False) as bundle:
        b08 = bundle["nir"].astype(np.float32) * 0.0001 - 0.1
        b11 = np.repeat(
            np.repeat(bundle["swir16_native_20m"].astype(np.float32) * 0.0001 - 0.1, 2, 0), 2, 1
        )
        scl = np.repeat(np.repeat(bundle["scl"], 2, 0), 2, 1)
        valid = (
            bundle["coverage"].astype(bool)
            & np.isin(scl, [4, 5, 6])
            & np.isfinite(b08)
            & np.isfinite(b11)
            & (b08 >= 0)
            & (b11 >= 0)
            & ((b08 + b11) > 0)
        )
        ndbi = np.full(b08.shape, np.nan, dtype=np.float32)
        ndbi[valid] = (b11[valid] - b08[valid]) / (b11[valid] + b08[valid])
    return ndbi, valid


def test_common_valid_mask_and_deterministic_ndbi_evidence():
    first = SCIENCE.assess()
    second = SCIENCE.assess()
    assert first == second
    assert first["periods"]["period_a"]["valid_pixels"] == 338
    assert first["periods"]["period_b"]["valid_pixels"] == 395
    assert first["common_valid_pixels"] == 322
    assert first["common_mean_delta_ndbi"] == pytest.approx(0.1404718757)
    a, valid_a = _period("period_a")
    b, valid_b = _period("period_b")
    common = valid_a & valid_b
    assert int(common.sum()) == first["common_valid_pixels"]
    assert np.all(np.isfinite(a[common]))
    assert np.all(np.isfinite(b[common]))
    np.testing.assert_allclose(np.mean((b - a)[common]), first["common_mean_delta_ndbi"])


def test_mask_and_zero_denominator_fail_closed():
    b08 = np.array([[0.2, 0.0], [0.1, np.nan]], dtype=np.float32)
    b11 = np.array([[0.2, 0.0], [-0.1, 0.1]], dtype=np.float32)
    denominator = b08 + b11
    valid = np.isfinite(b08) & np.isfinite(b11) & (b08 >= 0) & (b11 >= 0) & (denominator != 0)
    assert valid.tolist() == [[True, False], [False, False]]
    ndbi = np.full(b08.shape, np.nan, dtype=np.float32)
    ndbi[valid] = (b11[valid] - b08[valid]) / denominator[valid]
    assert np.isfinite(ndbi[0, 0])
    assert np.isnan(ndbi[0, 1]) and np.isnan(ndbi[1, 0]) and np.isnan(ndbi[1, 1])


def test_manifest_mutations_fail_even_when_sidecar_is_regenerated(tmp_path):
    cases = (
        ("crs", lambda data: data["preparation"]["target_grid"].__setitem__("crs", "EPSG:4326")),
        (
            "transform",
            lambda data: data["preparation"]["target_grid"]["transform"].__setitem__(0, 20),
        ),
    )
    for name, mutate in cases:
        ndbi, ndwi = _copy_evidence(tmp_path / name)
        _rewrite_manifest(ndbi, mutate)
        _assert_science_rejects(ndbi, ndwi)


def test_ndbi_npz_pixel_mutation_fails_at_science_entrypoint(tmp_path):
    ndbi, ndwi = _copy_evidence(tmp_path)
    _rewrite_npz(
        ndbi / "period_a.npz",
        lambda arrays: arrays["swir16_native_20m"].__setitem__((0, 0), 9999),
    )
    _assert_science_rejects(ndbi, ndwi)


def test_stac_mutation_and_missing_required_file_fail(tmp_path):
    ndbi, ndwi = _copy_evidence(tmp_path / "stac")
    item = ndbi / "period_b.item.json"
    data = json.loads(item.read_text())
    data["properties"]["datetime"] = "2024-07-31T03:19:32.078000Z"
    item.write_text(json.dumps(data, indent=2) + "\n")
    _assert_science_rejects(ndbi, ndwi)

    ndbi, ndwi = _copy_evidence(tmp_path / "missing")
    (ndbi / "period_b.item.json").unlink()
    _assert_science_rejects(ndbi, ndwi)


def test_reused_ndwi_manifest_and_pixels_are_pinned(tmp_path):
    ndbi, ndwi = _copy_evidence(tmp_path / "ndwi")
    manifest = json.loads((ndwi / "manifest.json").read_text())
    manifest["fixture_version"] = "tampered"
    (ndwi / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    _assert_science_rejects(ndbi, ndwi)

    ndbi, ndwi = _copy_evidence(tmp_path / "ndwi_pixels")
    _rewrite_npz(
        ndwi / "period_a.npz",
        lambda arrays: arrays["nir"].__setitem__((0, 0), 9999),
    )
    _assert_science_rejects(ndbi, ndwi)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_transform", [10, 0, 199980, 0, -10, 3400020]),
        ("source_resolution_m", 10),
        ("scale", 1.0),
        ("offset", 0.0),
        ("nodata", 1),
    ],
)
def test_b11_asset_contract_mutations_fail_closed(tmp_path, field, value):
    ndbi, ndwi = _copy_evidence(tmp_path / field)
    _rewrite_manifest(
        ndbi,
        lambda data: data["scenes"]["period_a"]["b11_asset_identity"].__setitem__(field, value),
    )
    _assert_science_rejects(ndbi, ndwi)


def test_array_geometry_and_coverage_mutations_fail_closed(tmp_path):
    ndbi, ndwi = _copy_evidence(tmp_path / "arrays")
    _rewrite_npz(
        ndbi / "period_b.npz",
        lambda arrays: arrays.__setitem__("coverage", arrays["coverage"][:23, :]),
    )
    _assert_science_rejects(ndbi, ndwi)

    ndbi, ndwi = _copy_evidence(tmp_path / "coverage")
    _rewrite_manifest(
        ndbi,
        lambda data: data["scenes"]["period_b"].__setitem__("coverage_pixels", 0),
    )
    _assert_science_rejects(ndbi, ndwi)
