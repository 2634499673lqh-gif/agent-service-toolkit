import json
import shutil
from pathlib import Path
from typing import Any
from uuid import UUID

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
_ATTEMPTS_DIRECTORY = "attempts"
_CURRENT_ATTEMPT_FILENAME = ".current_attempt"


def _run_root(task_id: str, run_id: str) -> Path:
    artifact_root = ARTIFACT_ROOT.resolve()
    root = (artifact_root / task_id / run_id).resolve()
    if artifact_root not in root.parents:
        raise ValueError("artifact identity escapes root")
    return root


def attempt_artifact_root(task_id: str, run_id: str, attempt_id: str | UUID) -> Path:
    """Return an isolated working root for one server-issued execution attempt."""

    task = str(UUID(str(task_id)))
    run = str(UUID(str(run_id)))
    attempt = str(UUID(str(attempt_id)))
    root = _run_root(task, run) / _ATTEMPTS_DIRECTORY / attempt
    if _run_root(task, run) not in root.parents:
        raise ValueError("attempt identity escapes root")
    return root


def _resolved_run_root(task_id: str, run_id: str) -> Path:
    root = _run_root(task_id, run_id)
    pointer = root / _CURRENT_ATTEMPT_FILENAME
    if not pointer.exists():
        return root
    try:
        attempt = UUID(pointer.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return root / _ATTEMPTS_DIRECTORY / "__invalid_attempt_pointer__"
    # A present but malformed or missing attempt directory must fail closed;
    # callers will see missing artifacts instead of falling back to old files.
    return root / _ATTEMPTS_DIRECTORY / str(attempt)


def publish_attempt_pointer(task_id: str, run_id: str, attempt_id: str | UUID) -> None:
    """Atomically expose a completely verified attempt directory."""

    source = attempt_artifact_root(task_id, run_id, attempt_id)
    if not source.is_dir():
        raise ValueError("attempt artifact directory is missing")
    root = _run_root(str(UUID(str(task_id))), str(UUID(str(run_id))))
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f"{_CURRENT_ATTEMPT_FILENAME}.{UUID(str(attempt_id))}.tmp"
    try:
        temporary.write_text(str(UUID(str(attempt_id))), encoding="ascii")
        temporary.replace(root / _CURRENT_ATTEMPT_FILENAME)
    finally:
        temporary.unlink(missing_ok=True)


def current_attempt_id(task_id: str, run_id: str) -> UUID | None:
    """Read the server-written current-attempt pointer, if valid."""

    pointer = _run_root(str(UUID(str(task_id))), str(UUID(str(run_id)))) / _CURRENT_ATTEMPT_FILENAME
    try:
        return UUID(pointer.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        return None


def invalidate_attempt_pointer(task_id: str, run_id: str) -> None:
    """Fence the previously exposed attempt before a reset starts."""

    root = _run_root(str(UUID(str(task_id))), str(UUID(str(run_id))))
    root.mkdir(parents=True, exist_ok=True)
    pointer = root / _CURRENT_ATTEMPT_FILENAME
    temporary = root / f"{_CURRENT_ATTEMPT_FILENAME}.invalid.tmp"
    try:
        temporary.write_text("invalid", encoding="ascii")
        temporary.replace(pointer)
    finally:
        temporary.unlink(missing_ok=True)


def discard_attempt_artifacts(path: str | Path) -> None:
    """Remove one completed worker's private directory after it is fenced."""

    candidate = Path(path).resolve()
    if candidate.parent.name != _ATTEMPTS_DIRECTORY:
        raise ValueError("invalid attempt artifact directory")
    UUID(candidate.name)
    run_root = candidate.parent.parent
    artifact_root = ARTIFACT_ROOT.resolve()
    if artifact_root not in run_root.parents:
        raise ValueError("attempt artifact directory escapes root")
    shutil.rmtree(candidate, ignore_errors=True)


def artifact_path(task_id: str, run_id: str, artifact_name: str) -> Path:
    if artifact_name not in ALLOWED_ARTIFACTS:
        raise ValueError("unsupported artifact")
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in (task_id, run_id)):
        raise ValueError("invalid artifact identity")
    root = _resolved_run_root(task_id, run_id)
    path = (root / CANONICAL_ARTIFACT_FILENAMES[artifact_name]).resolve()
    if root not in path.parents:
        raise ValueError("artifact path escapes root")
    return path


def dynamic_evidence_path(task_id: str, run_id: str) -> Path:
    """Return the server-only binder path for one dynamic run."""

    if any(part in {".", ".."} or "/" in part or "\\" in part for part in (task_id, run_id)):
        raise ValueError("invalid dynamic evidence identity")
    root = _resolved_run_root(task_id, run_id)
    artifact_root = ARTIFACT_ROOT.resolve()
    if artifact_root not in root.parents:
        raise ValueError("dynamic evidence path escapes root")
    return root / _DYNAMIC_EVIDENCE_FILENAME


def write_dynamic_evidence(
    task_id: str,
    run_id: str,
    evidence: dict[str, Any],
    *,
    attempt_id: str | UUID | None = None,
) -> None:
    """Atomically persist the server-built dynamic terminal binder."""

    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("dynamic evidence exceeds the bounded limit")
    path = (
        attempt_artifact_root(task_id, run_id, attempt_id) / _DYNAMIC_EVIDENCE_FILENAME
        if attempt_id is not None
        else dynamic_evidence_path(task_id, run_id)
    )
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
    "attempt_artifact_root",
    "current_attempt_id",
    "discard_attempt_artifacts",
    "dynamic_evidence_path",
    "invalidate_attempt_pointer",
    "load_dynamic_evidence",
    "publish_attempt_pointer",
    "write_dynamic_evidence",
]
