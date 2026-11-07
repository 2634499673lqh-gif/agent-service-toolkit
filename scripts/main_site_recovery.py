"""Inventory historical references and verify a recovery plan without live writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from geochange.artifacts import CANONICAL_ARTIFACT_FILENAMES


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bounded_path(root: Path, relative: str) -> Path:
    """Reject traversal and links, including dangling links and Windows junctions."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in {"..", "."} for p in parts):
        raise ValueError("unsafe relative path")
    if "\\" in relative or ":" in relative:
        raise ValueError("unsafe relative path")
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink() or current.is_junction():
            raise ValueError("linked recovery path")
    resolved = current.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("recovery path escapes root")
    return resolved


def run_relative(root: Path, task: str, run: str) -> str:
    relative = f"{UUID(task)}/{UUID(run)}"
    pointer = bounded_path(root, relative + "/.current_attempt")
    if pointer.exists():
        attempt = UUID(pointer.read_text(encoding="ascii").strip())
        relative += f"/attempts/{attempt}"
    return relative


def inventory(references: list[dict[str, Any]], live: Path, roots: list[Path]) -> dict[str, Any]:
    live = live.resolve(strict=True)
    roots = sorted({root.resolve(strict=True) for root in roots}, key=str)
    rows: list[dict[str, Any]] = []
    for run in sorted(references, key=lambda r: (r["task_id"], r["run_id"])):
        task, run_id = str(UUID(run["task_id"])), str(UUID(run["run_id"]))
        for name, value in sorted(run["references"].items()):
            row: dict[str, Any] = {"task_id": task, "run_id": run_id, "artifact": name}
            rows.append(row)
            if name not in CANONICAL_ARTIFACT_FILENAMES or value != name:
                row.update(classification="UNSAFE", reason="noncanonical reference")
                continue
            expected = (run.get("checksums") or {}).get(name)
            if expected is not None and (
                not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
            ):
                row.update(classification="UNSAFE", reason="invalid authoritative checksum")
                continue
            row["expected_sha256"] = expected
            filename = CANONICAL_ARTIFACT_FILENAMES[name]
            max_bytes = 16_000_000 if filename.endswith(".tif") else 2_000_000
            try:
                target = run_relative(live, task, run_id) + "/" + filename
                path = bounded_path(live, target)
            except (ValueError, OSError, UnicodeError):
                row.update(classification="UNSAFE", reason="invalid live attempt authority")
                continue
            row["target_relative"] = target
            if path.exists():
                if not path.is_file() or not 0 < path.stat().st_size <= max_bytes:
                    row.update(classification="UNSAFE", reason="non-file or invalid-size target")
                    continue
                digest = sha256(path)
                row.update(source=str(path), sha256=digest, bytes=path.stat().st_size)
                row["classification"] = "UNSAFE" if expected and digest != expected else "PRESENT"
                row["identity_verified"] = bool(expected and digest == expected)
                continue
            candidates = []
            unsafe = False
            for root in roots:
                try:
                    source_relative = run_relative(root, task, run_id) + "/" + filename
                    candidate = bounded_path(root, source_relative)
                    if candidate.is_file():
                        if not 0 < candidate.stat().st_size <= max_bytes:
                            unsafe = True
                            continue
                        candidates.append(
                            {
                                "source": str(candidate),
                                "sha256": sha256(candidate),
                                "bytes": candidate.stat().st_size,
                            }
                        )
                except (ValueError, OSError, UnicodeError):
                    unsafe = True
            row["candidates"] = candidates
            verified = [c for c in candidates if expected and c["sha256"] == expected]
            if verified:
                row.update(verified[0], classification="FOUND_VERIFIED", identity_verified=True)
            elif candidates:
                row.update(
                    classification="FOUND_UNVERIFIED",
                    identity_verified=False,
                    reason="checksum mismatch" if expected else "no authoritative digest",
                )
            else:
                row.update(classification="UNSAFE" if unsafe else "NOT_FOUND")
    summary: dict[str, int] = {}
    for row in rows:
        key = row["classification"]
        summary[key] = summary.get(key, 0) + 1
    return {
        "format": "taskpilot.main-recovery.v1",
        "live_root": str(live),
        "search_roots": [str(root) for root in roots],
        "summary": summary,
        "entries": rows,
        "collision_policy": "never overwrite; identical target is a no-op",
        "rollback": "retain originals; remove only newly copied, unchanged digest-matched files",
        "sidecars": "never synthesize evidence or attempt pointers",
    }


def dry_run(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("format") != "taskpilot.main-recovery.v1":
        raise ValueError("unsupported recovery manifest")
    root = Path(plan["live_root"]).resolve(strict=True)
    approved_roots = [Path(p).resolve(strict=True) for p in plan["search_roots"]]
    decisions = []
    seen: set[str] = set()
    for row in plan["entries"]:
        if row["classification"] != "FOUND_VERIFIED":
            continue
        relative = row["target_relative"]
        parts = Path(relative).parts
        if len(parts) not in {3, 5} or parts[0] != str(UUID(row["task_id"])):
            raise ValueError("invalid target identity")
        if parts[1] != str(UUID(row["run_id"])):
            raise ValueError("invalid run identity")
        if parts[-1] != CANONICAL_ARTIFACT_FILENAMES[row["artifact"]]:
            raise ValueError("invalid artifact identity")
        if len(parts) == 5 and (parts[2] != "attempts" or parts[3] != str(UUID(parts[3]))):
            raise ValueError("invalid attempt identity")
        if relative in seen:
            raise ValueError("duplicate target")
        seen.add(relative)
        expected = row["expected_sha256"]
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("authoritative digest required")
        source = Path(row["source"])
        safe = False
        for candidate_root in approved_roots:
            if source.is_relative_to(candidate_root):
                source = bounded_path(candidate_root, source.relative_to(candidate_root).as_posix())
                safe = True
                break
        if not safe or not source.is_file():
            raise ValueError("untrusted recovery source")
        max_bytes = 16_000_000 if source.suffix == ".tif" else 2_000_000
        if not 0 < source.stat().st_size <= max_bytes:
            raise ValueError("source size violates Artifact bounds")
        if sha256(source) != expected or source.stat().st_size != row["bytes"]:
            raise ValueError("source changed after inventory")
        target = bounded_path(root, relative)
        if run_relative(root, row["task_id"], row["run_id"]) != str(Path(relative).parent).replace(
            "\\", "/"
        ):
            raise ValueError("live attempt changed after inventory")
        action = "COPY_AFTER_APPROVAL"
        if target.exists():
            action = (
                "NOOP_IDENTICAL"
                if target.is_file() and sha256(target) == expected
                else "BLOCK_COLLISION"
            )
        decisions.append({"target_relative": relative, "action": action})
    return {
        "writes_performed": 0,
        "decisions": decisions,
        "blocked": any(d["action"] == "BLOCK_COLLISION" for d in decisions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--live", type=Path)
    parser.add_argument("--search-root", action="append", type=Path, default=[])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", type=Path)
    args = parser.parse_args()
    if args.dry_run:
        result = dry_run(json.loads(args.dry_run.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2))
        return 2 if result["blocked"] else 0
    if not args.references or not args.live or not args.output:
        parser.error("inventory requires --references, --live, --output")
    output = args.output.resolve()
    for root in [args.live, *args.search_root]:
        if output.is_relative_to(root.resolve()):
            parser.error("manifest must be outside original Artifact roots")
    result = inventory(
        json.loads(args.references.read_text(encoding="utf-8")), args.live, args.search_root
    )
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
    print(json.dumps(result["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
