"""Bounded live Landsat preparation probe for the agent_service container.

This intentionally calls the same STAC discovery, deterministic scene selection,
and prepared-pair code used by the service.  Output is a small JSON document
containing only stable identities and operational counters; signed URLs are
never printed.

Run from the repository (or inside ``agent_service``):

    python scripts/probe_landsat_pair.py
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

# The service image copies ``geochange`` to /app; when running directly from a
# checkout the package lives under src/.  Keep both invocation forms on the
# same production code path without requiring an editable install.
_REPOSITORY_SRC = Path(__file__).resolve().parents[1] / "src"
if _REPOSITORY_SRC.is_dir():
    sys.path.insert(0, str(_REPOSITORY_SRC))

from geochange.aoi import load_trusted_aoi  # noqa: E402
from geochange.landsat import (  # noqa: E402
    DiscoveryLimits,
    PreparationFailure,
    default_period_pair,
    prepare_landsat_operation,
)

_SIGNED_QUERY = re.compile(r"([?&](?:sig|se|sp|sr|st|spr|sv)=[^&#\s]*)", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s\"']+", re.IGNORECASE)


def _sanitize(value: Any) -> Any:
    """Bound exception/provenance text without exposing href query credentials."""

    if isinstance(value, Mapping):
        return {str(key): _sanitize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        value = _SIGNED_QUERY.sub("", value)
        return _URL.sub("<redacted-url>", value)[:300]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:300]


def _scene_summary(scenes: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for scene in scenes:
        result.append(
            {
                "item_id": scene.item_id,
                "acquisition_date": scene.acquisition_datetime.date().isoformat(),
                "cloud_cover": round(float(scene.cloud_cover), 3),
                "intersection_ratio": round(float(scene.intersection_ratio), 6),
                "asset_identity_hashes": {
                    role: asset.identity_hash for role, asset in scene.assets.items()
                },
            }
        )
    return result


def run_probe() -> dict[str, Any]:
    aoi = load_trusted_aoi()
    periods = default_period_pair()
    # Keep the probe bounded even if the host or provider is degraded.  The
    # production default remains the source of the configured hard limits.
    limits = DiscoveryLimits(request_deadline_seconds=180.0)
    timeout = httpx.Timeout(connect=15.0, read=45.0, write=15.0, pool=15.0)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        _report, selected, prepared = prepare_landsat_operation(aoi, periods, limits, client=client)

    provenance = {
        period_id: dataset.provenance
        for period_id, dataset in (("a", prepared.period_a), ("b", prepared.period_b))
    }
    return {
        "probe": "landsat_pair",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "aoi": {
            "aoi_id": aoi.aoi_id,
            "admin_code": aoi.admin_code,
            "source_version": aoi.source_version,
            "source_hash": aoi.source_hash,
            "crs": aoi.crs,
            "area_m2": round(float(aoi.area_m2), 3),
        },
        "periods": {
            "a": {"scenes": _scene_summary(selected.period_a)},
            "b": {"scenes": _scene_summary(selected.period_b)},
        },
        "grid": prepared.pair_grid.model_dump(mode="json"),
        "coverage": {
            "a": prepared.period_a.coverage.model_dump(mode="json"),
            "b": prepared.period_b.coverage.model_dump(mode="json"),
            "common_preparation_pixels": int(prepared.common_preparation_valid_mask.sum()),
        },
        "provenance": _sanitize(provenance),
        "hard_limits_applied": _sanitize(prepared.hard_limits_applied),
        "operational_metrics": _sanitize(prepared.operational_metrics),
        "best_effort_warnings": list(prepared.best_effort_warnings),
    }


def main() -> int:
    try:
        print(json.dumps(run_probe(), ensure_ascii=False, sort_keys=True, indent=2))
    except PreparationFailure as exc:
        # Keep provider failures machine-readable and bounded.  In particular,
        # do not print the exception chain, which may contain a signed href.
        print(json.dumps({"probe": "landsat_pair", "error": _sanitize(exc.as_dict())}, indent=2))
        return 2
    except Exception as exc:  # pragma: no cover - live probe safety boundary
        print(
            json.dumps(
                {
                    "probe": "landsat_pair",
                    "error": {"code": type(exc).__name__, "message": _sanitize(str(exc))},
                },
                indent=2,
            )
        )
        return 3


if __name__ == "__main__":
    sys.exit(main())
