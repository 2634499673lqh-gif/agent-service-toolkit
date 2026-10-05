"""Resolve server-owned GeoChange data without depending on the cwd."""

import os
from pathlib import Path


def fixture_root(name: str) -> Path:
    """Return a validated fixture directory from an explicit root or repo layout."""

    configured = os.getenv("TASKPILOT_GEOCHANGE_FIXTURE_ROOT")
    candidates = []
    if configured:
        candidates.append(Path(configured).expanduser() / name)
    module_root = Path(__file__).resolve()
    candidates.extend(
        (
            module_root.parents[2] / "data" / "geochange-fixtures" / name,
            Path("/app/data/geochange-fixtures") / name,
            Path.cwd() / "data" / "geochange-fixtures" / name,
        )
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"GeoChange fixture '{name}' was not found; searched: {searched}")
