# Deployment Runbook

V1 target is a reproducible Docker Compose deployment, not Kubernetes.

## Required services

Exact services depend on final implementation, likely:

- API
- PostgreSQL
- UI
- optional Redis/worker
- optional observability backend

## Deployment checklist

- env vars present
- secrets not committed
- DB reachable
- migrations applied
- health endpoint green
- readiness checks pass
- LLM provider configured
- storage configured
- CORS restricted appropriately
- test user/demo data policy understood
- log redaction enabled
- backups considered for persistent DB

## Rollback

Document:

- code rollback
- migration rollback policy
- what migrations are irreversible
- how to disable external-action tools quickly

## Incident basics

When a run misbehaves:

1. disable risky tool if needed
2. capture task/run/trace IDs
3. inspect approval/audit
4. determine whether side effect occurred
5. never blindly retry ambiguous external action

## Original 5173 recovery and V0.4 preparation (2026-10-16)

Status: implementation preparation only; independent Strong Review and explicit
live cutover authorization are required. Do not use `compose.yaml up` for this
operation: that topology includes an automatic migration service.

The approved application baseline is `5e174e0cc38a8ff26e66406423b646c74e3c6b4f`.
The live PostgreSQL container is `agent-service-toolkit-postgres-1`, database
`agent_service`, volume `agent-service-toolkit_postgres_data`, network
`agent-service-toolkit_default`. Preserve all four identities. The original API
and web containers are retained for rollback, never removed or recreated by
this procedure. The original Streamlit container also uses the API network
alias and is affected by the short backend cutover interruption.

### Backup and evidence

Keep three directory roles explicit; never replace the deployment directory
with a new backup directory:

| Role | Location / contents |
| --- | --- |
| `$deploymentDir` | `D:\codex\agent项目\backups\taskpilot-main-20261016-125845`: verified deployment evidence and configuration, including `live-runtime.env.dpapi`, `live-compose.env`, `live-guard.json`, `deployment.json`, and `final-build/candidate-source.json` |
| `$verifiedDataBackupDir` | `D:\codex\agent项目\backups\taskpilot-main-20261016-script-verified`: existing verified data backup used for pre-downtime checks and rollback, even if the final backup fails |
| `$finalDataBackupDir` | A **new** timestamped data-only backup directory created under maintenance; never a source of deployment configuration |

The initial deployment-evidence directory also retains the earlier backup and
candidate image archive. That historical co-location does not mean a fresh backup
contains deployment configuration. `backup_main_site.py` generates the database
snapshot, Artifact copies/archives/inventories, `deployment.json`, encrypted
container definitions, rollback image archive and `backup-inventory.json`.
It **does not generate** `live-runtime.env.dpapi`, `live-compose.env` or
`live-guard.json`. The corrected commands always resolve those from
`$deploymentDir`; `DataBackupDir` is used only for verified data backup outputs.

Backups contain private data; keep restricted ACLs and do not attach them to a
PR. DPAPI recovery requires the same Windows identity/profile. These local
rollback copies are not off-machine disaster-recovery backups. Preflight decrypts
the runtime file only inside the existing restricted deployment directory; it
never prints secrets and rejects a differing pre-existing plaintext file.

`scripts/backup_main_site.py` is the tested repeatable backup procedure. It uses an
exported repeatable-read PostgreSQL snapshot for both row counts/reference
inventory and `pg_dump --snapshot`, verifies source inventories before/after
copy, and reads every archived member back. PostgreSQL was restored to the
separate `taskpilot_main_restore_20261016` volume/container, loopback port 35432,
database `taskpilot_main_restore_test`. All nine business table counts matched.
The original schema and restored schema are `t037_proposal_identity`; no live
migration is required by the current approved code.

