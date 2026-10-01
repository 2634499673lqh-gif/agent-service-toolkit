from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

MAX_PIXELS = 4_000_000
MAX_ARTIFACT_BYTES = 2_000_000


@dataclass(frozen=True)
class VegetationChange:
    ndvi_a: np.ndarray
    ndvi_b: np.ndarray
    delta: np.ndarray
    valid_mask: np.ndarray
    pixel_area_m2: float
    artifacts: dict[str, str]


def _ndvi(red: np.ndarray, nir: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if red.shape != nir.shape or red.ndim != 2:
        raise ValueError("Red and NIR grids must be matching two-dimensional arrays")
    if red.size == 0 or red.size > MAX_PIXELS:
        raise ValueError("raster size is outside the bounded processing limit")
    red = np.asarray(red, dtype=np.float32)
    nir = np.asarray(nir, dtype=np.float32)
    finite = np.isfinite(red) & np.isfinite(nir)
    denominator = nir + red
    valid = finite & np.isfinite(denominator) & (denominator != 0)
    out = np.full(red.shape, np.nan, dtype=np.float32)
    out[valid] = (nir[valid] - red[valid]) / denominator[valid]
    valid &= out >= -1.00001
    valid &= out <= 1.00001
    out[~valid] = np.nan
    return out, valid


def _write_png(array: np.ndarray, path: Path) -> None:
    scaled = np.nan_to_num((array + 1.0) * 127.5, nan=0.0, posinf=255.0, neginf=0.0)
    image = Image.fromarray(np.clip(scaled, 0, 255).astype(np.uint8), mode="L")
    image.save(path, format="PNG", optimize=True)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        path.unlink(missing_ok=True)
        raise ValueError("generated artifact exceeds the bounded size")


def compute_vegetation_change(
    red_a: Any,
    nir_a: Any,
    red_b: Any,
    nir_b: Any,
    *,
    pixel_area_m2: float = 100.0,
    artifact_dir: str | Path | None = None,
) -> VegetationChange:
    if not np.isfinite(pixel_area_m2) or pixel_area_m2 <= 0 or pixel_area_m2 > 1_000_000:
        raise ValueError("pixel_area_m2 is invalid")
    ndvi_a, valid_a = _ndvi(np.asarray(red_a), np.asarray(nir_a))
    ndvi_b, valid_b = _ndvi(np.asarray(red_b), np.asarray(nir_b))
    valid = valid_a & valid_b
    if not np.any(valid):
        raise ValueError("no valid pixels remain for analysis")
    delta = np.full(ndvi_a.shape, np.nan, dtype=np.float32)
    delta[valid] = ndvi_b[valid] - ndvi_a[valid]
    artifacts: dict[str, str] = {}
    if artifact_dir is not None:
        root = Path(artifact_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        for name, array in (
            ("ndvi_before.png", ndvi_a),
            ("ndvi_after.png", ndvi_b),
            ("ndvi_change.png", delta),
        ):
            path = root / name
            _write_png(array, path)
            artifacts[name.removesuffix(".png")] = path.name
    return VegetationChange(ndvi_a, ndvi_b, delta, valid, float(pixel_area_m2), artifacts)


__all__ = ["VegetationChange", "compute_vegetation_change"]
