from pathlib import Path

ALLOWED_ARTIFACTS = frozenset({"ndvi_before", "ndvi_after", "ndvi_change"})
ARTIFACT_ROOT = Path("data/geochange-artifacts").resolve()


def artifact_path(task_id: str, run_id: str, artifact_name: str) -> Path:
    if artifact_name not in ALLOWED_ARTIFACTS:
        raise ValueError("unsupported artifact")
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in (task_id, run_id)):
        raise ValueError("invalid artifact identity")
    artifact_root = ARTIFACT_ROOT.resolve()
    root = (artifact_root / task_id / run_id).resolve()
    if artifact_root not in root.parents:
        raise ValueError("artifact identity escapes root")
    path = (root / f"{artifact_name}.png").resolve()
    if root not in path.parents:
        raise ValueError("artifact path escapes root")
    return path


__all__ = ["ALLOWED_ARTIFACTS", "ARTIFACT_ROOT", "artifact_path"]