Database and filesystem snapshots are not a cross-system atomic backup. There
were zero running and three pending runs during preparation, and source files
were stable across the backup. At cutover, stop all writers after approval and
take a fresh final backup with the same procedure in a new restricted directory.
Do not overwrite this evidence set. Do not copy a live PostgreSQL data directory.
The repository script was also executed into the separate
`taskpilot-main-20261016-script-verified` backup set and successfully restored into
`taskpilot_main_backup_rehearsal_test` on the isolated PostgreSQL instance.

```powershell
# Use a new timestamped directory; the script restricts its ACL before writing private data.
.\.venv\Scripts\python.exe -B scripts/backup_main_site.py --output "$newBackupDirectory"
```

### Artifact recovery boundaries

Run the read-only planner against the database reference export and explicitly
chosen source roots; keep its output outside every source directory:

```powershell
.\.venv\Scripts\python.exe -B scripts/main_site_recovery.py --references "$verifiedDataBackupDir\artifact-references.json" --live "$liveArtifacts" --search-root "$historicalArtifacts" --output "$deploymentDir\new-recovery-manifest.json"
.\.venv\Scripts\python.exe -B scripts/main_site_recovery.py --dry-run "$deploymentDir\new-recovery-manifest.json"
```

The planner follows published attempt pointers and fails closed on malformed
pointers, links, traversal, changed sources, and target collisions. Only a
code/metadata-authoritative SHA-256 match permits `FOUND_VERIFIED`. A newly
computed source digest protects the copy against later changes but cannot prove
historical scientific identity. Never synthesize `.dynamic_evidence.json`,
attempt pointers, missing rasters, or TaskRun metadata. The tool deliberately
does not implement live writes.

Preparation found 114 references: 33 present, 21 found without an authoritative
digest, 60 not found. All 27 referenced dynamic files present in the live root
matched their database-owned digests. No missing reference qualified for
automatic recovery. The 21 candidates remain `FOUND_UNVERIFIED`; no copy is
authorized or planned for them. Any later verified copy requires a newly
reviewed per-file manifest and explicit approval. Use exclusive creation, never
overwrite, verify target SHA-256 after copying, and record newly created paths.
Rollback may remove only files created by that operation that still match its
recorded digest, after confirming there are no active readers/writers.

### Reproducible candidate build and startup

```powershell
.\.venv\Scripts\python.exe -B scripts/build_main_site.py --output "$newBuildDirectory"
docker start taskpilot-main-restore-20261016
if ($LASTEXITCODE -ne 0) { throw 'Isolated PostgreSQL start failed.' }
docker compose --env-file "$deploymentDir\candidate-compose.env" -p taskpilot-main-candidate-20261016 -f compose.main-site.yaml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Candidate Compose validation failed.' }
docker compose --env-file "$deploymentDir\candidate-compose.env" -p taskpilot-main-candidate-20261016 -f compose.main-site.yaml up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw 'Candidate startup failed.' }
```

The build exports the approved commit with `core.autocrlf=false`, then materializes
only JSON LF/CRLF serialization needed to match the **unchanged code-owned
fixture digests**. The historical pins mix LF and CRLF; neither global newline
setting satisfies them all. Binary rasters, scientific values, trusted digests,
and repository fixtures are unchanged. Each packaged file has traceable input
and output digests. Failure to match an existing pin blocks the build.

The API image adds only the deployment preflight. OCI labels record the approved
revision and preflight/Compose hashes; `candidate-source.json` records immutable
image IDs. Use those IDs, not mutable tags, for cutover. Capture the resulting
image archive and frontend lockfile as build evidence. Frontend dependencies are
pinned in `web/package.json` and `web/pnpm-lock.yaml`, but a later rebuild can
still produce different image IDs through base-image or toolchain changes. Use
the verified image archive to reproduce this exact deployment.

`compose.main-site.yaml` is standalone and defines only API/web. It requires an
existing external network, existing bind directories, pinned image IDs, protected
raw environment file, and explicit ports. It cannot create PostgreSQL or run
Alembic. The API checks cluster system identifier, database name, schema head,
protected original user IDs and minimum task/run counts before startup; a wrong
or empty database fails closed. Additional users do not invalidate the protected
original-ID check. If an original account is intentionally removed later, revise
the protected identity inventory through a separate reviewed operation.

