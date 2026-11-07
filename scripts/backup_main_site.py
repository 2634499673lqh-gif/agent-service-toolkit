"""Create a consistent PostgreSQL snapshot and verified original-site rollback backup."""

import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import tarfile
from datetime import UTC, datetime

import psycopg
from psycopg import sql

NAMES = [
    "agent-service-toolkit-web-1",
    "agent-service-toolkit-agent_service-1",
    "agent-service-toolkit-postgres-1",
]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def inventory(root):
    result = {}
    if root.is_symlink() or root.is_junction() or not root.is_dir():
        raise RuntimeError("Artifact root is not a direct existing directory")

    def fail_scan(error):
        raise error

    for current, dirs, files in os.walk(root, followlinks=False, onerror=fail_scan):
        for name in dirs + files:
            path = pathlib.Path(current) / name
            if path.is_symlink() or path.is_junction():
                raise RuntimeError("Linked artifact path requires manual inspection")
        for name in files:
            path = pathlib.Path(current) / name
            result[path.relative_to(root).as_posix()] = {
                "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
    return result


def write(root, name, value):
    (root / name).write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Verified backup of the original TaskPilot main site"
    )
    parser.add_argument("--output", type=pathlib.Path, required=True)
    ROOT = parser.parse_args().output.resolve()
    if os.name != "nt":
        raise SystemExit("This backup uses Windows ACL and user-bound DPAPI")
    for parent in [ROOT, *ROOT.parents]:
        if (parent / ".git").exists():
            raise SystemExit("Backups must be outside Git repositories")
    for protected_root in [
        pathlib.Path(r"D:\github\agent-service\_taskpilot_v03_browser\data\geochange-artifacts"),
        pathlib.Path(r"D:\github\agent-service\agent-service-toolkit\data\geochange-artifacts"),
    ]:
        if ROOT.is_relative_to(protected_root.resolve()):
            raise SystemExit("Backup output overlaps original Artifact directory")

    ROOT.mkdir(parents=True, exist_ok=False)
    backup_env = os.environ.copy()
    # Do not inherit PowerShell 7 module paths into a Windows PowerShell child.
    backup_env.pop("PSModulePath", None)
    backup_env["TASKPILOT_BACKUP_DIR"] = str(ROOT)
    protect = r"""
    $ErrorActionPreference = 'Stop'
    $dir = $env:TASKPILOT_BACKUP_DIR
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    icacls $dir /inheritance:r /grant:r "${identity}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Backup ACL failed' }
    $raw = docker inspect agent-service-toolkit-web-1 agent-service-toolkit-agent_service-1 agent-service-toolkit-postgres-1
    if ($LASTEXITCODE -ne 0) { throw 'Container metadata failed' }
    ($raw -join "`n") | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $dir 'container-definitions.dpapi')
    """
    subprocess.run(
        [shutil.which("pwsh") or "powershell", "-NoProfile", "-Command", protect],
        env=backup_env,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    containers = json.loads(subprocess.check_output(["docker", "inspect", *NAMES]))
    metadata = []
    for c in containers:
        cfg = c["Config"]
        metadata.append(
            {
                "name": c["Name"],
                "id": c["Id"],
                "image": c["Image"],
                "created": c["Created"],
                "started": c["State"]["StartedAt"],
                "command": ["[see encrypted definitions]"],
                "workdir": cfg["WorkingDir"],
                "environment_names": sorted(x.split("=", 1)[0] for x in cfg["Env"]),
                "compose_labels": {
                    k: v for k, v in cfg["Labels"].items() if k.startswith("com.docker.compose.")
                },
                "mounts": c["Mounts"],
                "ports": c["HostConfig"]["PortBindings"],
                "networks": c["NetworkSettings"]["Networks"],
            }
        )
    write(ROOT, "deployment.json", metadata)
    pg_env = dict(x.split("=", 1) for x in containers[2]["Config"]["Env"] if "=" in x)
    with psycopg.connect(
        host="127.0.0.1",
        port=5432,
        dbname=pg_env["POSTGRES_DB"],
        user=pg_env["POSTGRES_USER"],
        password=pg_env["POSTGRES_PASSWORD"],
        connect_timeout=5,
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
            cur.execute("SET LOCAL statement_timeout='10s'")
            cur.execute("SELECT pg_export_snapshot()")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Snapshot unavailable")
            snapshot = row[0]
            counts = {}
            for table in [
                "users",
                "tasks",
                "task_runs",
                "organizations",
                "memberships",
                "auth_sessions",
                "agent_runs",
                "tool_calls",
                "approvals",
            ]:
                cur.execute(
                    sql.SQL("SELECT count(*) FROM taskpilot.{}").format(sql.Identifier(table))
                )
                row = cur.fetchone()
                if row is None:
                    raise RuntimeError("Count unavailable")
                counts[table] = row[0]
            cur.execute("SELECT version_num FROM taskpilot.alembic_version")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("Revision unavailable")
            revision = row[0]
            cur.execute("SELECT status,count(*) FROM taskpilot.task_runs GROUP BY status")
            statuses = dict(cur.fetchall())
            cur.execute(
                "SELECT task_id,id,status,result_metadata->'artifact_references', "
                "result_metadata->'provenance'->'artifact_checksums', "
                "result_metadata->>'execution_mode' FROM taskpilot.task_runs "
                "WHERE result_metadata ? 'artifact_references' ORDER BY task_id,id"
            )
            references = [
                {
                    "task_id": str(a),
                    "run_id": str(b),
                    "status": s,
                    "references": refs,
                    "checksums": checksums,
                    "mode": mode,
                }
                for a, b, s, refs, checksums, mode in cur.fetchall()
            ]
            write(ROOT, "artifact-references.json", references)
            with (ROOT / "database.dump").open("wb") as target:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        NAMES[2],
                        "pg_dump",
                        "-U",
                        pg_env["POSTGRES_USER"],
                        "-d",
                        pg_env["POSTGRES_DB"],
                        "-Fc",
                        "--snapshot",
                        snapshot,
                    ],
                    stdout=target,
                    stderr=subprocess.PIPE,
                    check=True,
                )
            write(
                ROOT,
                "database-baseline.json",
                {
                    "counts": counts,
                    "revision": revision,
                    "run_statuses": statuses,
                    "snapshot": snapshot,
                },
            )
    print("Consistent PostgreSQL dump completed", flush=True)

    sources = {
        "live-artifacts": pathlib.Path(
            r"D:\github\agent-service\_taskpilot_v03_browser\data\geochange-artifacts"
        ),
        "historical-artifacts": pathlib.Path(
            r"D:\github\agent-service\agent-service-toolkit\data\geochange-artifacts"
        ),
    }
    for label, source in sources.items():
        before = inventory(source)
        shutil.copytree(source, ROOT / label, symlinks=False)
        after = inventory(source)
        copied = inventory(ROOT / label)
        if before != after or before != copied:
            raise RuntimeError("Artifact source changed or copy verification failed")
        with tarfile.open(ROOT / (label + ".tar.gz"), "w:gz") as archive:
            archive.add(ROOT / label, arcname=label)
        with tarfile.open(ROOT / (label + ".tar.gz"), "r:gz") as archive:
            archived = {}
            for member in archive:
                if member.isfile():
                    stream = archive.extractfile(member)
                    assert stream is not None
                    h = hashlib.sha256()
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        h.update(block)
                    archived[pathlib.PurePosixPath(member.name).relative_to(label).as_posix()] = {
                        "bytes": member.size,
                        "sha256": h.hexdigest(),
                    }
            if archived != copied:
                raise RuntimeError("Archive verification failed")
        write(ROOT, label + "-inventory.json", copied)
        print(label, "copy/archive verified", len(copied), "files", flush=True)

    images = [c["Image"] for c in containers[:2]]
    subprocess.run(
        ["docker", "image", "save", "-o", str(ROOT / "rollback-images.tar"), *images],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    with tarfile.open(ROOT / "rollback-images.tar") as archive:
        for member in archive:
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                for _ in iter(lambda: stream.read(1024 * 1024), b""):
                    pass
    print("Rollback image archive readable", flush=True)
    files = {
        p.relative_to(ROOT).as_posix(): {"bytes": p.stat().st_size, "sha256": digest(p)}
        for p in ROOT.iterdir()
        if p.is_file() and p.name != "backup-inventory.json"
    }
    write(
        ROOT,
        "backup-inventory.json",
        {
            "created_utc": datetime.now(UTC).isoformat(),
            "status": "VERIFIED_ARCHIVES_RESTORE_PENDING",
            "files": files,
            "source_inventories": [s + "-inventory.json" for s in sources],
        },
    )
    print("BACKUP_ARCHIVES_VERIFIED", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "Backup failed; incomplete set must not be used. Sensitive error details suppressed."
        ) from None
