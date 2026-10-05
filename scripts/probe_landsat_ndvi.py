"""Bounded real-provider B smoke: A preparation handoff through NDVI verifier."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geochange.aoi import load_trusted_aoi
from geochange.landsat import DiscoveryLimits, default_period_pair, prepare_landsat_operation
from geochange.landsat_ndvi import compute_landsat_ndvi_product, verify_landsat_ndvi_product


def main() -> int:
    with httpx.Client(
        timeout=httpx.Timeout(connect=15, read=45, write=15, pool=15), follow_redirects=False
    ) as client:
        _report, selected, prepared = prepare_landsat_operation(
            load_trusted_aoi(), default_period_pair(), DiscoveryLimits(), client=client
        )
    artifact_dir = os.environ.get("TASKPILOT_NDVI_ARTIFACT_DIR") or tempfile.mkdtemp(
        prefix="taskpilot-ndvi-"
    )
    product = compute_landsat_ndvi_product(prepared, artifact_dir=artifact_dir)
    verification = verify_landsat_ndvi_product(product, prepared)
    print(
        json.dumps(
            {
                "selected": [
                    [scene.item_id for scene in selected.period_a],
                    [scene.item_id for scene in selected.period_b],
                ],
                "metrics": product.metrics,
                "verification": verification,
                "artifacts": product.artifacts,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if verification["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