The candidate uses its own network, restored database, copied Artifact directory,
empty credential directory, ports 35173/38080, fake model and disabled live
LLM/STAC flags. No original writable volume or Artifact mount is attached.

### Exact live cutover plan — NOT EXECUTED

Do not execute this section until independent review and explicit authorization
cover the original API/web interruption, final backup, mount selection, and
the historical result limitations. The isolated rehearsal is not live approval.

1. Recheck original container/image IDs against `deployment.json`, original
   database system ID/account identity, schema, counts, and backup hashes.
   Confirm the verified image IDs and Compose hash match `final-build/candidate-source.json`.
   No pending/running record may be auto-replayed. Current three pending runs
   remain untouched; if they are active requests, defer cutover for an owner
   disposition. Any running or waiting run requires a bounded drain/explicit
   disposition before interruption; never mark it succeeded or cancelled here.
2. Announce a maintenance window and block new submissions. After authorization,
   stop original web then API. Confirm no other service/process writes the same
   database or Artifact root. Streamlit (:8501) and direct API callers must also
   remain in maintenance during verification. If other writers cannot be
   excluded, stop and return to the previous containers.
3. Take a fresh final snapshot/Artifact backup under quiescence. Recheck accounts,
   row counts and IDs, run state, digest inventory and revision. Refresh the
   protected live guard/encrypted runtime file if identities changed. This phase
   never runs `alembic upgrade`; a revision mismatch blocks the cutover.
4. Rerun Artifact inventory/dry-run. Current recovery application is an explicit
   **zero-file operation**: no missing file has authoritative verified identity.
   If the inventory changes, review a new recovery manifest first. Keep the
   live Artifact bind at `_taskpilot_v03_browser\data\geochange-artifacts` unless
   an independently verified remap has been explicitly approved.
5. Use the prevalidated DPAPI runtime environment in `$deploymentDir`,
   never a data backup directory or Git; do not print it. Use `$deploymentDir\live-compose.env` (immutable candidate
   image IDs, original network, original Artifact/credential paths, 5173/8080).
   `format: raw` preserves secret values without interpolation. Original provider
   flags/credentials are preserved, but do not trigger external-task smoke tests.
6. Start only the new API/web under project `taskpilot-main-v04`. The PostgreSQL,
   volume, network, and original stopped containers are unchanged. The new API
   takes `agent_service` DNS alias only after the original API is stopped.
7. Verify preflight/health, version labels, original account login with the
   owner's credentials (never collect/report them), history row preservation,
   authorized existing maps/Artifact GETs, unauthenticated 401, cross-tenant 404,
   and served progress/quality UI. Do not create a task. Restore user access only
   after acceptance. Retain old containers/images and all backups for rollback.

The executable phases are in `scripts/main_site_cutover.ps1` (PowerShell 7).
Dot-sourcing only loads functions; it never calls Docker or interrupts services.
Every native Docker invocation checks `$LASTEXITCODE` immediately. A failure
throws and aborts dependent operations; readiness probes handle their exit code
explicitly and use a bounded timeout.

Before any downtime, `Get-MainSiteContext` verifies all required deployment files,
immutable image IDs and version/configuration labels, Compose/preflight hashes,
original container identities and PostgreSQL volume, original bind directories,
runtime/database guard references, resolved Compose paths/ports/environment,
and the **actual backup-script output files and hashes**. It rejects missing,
mismatched or incomplete inputs. The raw backup status
`VERIFIED_ARCHIVES_RESTORE_PENDING` is accepted as archive verification only;
it is not reported as restore evidence. Preparation's separate successful
restore evidence remains required by the earlier review gate.

Initialize and run pre-downtime validation only:

