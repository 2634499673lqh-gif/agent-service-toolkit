from pathlib import Path

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


__all__ = [
    "ALLOWED_ARTIFACTS",
    "ARTIFACT_ROOT",
    "CANONICAL_ARTIFACT_FILENAMES",
    "artifact_path",
]
