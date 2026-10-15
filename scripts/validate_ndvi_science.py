"""Independently validate one explicitly captured Landsat NDVI evidence package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geochange.ndvi_validation import validate_capture


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        report = validate_capture(args.package)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        report = {
            "status": "ERROR",
            "reference_policy": "taskpilot.ndvi.reference.v1",
            "error": str(error)[:240],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
