"""Execute the real PowerShell phases against a native fake docker.cmd, never Docker."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).parents[1]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not PWSH, reason="Windows DPAPI/PowerShell 7 rehearsal"
)

FAKE_DOCKER = r"""
import json,os,pathlib,sys
root=pathlib.Path(os.environ['MAIN_SITE_MOCK_ROOT'])
state=json.loads((root/'state.json').read_text(encoding='utf-8'))
args=sys.argv[1:]
with (root/'trace.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(args)+'\n')
fault=state['fault']
def finish(value=None,code=0):
 (root/'state.json').write_text(json.dumps(state),encoding='utf-8')
 if value is not None:print(json.dumps(value))
 raise SystemExit(code)
def container(name):
 return next(c for c in state['containers'] if c['Id']==name or c['Name'].lstrip('/')==name.lstrip('/'))
def running(c,value):
 c['State'].update(Running=value,Status='running' if value else 'exited')
if args[:2]==['image','inspect']:
 image=args[2];labels=state['labels'].copy()
 if fault=='image_mismatch':labels['org.opencontainers.image.revision']='unexpected'
 finish([{'Id':image,'Config':{'Labels':labels}}])
if args[0]=='inspect':finish([container(args[1])])
if args[0]=='ps':
 for c in state['containers']:
  if c['Name'].startswith('/taskpilot-main-v04-'):print(c['Id'])
 finish()
if args[0]=='stop':
 if fault=='docker_stop_failure':finish(code=17)
 running(container(args[1]),False);finish()
if args[0]=='start':
 c=container(args[1])
 if fault=='old_api_start_failure' and c['Name'].endswith('agent_service-1'):finish(code=17)
 running(c,True);finish()
if args[0]=='exec':
 c=container(args[1])
 if fault=='old_api_timeout' and c['Name'].endswith('agent_service-1'):finish(code=17)
 if fault=='readiness_identity_changed' and c['Name'].endswith('agent_service-1'):c['Image']='sha256:'+'f'*64
 finish()
if args[0]=='compose':
 if 'config' in args:
  if fault=='compose_config_failure':finish(code=17)
  if '--quiet' in args:finish()
  cfg=state['compose']
  if fault=='resolved_path_mismatch':cfg['services']['api']['volumes'][0]['source']=str(root/'wrong')
  finish(cfg)
 if 'up' in args:
  state['containers'] += state['new'][:1] if fault=='compose_up_failure' else state['new']
  finish(code=17 if fault=='compose_up_failure' else 0)
 if 'stop' in args:
  if fault=='compose_stop_failure':finish(code=17)
  for c in state['containers']:
   if c['Name'].startswith('/taskpilot-main-v04-'):
    if fault=='incomplete_stop' and c['Name'].endswith('api-1'):continue
    running(c,False)
  finish()
finish(code=99)
"""


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def actual_backup_output(output, old, monkeypatch):
    """Execute backup_main_site.main with simulated IO, retaining its actual output schema."""
    spec = importlib.util.spec_from_file_location(
        "cutover_backup", REPO / "scripts/backup_main_site.py"
    )
    backup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backup)
    fake_inspect = [
        {
            "Name": c["Name"],
            "Id": c["Id"],
            "Image": c["Image"],
            "Created": "test",
            "State": {"StartedAt": "test"},
            "Mounts": c["Mounts"],
            "HostConfig": {"PortBindings": {}},
            "NetworkSettings": {"Networks": {}},
            "Config": {
                "WorkingDir": "/app",
                "Labels": {},
                "Env": [
                    "POSTGRES_DB=agent_service",
                    "POSTGRES_USER=postgres",
                    "POSTGRES_PASSWORD=mock",
                ],
            },
        }
        for c in old
    ]

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, query):
            self.query = query.as_string() if hasattr(query, "as_string") else query

        def fetchone(self):
            if "pg_export_snapshot" in self.query:
                return ("snapshot",)
            if "alembic_version" in self.query:
                return ("t037_proposal_identity",)
            if '"tasks"' in self.query:
                return (46,)
            if '"task_runs"' in self.query:
                return (48,)
            return (7,)

        def fetchall(self):
            return [("succeeded", 32)] if "GROUP BY status" in self.query else []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def cursor(self):
            return Cursor()

    def run(args, **kwargs):
        if args[:3] == ["docker", "image", "save"]:
            with tarfile.open(args[4], "w"):
                pass
        elif args[:2] == ["docker", "exec"]:
            kwargs["stdout"].write(b"PGDMP mock snapshot")
        else:
            (output / "container-definitions.dpapi").write_text("mock encrypted backup definition")
        return subprocess.CompletedProcess(args, 0)

    contents = b"fixture"
    files = {
        "artifact.png": {"bytes": len(contents), "sha256": hashlib.sha256(contents).hexdigest()}
    }

    def copytree(source, target, **kwargs):
        target.mkdir()
        (target / "artifact.png").write_bytes(contents)

    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", ["backup", "--output", str(output)])
        patch.setattr(
            backup.subprocess, "check_output", lambda *a, **kw: json.dumps(fake_inspect).encode()
        )
        patch.setattr(backup.subprocess, "run", run)
        patch.setattr(backup.psycopg, "connect", lambda **kw: Connection())
        patch.setattr(backup, "inventory", lambda *a: files)
        patch.setattr(backup.shutil, "copytree", copytree)
        backup.main()
    for name in ["live-runtime.env.dpapi", "live-compose.env", "live-guard.json"]:
        assert not (output / name).exists()
    assert (
        json.loads((output / "backup-inventory.json").read_text())["status"]
        == "VERIFIED_ARCHIVES_RESTORE_PENDING"
    )


@pytest.fixture
def rehearsal(tmp_path, monkeypatch):
    deployment, data = tmp_path / "deployment", tmp_path / "final-data-backup"
    deployment.mkdir()
    (deployment / "final-build").mkdir()
    artifacts, credentials = tmp_path / "artifacts", tmp_path / "credentials"
    artifacts.mkdir()
    credentials.mkdir()
    api_image, web_image = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    source = {
        "approved_revision": "5e174e0cc38a8ff26e66406423b646c74e3c6b4f",
        "compose_sha256": hash_file(REPO / "compose.main-site.yaml"),
        "preflight_sha256": hash_file(REPO / "scripts/main_site_preflight.py"),
        "api_image": api_image,
        "web_image": web_image,
    }

    def container(name, id_char, image, running=True, mounts=None, service=None):
        return {
            "Name": "/" + name,
            "Id": id_char * 64,
            "Image": image,
            "Mounts": mounts or [],
            "State": {
                "Running": running,
                "Restarting": False,
                "Paused": False,
                "Status": "running" if running else "exited",
                "Health": {"Status": "healthy"},
            },
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "taskpilot-main-v04",
                    "com.docker.compose.service": service,
                }
            },
        }

    old = [
        container("agent-service-toolkit-web-1", "1", "sha256:" + "c" * 64),
        container(
            "agent-service-toolkit-agent_service-1",
            "2",
            "sha256:" + "d" * 64,
            mounts=[
                {"Source": str(artifacts), "Destination": "/app/data/geochange-artifacts"},
                {"Source": str(credentials), "Destination": "/privatecredentials"},
            ],
        ),
        container(
            "agent-service-toolkit-postgres-1",
            "3",
            "sha256:" + "e" * 64,
            mounts=[
                {
                    "Type": "volume",
                    "Name": "agent-service-toolkit_postgres_data",
                    "Destination": "/var/lib/postgresql/data",
                }
            ],
        ),
    ]
    actual_backup_output(data, old, monkeypatch)
    metadata = json.loads((data / "deployment.json").read_text())
    guard = {
        "database": "agent_service",
        "system_identifier": "cluster",
        "user_ids_sha256": "f" * 64,
        "protected_user_ids": ["00000000-0000-0000-0000-000000000001"],
        "min_tasks": 46,
        "min_task_runs": 48,
        "artifact_root": str(artifacts),
        "network": "agent-service-toolkit_default",
    }
    runtime = {
        "TASKPILOT_EXPECTED_DATABASE": "agent_service",
        "TASKPILOT_EXPECTED_SYSTEM_ID": "cluster",
        "TASKPILOT_EXPECTED_USER_IDS_SHA256": "f" * 64,
        "TASKPILOT_PROTECTED_USER_IDS": guard["protected_user_ids"][0],
        "TASKPILOT_MIN_TASKS": "46",
        "TASKPILOT_MIN_TASK_RUNS": "48",
        "TASKPILOT_DATABASE_URL": "postgresql+psycopg://mock:mock@postgres:5432/agent_service",
    }
    settings = {
        "TASKPILOT_API_IMAGE": api_image,
        "TASKPILOT_WEB_IMAGE": web_image,
        "TASKPILOT_RUNTIME_ENV": str(deployment / "live-runtime.env"),
        "TASKPILOT_ARTIFACT_ROOT": str(artifacts),
        "TASKPILOT_CREDENTIAL_ROOT": str(credentials),
        "TASKPILOT_NETWORK": guard["network"],
        "TASKPILOT_API_PORT": "8080",
        "TASKPILOT_WEB_PORT": "5173",
    }
    for name, value in [
        ("deployment.json", metadata),
        ("live-guard.json", guard),
        ("final-build/candidate-source.json", source),
    ]:
        (deployment / name).write_text(json.dumps(value), encoding="utf-8")
    (deployment / "live-compose.env").write_text(
        "".join(k + "=" + v + "\n" for k, v in settings.items()), encoding="utf-8"
    )
    (deployment / "runtime-fixture.txt").write_text(
        "".join(k + "=" + v + "\n" for k, v in runtime.items()), encoding="utf-8"
    )
    new = [
        container("taskpilot-main-v04-api-1", "4", api_image, service="api"),
        container("taskpilot-main-v04-web-1", "5", web_image, service="web"),
    ]
    compose = {
        "services": {
            "api": {
                "image": api_image,
                "environment": runtime,
                "ports": [{"target": 8080, "published": "8080", "host_ip": "127.0.0.1"}],
                "volumes": [
                    {
                        "target": target,
                        "source": str(path),
                        "type": "bind",
                        "bind": {"create_host_path": False},
                    }
                    for target, path in [
                        ("/app/data/geochange-artifacts", artifacts),
                        ("/privatecredentials", credentials),
                    ]
                ],
            },
            "web": {
                "image": web_image,
                "ports": [{"target": 5173, "published": "5173", "host_ip": "127.0.0.1"}],
            },
        },
        "networks": {"existing": {"external": True, "name": guard["network"]}},
    }
    state = {
        "containers": old,
        "new": new,
        "compose": compose,
        "fault": "",
        "labels": {
            "org.opencontainers.image.revision": source["approved_revision"],
            "taskpilot.main.compose.sha256": source["compose_sha256"],
            "taskpilot.main.preflight.sha256": source["preflight_sha256"],
        },
    }
    (tmp_path / "fake_docker.py").write_text(FAKE_DOCKER, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker.cmd").write_text(
        f'@echo off\n"{sys.executable}" "{tmp_path / "fake_docker.py"}" %*\nexit /b %errorlevel%\n'
    )
    runner = tmp_path / "runner.ps1"
    runner.write_text(
        r"""
param($Root, $Script, $Action)
$ErrorActionPreference = 'Stop'
if ((Get-Command docker).Source -ne (Join-Path $Root 'bin/docker.cmd')) { throw 'Mock isolation failed' }
$deployment = Join-Path $Root 'deployment'
$backup = Join-Path $Root 'final-data-backup'
$text = Get-Content -LiteralPath (Join-Path $deployment 'runtime-fixture.txt') -Raw -Encoding utf8
$text | ConvertTo-SecureString -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath (Join-Path $deployment 'live-runtime.env.dpapi')
. $Script
switch ($Action) {
 'stop' { Stop-MainSiteOriginal $deployment $backup }
 'start' { Start-MainSiteCandidate $deployment $backup -TimeoutSeconds 1 }
 'cutover' {
   Stop-MainSiteOriginal $deployment $backup
   Start-MainSiteCandidate $deployment $backup -TimeoutSeconds 1
 }
 'rollback' { Restore-MainSiteOriginal $deployment $backup -TimeoutSeconds 1 }
 'partial-rollback' {
   try { Start-MainSiteCandidate $deployment $backup -TimeoutSeconds 1 }
   catch { Restore-MainSiteOriginal $deployment $backup -TimeoutSeconds 1 }
 }
}
""",
        encoding="utf-8",
    )

    def execute(action, fault="", change=None):
        current = json.loads(json.dumps(state))
        current["fault"] = fault
        if action not in {"stop", "cutover"}:
            for c in current["containers"][:2]:
                c["State"].update(Running=False, Status="exited")
        if action == "rollback":
            current["containers"] += current["new"]
        if change:
            change(current)
        (tmp_path / "state.json").write_text(json.dumps(current), encoding="utf-8")
        env = os.environ.copy()
        env.pop("PSModulePath", None)
        env.update(MAIN_SITE_MOCK_ROOT=str(tmp_path), PYTHONIOENCODING="utf-8")
        env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]
        result = subprocess.run(
            [
                PWSH,
                "-NoProfile",
                "-File",
                str(runner),
                str(tmp_path),
                str(REPO / "scripts/main_site_cutover.ps1"),
                action,
            ],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=40,
        )
        trace_path = tmp_path / "trace.jsonl"
        trace = (
            [json.loads(line) for line in trace_path.read_text().splitlines()]
            if trace_path.exists()
            else []
        )
        return result, trace

    return execute, deployment, data


def has(trace, prefix):
    return any(args[: len(prefix)] == prefix for args in trace)


@pytest.mark.parametrize(
    "fault",
    ["docker_stop_failure", "compose_config_failure", "image_mismatch", "resolved_path_mismatch"],
)
def test_failed_precondition_never_reaches_dependent_shutdown(rehearsal, fault):
    run, _, _ = rehearsal
    result, trace = run("stop", fault)
    assert result.returncode != 0, result.stderr
    assert not has(trace, ["stop", "2" * 64])
    if fault != "docker_stop_failure":
        assert not has(trace, ["stop"])
    else:
        assert has(trace, ["stop", "1" * 64])


def test_missing_deployment_configuration_before_downtime(rehearsal):
    run, deployment, data = rehearsal
    assert not (data / "live-compose.env").exists()
    (deployment / "live-compose.env").unlink()
    result, trace = run("stop")
    assert result.returncode != 0
    assert trace == []


def test_backup_directory_cannot_substitute_for_runtime_configuration(rehearsal):
    run, deployment, data = rehearsal
    path = deployment / "live-compose.env"
    text = path.read_text(encoding="utf-8").replace(
        str(deployment / "live-runtime.env"), str(data / "live-runtime.env")
    )
    path.write_text(text, encoding="utf-8")
    result, trace = run("stop")
    assert result.returncode != 0
    assert not has(trace, ["stop"])
    assert not (data / "live-runtime.env").exists()


def test_corrupt_actual_backup_output_blocks_downtime(rehearsal):
    run, _, data = rehearsal
    (data / "database.dump").write_bytes(b"changed")
    result, trace = run("stop")
    assert result.returncode != 0
    assert not has(trace, ["stop"])


def test_compose_up_failure_never_health_checks_or_starts_old_services(rehearsal):
    run, _, _ = rehearsal
    result, trace = run("start", "compose_up_failure")
    assert result.returncode != 0
    assert any("up" in args for args in trace)
    assert not has(trace, ["exec"])
    assert not has(trace, ["start"])


@pytest.mark.parametrize(
    "fault",
    [
        "compose_stop_failure",
        "incomplete_stop",
        "old_api_start_failure",
        "old_api_timeout",
        "readiness_identity_changed",
    ],
)
def test_failed_rollback_prerequisite_never_starts_old_web(rehearsal, fault):
    run, _, _ = rehearsal
    result, trace = run("rollback", fault)
    assert result.returncode != 0, result.stderr
    assert not has(trace, ["start", "1" * 64])
    if fault in {"compose_stop_failure", "incomplete_stop"}:
        assert not has(trace, ["start", "2" * 64])
    if fault == "old_api_start_failure":
        assert has(trace, ["start", "2" * 64])
        assert not has(trace, ["exec"])
    if fault in {"old_api_timeout", "readiness_identity_changed"}:
        assert has(trace, ["exec", "2" * 64])
    assert any(args[-3:] == ["stop", "web", "api"] for args in trace)


def test_unexpected_new_image_aborts_rollback_before_any_stop_or_start(rehearsal):
    run, _, _ = rehearsal
    result, trace = run(
        "rollback", change=lambda state: state["containers"][-1].update(Image="sha256:" + "f" * 64)
    )
    assert result.returncode != 0
    assert not has(trace, ["start"])
    assert not any(args[-3:] == ["stop", "web", "api"] for args in trace)


def test_successful_mock_cutover_stops_originals_before_new_version_readiness(rehearsal):
    run, _, _ = rehearsal
    result, trace = run("cutover")
    assert result.returncode == 0, result.stderr
    web_stop = trace.index(["stop", "1" * 64])
    api_stop = trace.index(["stop", "2" * 64])
    up = next(i for i, args in enumerate(trace) if args[0] == "compose" and "up" in args)
    api_ready = next(i for i, args in enumerate(trace) if args[:2] == ["exec", "4" * 64])
    web_ready = next(i for i, args in enumerate(trace) if args[:2] == ["exec", "5" * 64])
    assert web_stop < api_stop < up < api_ready < web_ready
    assert not has(trace, ["start"])


@pytest.mark.parametrize(
    "action,fault", [("rollback", ""), ("partial-rollback", "compose_up_failure")]
)
def test_successful_rollback_orders_confirmed_stop_api_ready_then_web(rehearsal, action, fault):
    run, _, _ = rehearsal
    result, trace = run(action, fault)
    assert result.returncode == 0, result.stderr
    stopped = next(
        i
        for i, args in enumerate(trace)
        if args[0] == "compose" and args[-3:] == ["stop", "web", "api"]
    )
    api = trace.index(["start", "2" * 64])
    web = trace.index(["start", "1" * 64])
    ready = next(i for i, args in enumerate(trace) if args[:2] == ["exec", "2" * 64])
    assert stopped < api < ready < web
    assert any(args[0] == "ps" for args in trace[stopped + 1 : api])
    assert has(trace[stopped + 1 : api], ["inspect", "4" * 64])
    if action == "rollback":
        assert has(trace[stopped + 1 : api], ["inspect", "5" * 64])
    assert any(args[:2] == ["exec", "1" * 64] for args in trace[web + 1 :])
    assert "http://127.0.0.1:8080/health/ready" in trace[ready][-1]
