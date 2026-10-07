import json
from pathlib import Path
from typing import Any

ALLOWED_ARTIFACTS = frozenset(
    {
        "ndvi_before",
        "ndvi_after",
        "ndvi_change",
        "ndwi_before",
        "ndwi_after",
        "ndwi_change",
        "ndbi_before",
        "ndbi_after",
        "ndbi_change",
        "ndvi_before_raster",
        "ndvi_after_raster",
        "ndvi_change_raster",
        "ndvi_valid_before",
        "ndvi_valid_after",
        "ndvi_common_comparison",
    }
)
CANONICAL_ARTIFACT_FILENAMES = {
    name: f"{name}{'.tif' if name.endswith('_raster') or name.startswith('ndvi_valid_') or name == 'ndvi_common_comparison' else '.png'}"
    for name in ALLOWED_ARTIFACTS
}
ARTIFACT_ROOT = Path("data/geochange-artifacts").resolve()
_DYNAMIC_EVIDENCE_FILENAME = ".dynamic_evidence.json"


def artifact_path(task_id: str, run_id: str, artifact_name: str) -> Path:
    if artifact_name not in ALLOWED_ARTIFACTS:
        raise ValueError("unsupported artifact")
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in (task_id, run_id)):
        raise ValueError("invalid artifact identity")
    artifact_root = ARTIFACT_ROOT.resolve()
    root = (artifact_root / task_id / run_id).resolve()
    if artifact_root not in root.parents:
        raise ValueError("artifact identity escapes root")
    path = (root / CANONICAL_ARTIFACT_FILENAMES[artifact_name]).resolve()
    if root not in path.parents:
        raise ValueError("artifact path escapes root")
    return path


def dynamic_evidence_path(task_id: str, run_id: str) -> Path:
    """Return the server-only binder path for one dynamic run."""

    if any(part in {".", ".."} or "/" in part or "\\" in part for part in (task_id, run_id)):
        raise ValueError("invalid dynamic evidence identity")
    artifact_root = ARTIFACT_ROOT.resolve()
    root = (artifact_root / task_id / run_id).resolve()
    if artifact_root not in root.parents:
        raise ValueError("dynamic evidence path escapes root")
    return root / _DYNAMIC_EVIDENCE_FILENAME


def write_dynamic_evidence(
    task_id: str,
    run_id: str,
    evidence: dict[str, Any],
) -> None:
    """Atomically persist the server-built dynamic terminal binder."""

    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("dynamic evidence exceeds the bounded limit")
    path = dynamic_evidence_path(task_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)


def load_dynamic_evidence(task_id: str, run_id: str) -> dict[str, Any] | None:
    """Load one server-owned binder; malformed or missing state fails closed."""

    try:
        path = dynamic_evidence_path(task_id, run_id)
        evidence = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            return None
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(encoded.encode("utf-8")) > 16_384:
            return None
        return evidence
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


__all__ = [
    "ALLOWED_ARTIFACTS",
    "ARTIFACT_ROOT",
    "CANONICAL_ARTIFACT_FILENAMES",
    "artifact_path",
    "dynamic_evidence_path",
    "load_dynamic_evidence",
    "write_dynamic_evidence",
]
