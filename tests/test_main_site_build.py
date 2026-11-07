"""Windows checkout settings must not change scientific fixture bytes in an image."""

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_main_site", Path(__file__).parents[1] / "scripts/build_main_site.py"
)
assert SPEC and SPEC.loader
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def test_export_preserves_approved_blob_bytes(tmp_path):
    context = tmp_path / "context"
    build.export_source(context)
    relative = "data/geochange-fixtures/real-sentinel2-ndbi-v1/manifest.json"
    blob = subprocess.check_output(
        ["git", "-C", str(build.REPOSITORY), "show", build.APPROVED_REVISION + ":" + relative]
    )
    assert (context / relative).read_bytes() == blob
    assert (
        hashlib.sha256(blob).hexdigest()
        == (context / "data/geochange-fixtures/real-sentinel2-ndbi-v1/manifest.sha256")
        .read_text()
        .strip()
    )


def test_packaging_preserves_every_approved_integrity_anchor(tmp_path):
    context = tmp_path / "context"
    build.export_source(context)
    evidence = build.materialize_fixture_bytes(context)
    assert len(evidence) == 9
    for row in evidence:
        assert (
            hashlib.sha256((context / row["path"]).read_bytes()).hexdigest()
            == row["packaged_sha256"]
        )
    path = context / "data/geochange-fixtures/real-sentinel2-ndbi-v1/period_a.npz"
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="unchanged integrity anchor"):
        build.materialize_fixture_bytes(context)


def test_build_inside_a_repository_rejected_before_writes(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    output = tmp_path / "build"
    monkeypatch.setattr(sys, "argv", ["build", "--output", str(output)])
    with pytest.raises(SystemExit):
        build.main()
    assert not output.exists()
