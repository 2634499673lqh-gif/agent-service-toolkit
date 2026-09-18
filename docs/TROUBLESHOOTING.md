# Troubleshooting

Codex should append real issues encountered during development.

## Windows: Streamlit tests time out before app code runs

Symptom: `tests/app/test_streamlit_app.py` fails with `RuntimeError: Could not determine home directory` inside `Path.home()` or an AppTest timeout.

Cause: the shared `mock_env` fixture clears the process environment. On Windows, Streamlit needs `USERPROFILE` (and sometimes `HOMEDRIVE`/`HOMEPATH`) to find its config directory.

Resolution in this repository: `tests/conftest.py` preserves only those three OS path variables while continuing to clear project configuration and secrets. Do not put API keys or other application settings into that fixture.

## Windows: uv or pytest cannot write its cache or temporary files

Symptom: `uv` reports a cache permission error, or pytest fails before collection
because the default Windows `TEMP`/`TMP` directory is not writable.

Resolution: use repository-local paths for the current PowerShell process. The
existing `.uv-cache/` and `.pytest_cache/` directories are ignored by Git:

```powershell
$repo = (Get-Location).Path
$tempDir = Join-Path $repo '.pytest_cache\tmp'
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
$env:TEMP = $tempDir
$env:TMP = $tempDir
$env:UV_CACHE_DIR = Join-Path $repo '.uv-cache'
uv sync --frozen
uv run pytest
```

This is a process-local development workaround, not a requirement for every
machine. Do not change Windows ACLs, commit cache contents, or add these values to
the application's `.env` file.

## Docker command is unavailable

Symptom: `docker --version` and `docker compose version` cannot run; neither Docker Desktop nor the `com.docker.service` service is present.

Resolution: install and start Docker Desktop using the owner's normal Windows setup. This requires user-controlled system software installation and may require WSL2; do not replace it with ad-hoc local PostgreSQL changes. After Docker is running, execute `docker compose config`, then `docker compose up -d` and `docker compose ps` before using the UI or a smoke target.

## Docker CLI works but Docker Engine is unavailable

Symptom: `docker --version` and `docker compose version` work, but `docker info`
or `docker compose up -d` fails or never returns. On Windows, an error mentioning
the `dockerDesktopLinuxEngine` named pipe means the Docker Desktop Linux Engine is
not ready or is no longer running.

Cause: this is a Docker Desktop/WSL runtime startup issue, not a repository Compose configuration issue. Do not change `compose.yaml`, install a second PostgreSQL, or remove volumes to work around it.

Resolution: use Docker Desktop's Troubleshoot screen to restart Docker Desktop and
wait for its Engine-ready status. If it remains stuck, follow Docker Desktop's
displayed WSL2/virtualization repair guidance. Once `docker info` succeeds in a
newly opened terminal, rerun `docker compose config`, then `docker compose up -d`.
Do not interpret a successful `docker compose config` as Engine readiness.

## Compose starts PostgreSQL but agent service uses SQLite

Symptom: the PostgreSQL container is healthy, but service logs or behavior show the SQLite checkpointer instead.

Cause: an empty `DATABASE_TYPE` selects the settings default of SQLite. A plain `env_file` does not automatically supply the internal Compose hostname `postgres` to the application.

Resolution: the repository's `compose.yaml` explicitly sets the `agent_service` database type and PostgreSQL connection values to match its `postgres` service. Keep local `uv` development on SQLite unless you intentionally provide a reachable PostgreSQL configuration.

## Docker services are running but show `unhealthy`

Symptom: FastAPI and Streamlit answer their mapped host ports, but `docker compose ps` reports them as unhealthy.

Cause: the service images are based on `python:slim`, which does not include `curl`. A Compose healthcheck that runs `curl` therefore fails even when the Python application is ready.

Resolution in this repository: `compose.yaml` uses Python's built-in `urllib.request.urlopen` for both healthchecks. Do not add an operating-system package solely for a one-line health probe.

## Windows host FastAPI cannot open an async PostgreSQL connection

Symptom: the server logs `Psycopg cannot use the 'ProactorEventLoop' to run in async mode` and startup eventually fails with a pool timeout.

Cause: psycopg's async driver needs a Selector event loop on Windows. The installed Uvicorn version's `loop="auto"` chooses Proactor on Windows even if an earlier event-loop policy was set.

Resolution in this repository: `src/run_service.py` sets `WindowsSelectorEventLoopPolicy` and passes `loop="none"` to Uvicorn on Windows, allowing asyncio to create the compatible loop. Start the API through that file; this is not a reason to remove PostgreSQL or fall back to SQLite.

## PostgreSQL smoke script would remove local data

Symptom: `scripts/smoke_test.sh postgres` looks attractive for local verification, but its cleanup includes `docker compose down -v`.

Resolution: do not run that wrapper against a reusable development volume. Start the existing Compose stack, then use `uv run pytest tests/smoke/test_persistence.py -v --run-docker` with `AGENT_URL` and a unique `SMOKE_THREAD_ID`. Query the matching PostgreSQL checkpoints if an explicit backend proof is needed.

## Service does not start

Check:

- `.env`
- `uv sync --frozen`
- Docker Engine readiness (`docker info`)
- port conflicts
- SQLite path or PostgreSQL health, depending on `DATABASE_TYPE`
- `USE_FAKE_MODEL=true` for a no-network local check

