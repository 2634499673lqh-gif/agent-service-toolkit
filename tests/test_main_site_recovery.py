"""Recovery planning never grants identity from a filename or overwrites live data."""

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

SPEC = importlib.util.spec_from_file_location(
    "main_site_recovery", Path(__file__).parents[1] / "scripts/main_site_recovery.py"
)
assert SPEC and SPEC.loader
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


@pytest.fixture
def setup(tmp_path):
    live, source = tmp_path / "live", tmp_path / "source"
    live.mkdir()
    source.mkdir()
    task, run = str(uuid4()), str(uuid4())
    relative = f"{task}/{run}/ndvi_before.png"
    path = source / relative
    path.parent.mkdir(parents=True)
    path.write_bytes(b"historical bytes")
    refs = [
        {
            "task_id": task,
            "run_id": run,
            "references": {"ndvi_before": "ndvi_before"},
            "checksums": {"ndvi_before": recovery.sha256(path)},
        }
    ]
    return live, source, relative, path, refs


def test_verified_plan_is_deterministic_read_only_and_noop_when_identical(setup):
    live, source, relative, path, refs = setup
    plan = recovery.inventory(refs, live, [source])
    assert plan == recovery.inventory(refs, live, [source])
    assert plan["summary"] == {"FOUND_VERIFIED": 1}
    result = recovery.dry_run(plan)
    assert result["writes_performed"] == 0
    assert result["decisions"][0]["action"] == "COPY_AFTER_APPROVAL"
    assert not (live / relative).exists()
    target = live / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(path.read_bytes())
    assert recovery.dry_run(plan)["decisions"][0]["action"] == "NOOP_IDENTICAL"


@pytest.mark.parametrize("checksum", [None, "0" * 64])
def test_filename_and_path_do_not_prove_identity(setup, checksum):
    live, source, _, _, refs = setup
    refs[0]["checksums"] = {"ndvi_before": checksum} if checksum else {}
    plan = recovery.inventory(refs, live, [source])
    assert plan["summary"] == {"FOUND_UNVERIFIED": 1}
    assert recovery.dry_run(plan)["decisions"] == []


def test_source_mutation_fails_closed(setup):
    live, source, _, path, refs = setup
    plan = recovery.inventory(refs, live, [source])
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed"):
        recovery.dry_run(plan)


@pytest.mark.parametrize("size", [0, 2_000_001])
def test_invalid_size_never_qualifies_for_recovery(setup, size):
    live, source, _, path, refs = setup
    path.write_bytes(b"x" * size)
    refs[0]["checksums"]["ndvi_before"] = recovery.sha256(path)
    assert recovery.inventory(refs, live, [source])["summary"] == {"UNSAFE": 1}


def test_collision_never_overwrites(setup):
    live, source, relative, _, refs = setup
    plan = recovery.inventory(refs, live, [source])
    target = live / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"other bytes")
    assert recovery.dry_run(plan)["blocked"]
    assert target.read_bytes() == b"other bytes"


@pytest.mark.parametrize("relative", ["../escape", "/absolute", "C:/absolute", "a\\b"])
def test_traversal_rejected(tmp_path, relative):
    with pytest.raises(ValueError):
        recovery.bounded_path(tmp_path, relative)


def test_invalid_attempt_pointer_never_falls_back(setup):
    live, source, relative, _, refs = setup
    root = (live / relative).parent
    root.mkdir(parents=True)
    (root / ".current_attempt").write_text("invalid", encoding="ascii")
    assert recovery.inventory(refs, live, [source])["summary"] == {"UNSAFE": 1}


def test_attempt_change_after_plan_fails_closed(setup):
    live, source, relative, _, refs = setup
    plan = recovery.inventory(refs, live, [source])
    root = (live / relative).parent
    root.mkdir(parents=True)
    (root / ".current_attempt").write_text(str(uuid4()), encoding="ascii")
    with pytest.raises(ValueError, match="attempt changed"):
        recovery.dry_run(plan)


def test_untrusted_source_and_duplicate_target_rejected(setup, tmp_path):
    live, source, _, _, refs = setup
    plan = recovery.inventory(refs, live, [source])
    plan["entries"].append(plan["entries"][0].copy())
    with pytest.raises(ValueError, match="duplicate"):
        recovery.dry_run(plan)
    plan["entries"].pop()
    rogue = tmp_path / "rogue.png"
    rogue.write_bytes(b"historical bytes")
    plan["entries"][0]["source"] = str(rogue)
    with pytest.raises(ValueError, match="untrusted"):
        recovery.dry_run(plan)


def test_symlink_path_rejected(setup, monkeypatch):
    live, source, _, _, refs = setup
    original = Path.is_symlink
    monkeypatch.setattr(
        Path, "is_symlink", lambda p: p == source / refs[0]["task_id"] or original(p)
    )
    assert recovery.inventory(refs, live, [source])["summary"] == {"UNSAFE": 1}
