"""Deterministic exploratory NDWI threshold and change assessment.

The SCL labels are reported as a comparison reference only.  This script never
promotes a threshold to a scientific contract and never changes runtime code.
"""

import json
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1] / "data/geochange-fixtures/real-sentinel2-ndwi-v5"
THRESHOLDS = (0.0, 0.10, 0.15, 0.20, 0.25, 0.30)


def load_period(period: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with np.load(ROOT / f"{period}.npz", allow_pickle=False) as bundle:
        green = bundle["green"].astype(np.float32) * 0.0001 - 0.1
        nir = bundle["nir"].astype(np.float32) * 0.0001 - 0.1
        scl = np.repeat(np.repeat(bundle["scl"], 2, axis=0), 2, axis=1)
        coverage = bundle["coverage"].astype(bool)
    valid = (
        coverage
        & np.isin(scl, [4, 5, 6])
        & np.isfinite(green)
        & np.isfinite(nir)
        & (green >= 0)
        & (nir >= 0)
        & ((green + nir) > 0)
    )
    ndwi = np.full(green.shape, np.nan, dtype=np.float32)
    ndwi[valid] = (green[valid] - nir[valid]) / (green[valid] + nir[valid])
    return ndwi, valid, scl == 6


def confusion(
    ndwi: np.ndarray, valid: np.ndarray, water_reference: np.ndarray, threshold: float
) -> dict[str, int | float]:
    predicted = valid & (ndwi > threshold)
    tp = int((predicted & water_reference).sum())
    fp = int((predicted & valid & ~water_reference).sum())
    fn = int((~predicted & valid & water_reference).sum())
    tn = int((~predicted & valid & ~water_reference).sum())
    denominator = 2 * tp + fp + fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "f1": (2 * tp / denominator) if denominator else 0.0,
        "predicted_area_m2": int(predicted.sum()) * 100,
    }


def assess() -> dict[str, Any]:
    periods = {period: load_period(period) for period in ("period_a", "period_b")}
    result: dict[str, Any] = {"thresholds": list(THRESHOLDS), "periods": {}}
    for period, (ndwi, valid, water_reference) in periods.items():
        result["periods"][period] = {
            "valid_pixels": int(valid.sum()),
            "scl_water_pixels": int((valid & water_reference).sum()),
            "scl_nonwater_pixels": int((valid & ~water_reference).sum()),
            "thresholds": {str(t): confusion(ndwi, valid, water_reference, t) for t in THRESHOLDS},
            "quadrants_at_020": [
                confusion(
                    ndwi[r * 12 : (r + 1) * 12, c * 12 : (c + 1) * 12],
                    valid[r * 12 : (r + 1) * 12, c * 12 : (c + 1) * 12],
                    water_reference[r * 12 : (r + 1) * 12, c * 12 : (c + 1) * 12],
                    0.20,
                )
                for r in range(2)
                for c in range(2)
            ],
        }
    common = periods["period_a"][1] & periods["period_b"][1]
    result["common_valid_pixels"] = int(common.sum())
    result["change_sensitivity"] = {
        str(t): {
            "period_a_water_pixels": int(((periods["period_a"][0] > t) & common).sum()),
            "period_b_water_pixels": int(((periods["period_b"][0] > t) & common).sum()),
        }
        for t in THRESHOLDS
    }
    return result


if __name__ == "__main__":
    print(json.dumps(assess(), indent=2, sort_keys=True))
