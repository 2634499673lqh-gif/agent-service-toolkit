"""A backup must fail on unreadable or linked sources rather than silently omit data."""

import importlib.util
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "backup_main_site", Path(__file__).parents[1] / "scripts/backup_main_site.py"
)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def test_inventory_includes_hidden_evidence_and_hashes(tmp_path):
    (tmp_path / ".dynamic_evidence.json").write_bytes(b"canonical")
    result = backup.inventory(tmp_path)
    assert result[".dynamic_evidence.json"]["bytes"] == 9
    assert result == backup.inventory(tmp_path)


def test_unreadable_directory_scan_fails(tmp_path, monkeypatch):
    def failed_walk(root, *, followlinks, onerror):
        onerror(PermissionError("unreadable"))
        return iter([])

    monkeypatch.setattr(backup.os, "walk", failed_walk)
    with pytest.raises(PermissionError):
        backup.inventory(tmp_path)


def test_unreadable_file_fails(tmp_path, monkeypatch):
    (tmp_path / "raster.tif").write_bytes(b"data")

    def failed_digest(path):
        raise PermissionError("unreadable")

    monkeypatch.setattr(backup, "digest", failed_digest)
    with pytest.raises(PermissionError):
        backup.inventory(tmp_path)


def test_linked_root_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "is_symlink", lambda _: True)
    with pytest.raises(RuntimeError, match="direct existing"):
        backup.inventory(tmp_path)


def test_backup_inside_repository_rejected_before_writes(tmp_path, monkeypatch):
    (tmp_path / ".git").mkdir()
    output = tmp_path / "backup"
    monkeypatch.setattr(sys, "argv", ["backup", "--output", str(output)])
    with pytest.raises(SystemExit, match="outside Git"):
        backup.main()
    assert not output.exists()
