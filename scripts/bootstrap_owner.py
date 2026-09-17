"""Thin entry point for the TaskPilot owner bootstrap CLI.

All logic lives in ``service.bootstrap_cli`` so it stays type-checked and
testable; this file only makes the command runnable as ``python scripts/bootstrap_owner.py``.

Usage:
    uv run python scripts/bootstrap_owner.py --organization-name "Acme" --email owner@example.com

The password is requested twice from a hidden prompt; argument values are never
echoed back on failure.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from service.bootstrap_cli import main  # noqa: E402 - path setup must precede the import

if __name__ == "__main__":
    raise SystemExit(main())