TaskPilot business migrations are independent from LangGraph persistence. Set a
valid PostgreSQL-only `TASKPILOT_DATABASE_URL` before running
`uv run alembic upgrade head`; an unset URL fails clearly and a SQLite URL is
rejected. LangGraph's existing `DATABASE_TYPE=sqlite` local checkpoint fallback
is unaffected. Do not run migrations from application startup.

## TaskPilot-protected request always returns 401

Symptom: a route that depends on `service.auth_dependency.require_principal`
returns `{"detail": "Not authenticated"}` with `WWW-Authenticate: Bearer`
although a credential was supplied.

Cause and resolution, in order:

- `AUTH_SECRET` is not a TaskPilot credential. It only guards the retained upstream router. A TaskPilot route needs an opaque token issued by `AuthService.login`; an `AUTH_SECRET` value is rejected exactly like an unknown token.
- Sessions expire 24 hours after issuance, and explicit revocation sets `revoked_at`. Both mean the token is no longer valid; the row is retained for audit rather than deleted.
- Every request re-reads state. An inactive user, inactive membership, inactive organization, missing related row, or a session whose membership no longer belongs to the session's user all produce the same 401.
- The dependency resolves tokens through `service.auth_dependency.get_session_factory`, which reads `TASKPILOT_DATABASE_URL`. A process or test pointed at a different database than the one that issued the token sees an unknown token.

The single generic envelope is intentional: the response must not distinguish
unknown, revoked, expired, or inactive credentials.

## Unexpected 403 or 404 from a protected operation

Symptom: the credential is valid but the operation is refused.

Cause: 403 means the caller is inside the principal's own organization and lacks
the role the operation requires; 404 means the resource is outside the
principal's organization or does not exist. The two must not be interchanged: a
foreign resource always answers 404, even for an owner, so a 403/404 difference
cannot be used to enumerate another tenant's resources. Roles have no hierarchy,
so a `member`-only operation legitimately rejects an `owner`.

## `TASKPILOT_DATABASE_URL` is rejected

Symptom: settings validation or engine creation fails with a PostgreSQL
requirement.

Cause: TaskPilot business persistence is PostgreSQL-only. `TASKPILOT_DATABASE_URL`
accepts `postgresql://` or `postgresql+psycopg://` and rejects SQLite. The
upstream `DATABASE_TYPE` setting still selects the LangGraph checkpoint backend
and is independent of it; SQLite remains valid for local LangGraph checkpoints
and does not enable business persistence.

## Persistence and security tests skip

Symptom: `uv run pytest tests/persistence -q` reports skips instead of running
the disposable-database suites.

Cause: those tests require `TASKPILOT_TEST_DATABASE_URL`, and the URL must name a
database whose name contains `test`. Each scenario then creates and drops its own
uniquely named database and never touches the base database. Start the existing
container with `docker compose up -d postgres`, then:

```powershell
$env:TASKPILOT_TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test'
uv run pytest tests/persistence -q
```

Do not use `docker compose down -v` or `docker system prune` to prepare a test
run: that removes named volumes and developer data. A skipped run is not evidence
that the authentication or tenant rules hold.

## Bootstrap refuses to run

Symptom: `uv run python scripts/bootstrap_owner.py` exits non-zero, or the hidden
password prompt fails closed.

Cause: bootstrap is fail-closed by design. `--password` and positional plaintext
are always rejected, and the password must be entered twice at hidden prompts.
Partial, conflicting, inactive, or non-owner state is never repaired,
overwritten, elevated, or password-reset, and an exact complete active owner
state is a successful no-op. Fix the conflicting state deliberately; there is no
force flag.

## Application log is not JSON or has no request ID

Application records handled by the configured root handlers are emitted as
JSON lines. A `request_id` value is present for records created inside the HTTP
middleware context; startup, shutdown, background, or command-line records are
expected to use `null` because they do not belong to an HTTP request.

If a library or test installs a new logging handler after service configuration,
call the existing `configure_logging()` helper so that handler receives the
structured formatter. Do not log request headers, settings dumps, or raw
connection exceptions to diagnose formatting: Authorization values, credentials,
tokens, passwords, and credential-bearing URLs are intentionally redacted.

An exception that escapes to Starlette's outer `ServerErrorMiddleware` may still
produce a final 500 response without `X-Request-ID`; T014 does not change that
known T013 response-header limitation.

## LLM call fails

Check:

- API key exists
- model/provider configured
- network allowed
- rate limit
- provider status
- request payload size

Do not print API keys while debugging.

For local service/test verification without a provider account, create ignored `.env` from `.env.example` and set `USE_FAKE_MODEL=true`. This validates FastAPI, LangGraph and persistence wiring, but does not validate a real provider call.

## DB migration fails

Do not delete the database immediately.

Check:

- current revision
- migration history
- whether local data can be recreated
- incompatible schema changes

## Agent loops

Check:

- retry budget
- replan budget
- terminal condition
- invalid verifier routing
- tool error classification

All loops must be bounded.

## Approval stuck

Check:

- approval status
- checkpoint exists
- run/task status alignment
- authorization
- resume event consumed exactly once