```powershell
$repo = 'D:\github\agent-service\agent-service-toolkit-v03-b'
$deploymentDir = 'D:\codex\agent项目\backups\taskpilot-main-20261016-125845'
$verifiedDataBackupDir = 'D:\codex\agent项目\backups\taskpilot-main-20261016-script-verified'
$finalDataBackupDir = Join-Path (Split-Path $deploymentDir) ('taskpilot-main-final-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
. "$repo\scripts\main_site_cutover.ps1"
Get-MainSiteContext $deploymentDir $verifiedDataBackupDir | Out-Null
```

The following block requires explicit live authorization and the maintenance,
writer-exclusion, pending-run and historical-result gates above. `Stop` repeats
preflight **before the first original stop**. The final backup writes only to the
new data directory; `Start` revalidates that final backup while still resolving
configuration from the unchanged deployment directory. If account/runtime
configuration has drifted, return for a separately verified configuration update;
do not copy stale files into the final backup or improvise during downtime.

```powershell
& {
    Stop-MainSiteOriginal $deploymentDir $verifiedDataBackupDir
    & "$repo\.venv\Scripts\python.exe" -B "$repo\scripts\backup_main_site.py" --output $finalDataBackupDir
    if ($LASTEXITCODE -ne 0) { throw 'Final backup failed; do not start the candidate. Use the rollback phase.' }
    Start-MainSiteCandidate $deploymentDir $finalDataBackupDir -TimeoutSeconds 60
}
```

There is no automatic retry, replay or database restore. A failed stop/config/up
leaves the current partial state and aborts the block. Run the separate authorized
rollback phase below to recover deterministically. Use the existing verified data
backup for its checks so rollback does not depend on a missing/incomplete final
backup. Deployment configuration always comes from `$deploymentDir`.

```powershell
& {
    Restore-MainSiteOriginal $deploymentDir $verifiedDataBackupDir -TimeoutSeconds 60
}
```

Rollback validates identities/configuration, stops new Web/API, then independently
lists and inspects **all** containers in the new Compose project. None may be
running, restarting or paused; any unexpected container/image or failed stop
aborts before starting the old API. An absent new service is allowed after a
partial `up`, but only after a successful Docker project listing proves absence.
The old API is started by its recorded immutable container ID; its readiness is
polled **inside that container** with a one-second HTTP timeout per attempt and
the overall deadline above. Identity, running state and healthy status are checked
before/after readiness. Only then may old Web start; its own identity/readiness is
also checked. Host-port HTTP 200 is never used as evidence of the intended version.

A nonzero Docker stop/config/up/start, readiness timeout, unexpected identity or
incomplete stop is a hard failure. Do not proceed to dependent starts manually.
Keep maintenance in place and investigate the reported phase. Owner login,
history/maps, tenant-denial and version acceptance still precede reopening access;
container health is not a substitute for that acceptance.

Rollback is container/code rollback, not database restore. No schema migration
is planned, and smoke requests are read-only except the owner's login session.
Keep the same database/Artifact roots so histories are not discarded. Startup
may initialize existing LangGraph persistence metadata; the final logical backup
covers that schema as well. If new tasks/writes occurred after cutover, drain and
review version compatibility before restarting old code. Never automatically
restore a database dump over those writes. If a separate restore is required,
obtain explicit authorization, restore into a new isolated volume, verify it,
and plan the data-loss/reconciliation decision before switching mounts.

Historical acceptance limitation: authenticated comparison on the same clone
returned 5/32 maps with the original images (14 HTTP 409, 13 HTTP 422), versus
2/32 with V0.4 (17 HTTP 409, 13 HTTP 422). Three old dynamic
Landsat runs lack evidence acceptable to V0.4 quality projection. Do not fabricate
sidecars or weaken fail-closed checks to make them display. Owner acceptance of
these historical limitations, or a separate evidence-backed repair, is a live
cutover gate. Preparation and review readiness do not mean all historical maps
have been recovered or that live migration is complete.
