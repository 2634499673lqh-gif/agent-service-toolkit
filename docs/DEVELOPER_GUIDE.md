# Developer Guide — Verified local baseline

## Command status

The repository supports Python 3.12–3.14 and CI pins `uv 0.12.5`. The verified Windows baseline uses Python 3.12.4 and uv 0.12.12. `uv sync --frozen` creates the project-managed `.venv` from the existing `uv.lock` without changing dependency definitions or the lock file.

The T016 verification run on 2026-09-13 passed the local dependency, test, lint,
type-check, and application-entrypoint checks. `docker compose config` also passed,
but Docker Engine was unavailable in that shell, so Compose container startup was
blocked by Docker Desktop rather than by repository configuration. The exact
results are recorded below; do not treat a successful static Compose render as
proof that the Engine is running.

## Normal local setup

Run in a writable clone. Do not commit `.env`.

```powershell
Copy-Item .env.example .env
# For deterministic local verification, set USE_FAKE_MODEL=true in .env.
uv sync --frozen
```

`Settings` requires an LLM provider credential/configuration or `USE_FAKE_MODEL=true`; the fake model is appropriate for ordinary deterministic tests.

### Settings validation rules

Configuration is validated when `Settings` is created. The selected persistence
backend controls which database fields are required: SQLite keeps its local
`checkpoints.db` fallback and does not require PostgreSQL or MongoDB values;
PostgreSQL requires its host, port, database, user and password plus valid pool
bounds; MongoDB requires host, port and database, while authentication remains
optional but must be supplied as a complete user/password/auth-source set.

Provider and tracing settings are opt-in. A partially configured
OpenAI-compatible or Azure provider, an Ollama endpoint without a model, or
enabled LangChain/Langfuse tracing without its credentials fails fast. An
Ollama model may still use the library's local default endpoint, and
`USE_FAKE_MODEL=true` remains a valid no-network development fallback. Error
messages identify missing setting names only; secret values are never emitted.

### Structured application logs

The service keeps Python's standard-library `logging` and formats application
records as one JSON object per line. Request middleware emits `request.started`,
`request.completed`, and `request.failed` events with the server-generated
`request_id`; the ID is held in request-local async context and is reset when
the request finishes. The formatter includes UTC timestamp, level, logger,
message, and safe request metadata without changing API or SSE payloads.

Log messages and nested mapping/list values are redacted by sensitive field
name. Authorization/Bearer values, API keys, passwords, tokens, credentials,
and credential-bearing URLs are also masked. Ordinary fields such as model,
host, port, event, and request ID remain visible for diagnosis. This is a
Phase 1 application-log safeguard, not distributed tracing or a persisted audit
system.

### Environment template and secret safety

`.env.example` is the non-secret inventory for the current upstream runtime. It mirrors the fields in `src/core/settings.py` and the small set of client/integration variables read directly by the existing code (for example `AGENT_URL`, `AWS_KB_ID`, and voice-provider settings). Empty values are intentional placeholders; defaults are shown only for non-sensitive settings.

Keep provider keys, passwords, bearer tokens, and credential-file paths in the ignored `.env` file or in the host environment. Never paste values from `.env` into documentation, tests, logs, screenshots, or commits. Verify that Git still ignores the local file before committing:

```powershell
git check-ignore -v .env
git status --short --ignored .env
```

The template must remain safe to publish: it contains no real API keys, passwords, tokens, or private credential paths. Do not add speculative environment variables for future TaskPilot phases; update the template only when an implemented runtime setting is introduced.

## Verified commands after setup

```powershell
# Full local test suite
uv run pytest

# Style and imports
uv run ruff format --check
uv run ruff check

# Types and documentation
uv run pyrefly check
uv run pymarkdown scan README.md docs/

# API, then UI in a second terminal. USE_FAKE_MODEL=true avoids real LLM calls.
uv run python src/run_service.py
uv run streamlit run src/streamlit_app.py
```

On this Windows installation, Docker Desktop is installed per-user. If a terminal opened before installation cannot find `docker`, open a new terminal after Docker Desktop is running; the CLI is installed under the Docker Desktop per-user program directory. Do not set a project-specific `DOCKER_HOST` as a workaround.

### Docker versus local persistence

Local `uv` development follows `.env`: an empty `DATABASE_TYPE` selects the code's SQLite default. Docker Compose deliberately overrides only the `agent_service` database settings to use `DATABASE_TYPE=postgres` and the same user/password/database defaults as its `postgres` service; its host is the internal Compose hostname `postgres`. This keeps the Docker stack on PostgreSQL while preserving SQLite as a simple local, no-container path.

The Compose stack is `postgres` (PostgreSQL 16, host port 5432, named volume `postgres_data`), `agent_service` (FastAPI, port 8080), and `streamlit_app` (port 8501). The Phase 0.5 verification previously confirmed this stack against PostgreSQL; use the existing stack rather than a separately created development database:

```powershell
docker compose config
docker compose up -d
docker compose ps
docker compose logs --tail 50 agent_service streamlit_app postgres
docker compose stop
```

`docker compose stop` stops the containers without removing the named
`postgres_data` volume. Run these commands only after `docker info` succeeds. If
the Engine is unavailable, `docker compose config` can still pass because it is a
static configuration check; `up`, `ps`, `logs`, and `stop` need a running Engine.

For a PostgreSQL smoke check that preserves existing containers and volumes, start the service stack and run the test directly:

```powershell
$env:AGENT_URL = 'http://127.0.0.1:8080'
$env:SMOKE_THREAD_ID = 'local-postgres-smoke'
uv run pytest tests/smoke/test_persistence.py -v --run-docker
```

Do not run `scripts/smoke_test.sh postgres` against a reusable local environment without first reading it: its cleanup currently uses `docker compose down -v`. The direct test above is the safe equivalent for an existing named volume.

On Windows, run the API through `uv run python src/run_service.py`, not an ad-hoc
`uvicorn service:app` command, when using async PostgreSQL. The entrypoint selects
the Windows Selector event loop that psycopg requires. Docker uses Linux and is
unaffected.

The service health endpoint is `GET /health`; metadata is `GET /info`; OpenAPI is `GET /openapi.json`. When configured, the shared development secret requires `Authorization: Bearer <AUTH_SECRET>` on router endpoints.

## Persistence and migrations

TaskPilot business persistence is PostgreSQL-only and is intentionally separate
from the existing LangGraph backend selected by `DATABASE_TYPE`. Configure an
explicit `TASKPILOT_DATABASE_URL` (for example,
`postgresql+psycopg://user:password@host/db`) when using the business engine;
SQLite remains valid for local LangGraph checkpoints and does not enable
business persistence. The URL is a secret setting and is never logged.

The TaskPilot Alembic environment lives in `migrations/` and owns only the
`taskpilot` schema. A release/deployment step applies it with:

```powershell
uv run alembic upgrade head
```

Application startup does not run migrations. The first revision (`t021_organization`)
creates `taskpilot`, `taskpilot.alembic_version`, and `taskpilot.organizations`;
the second revision (`t022_user`) adds `taskpilot.users`; the third revision
(`t022a_membership`) adds `taskpilot.memberships`; the fourth
(`t023_auth_session`) adds `taskpilot.auth_sessions`. Downgrading removes only the
objects the target revision owns, so `alembic downgrade t022a_membership` drops the
session table while leaving users, memberships, and organizations intact, and
`alembic downgrade t022_user` additionally drops memberships. The environment's
pre-reflection ownership filter prevents LangGraph/public tables from becoming
autogenerate targets. `OrganizationRepository`, `UserRepository`, and
`MembershipRepository`, and `AuthSessionRepository` accept an `AsyncSession`,
flush writes, and leave commit/rollback to the service transaction boundary.
`uv run alembic check` reports "No new upgrade operations detected" when the ORM
metadata matches the migrated database.

### Authentication (T023)

Passwords are Argon2id hashes from `pwdlib` (`persistence/passwords.py`).
`service/session.py` issues opaque sessions: the raw base64url token is returned
once, and only its SHA-256 digest is stored in `taskpilot.auth_sessions`. Login
fails with one generic error for unknown email, wrong password, inactive user,
and no eligible membership, and a session is bound to exactly one user and one
membership.

For a user with several eligible organizations, `AuthService.login` accepts an
optional `organization_id` selector. With no selector it issues automatically
when exactly one membership is eligible and returns
`OrganizationSelectionRequired` (code `ORGANIZATION_SELECTION_REQUIRED` plus the
sorted eligible organization IDs) when several are; the result holds no token or
session. Passing the selector issues a session bound to that verified
membership, and switching organizations is simply another `login` call with the
target selector.

### Request authentication (T024)

Protected TaskPilot endpoints depend on `service.auth_dependency.require_principal`,
which resolves `Authorization: Bearer <opaque-token>` into a server-derived
`CurrentPrincipal`. Every request re-reads the session, user, membership, and
organization, so revocation, expiry, deactivation, and role changes take effect
immediately. Any failure returns one generic 401 `{"detail": "Not authenticated"}`
with `WWW-Authenticate: Bearer`, and no principal is constructed. The dependency
opens and closes its own session per request and never commits. Overriding
`service.auth_dependency.get_session_factory` (its `Depends` provider) is the
supported way for tests to point the dependency at a specific database.

```python
from typing import Annotated

from fastapi import Depends

from service.session import CurrentPrincipal


@app.get("/api/v1/me")
async def me(principal: Annotated[CurrentPrincipal, Depends(require_principal)]): ...
```

### Authorization (T025)

Protected operations use the single policy boundary in `service/authorization`
instead of comparing roles inline:

- `require_authenticated(principal)` fails closed with 401 when no server-derived principal exists; authorization never turns a missing credential into 403.
- `require_active_membership(principal)` is the explicit policy seam named by the helper contract. T024 already proves an active membership before a principal exists, so it performs no second lookup.
- `require_role(principal, allowed_roles)` returns 403 when the current membership role is not in the operation's allowed set. Roles are an explicit set match with no hierarchy, so a `member`-only operation rejects an `owner`.
- `require_resource_tenant(principal, resource_organization_id)` returns 404 for a resource outside the principal's organization, identical to a nonexistent resource.
- The FastAPI wrappers `require_authenticated_principal`, `require_role_dependency([...])`, and `require_resource_tenant_dependency(...)` convert the same decisions into `HTTPException`.

Tenant-owned lookups must carry the tenant predicate in the query itself
(`WHERE id = :id AND organization_id = :principal_organization_id`, as in
`OrganizationRepository.get_in_principal_tenant`) so a foreign row is not found
rather than fetched and compared in Python, and tenant existence must be
resolved before any role check. `APPROVAL_DECISION_ROLES` freezes the documented
owner/admin approval gate; no approval records exist yet.

### Security matrix (T026)

The authoritative negative matrix is
`tests/persistence/test_security_matrix_integration.py`. It uses the same
disposable PostgreSQL database as the persistence suite:

```powershell
$env:TASKPILOT_TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test'
uv run pytest tests/persistence -q
```

Without that variable the persistence and security suites skip, so a green run
does not prove the authentication or tenant rules.

Create the first organization owner with the controlled CLI. The password is
read from a hidden prompt and must be entered twice; `--password` and positional
plaintext are rejected:

```powershell
uv run python scripts/bootstrap_owner.py --organization-name "Acme" --email owner@example.com
```

The organization name and owner email may also come from
`TASKPILOT_BOOTSTRAP_ORGANIZATION_NAME` and `TASKPILOT_BOOTSTRAP_EMAIL`, or from
interactive prompts. The password has no flag, positional, or environment-variable
form: it is always read from two hidden prompts, so no automation shortcut can
bypass the confirmation. Re-running against an exact active owner state is a
no-op. Argument errors and database failures are reported with fixed messages
that never echo the supplied values, the SQL, or the bind parameters.

The persistence tests are PostgreSQL-only and run only when a disposable test
database is configured. The URL must name a database containing `test`; the
suite creates and drops uniquely named databases for each scenario and never
touches the base database:

```powershell
$env:TASKPILOT_TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test'
uv run pytest tests/persistence -q
```

After Docker/dependencies are ready, the repository also contains optional upstream
confidence checks:

```bash
./scripts/smoke_test.sh postgres
./scripts/smoke_test.sh mongo
./scripts/smoke_test.sh agui
```

They are not default CI and need Docker. Read `scripts/smoke_test.sh` before using
one: its cleanup includes `docker compose down -v`, which removes named volumes.
The `langfuse` target is a separate heavy target and is not part of the default
smoke run.

## T016 verification snapshot (2026-09-13)

```text
uv sync --frozen                         PASS: checked 248 packages
uv run pytest                            PASS: 206 passed, 4 skipped, 18 warnings
uv run ruff format --check               PASS
uv run ruff check                        PASS
uv run pyrefly check                     PASS: 0 errors (11 known suppressions)
uv run pymarkdown scan README.md docs/   FAIL: pre-existing MD022/MD032 debt in untouched docs
FastAPI local entrypoint                 PASS: /health and /info returned HTTP 200
Streamlit local entrypoint               PASS: /healthz and / returned HTTP 200
docker compose config                    PASS
docker compose up -d                     BLOCKED: Docker Desktop Linux Engine named pipe unavailable
```

The touched documentation files pass their focused Markdown check:
`uv run pymarkdown scan docs/DEVELOPER_GUIDE.md docs/TROUBLESHOOTING.md`. The full
scan is still non-zero, but only because of pre-existing formatting violations in
files this phase did not touch: as of 2026-09-18 the remaining 22 are in
`docs/AGENT_DESIGN.md` (5), `docs/CONTEXT_ENGINEERING.md` (1),
`docs/DEPLOYMENT_RUNBOOK.md` (3), and `docs/OBSERVABILITY_EVAL.md` (13).
`docs/API_CONVENTIONS.md`, `docs/SECURITY_HITL.md`, and `docs/USER_GUIDE.md` were
fixed here because T027 edits them; the rest is untouched debt for a separate
formatting-only change.

## Historical Phase 0.5 verification (2026-09-10)

```text
uv --version                             PASS: 0.12.12
python --version                         PASS: Python 3.12.4
python -m pip --version                  PASS: pip 24.0
uv sync --frozen                         PASS: created .venv from uv.lock
uv run pytest                            PASS: 191 passed, 4 skipped, 18 warnings
uv run ruff format --check               PASS
uv run ruff check --output-format concise PASS
uv run pyrefly check                     PASS: 0 errors (11 known suppressions)
FastAPI /health, /info, /openapi.json    PASS with USE_FAKE_MODEL=true
FastAPI invoke + /history                PASS with SQLite checkpoint
Streamlit /healthz and base page         PASS
Docker CLI / Compose plugin              PASS: Docker 29.7.2 / Compose v5.5.1
Docker Engine                             PASS: Docker Desktop linux engine
docker compose config                     PASS
PostgreSQL 16 / health / volume           PASS: healthy, host 5432, postgres_data
PostgreSQL checkpoint / Store             PASS: real smoke writes and Store put/get
Docker FastAPI / Streamlit                PASS: healthy; mapped endpoints return 200
```

The four skipped tests require the explicit `--run-docker` option. The warnings are upstream dependency deprecations, not failing assertions. The PostgreSQL smoke test was executed separately with `--run-docker` and passed twice: once against a host FastAPI process and once against Docker FastAPI. Both cases wrote the unique smoke thread's checkpoints into PostgreSQL, so neither used a silent SQLite fallback.

## Change workflow

1. Read `AGENTS.md`, the current phase prompt, relevant code and tests.
2. Make a small plan; identify authorization, migration and compatibility effects.
3. Change only scoped files; run focused checks, then broader checks.
4. Update stale docs and append an honest progress entry.
5. Review the diff. Never modify production code merely to conceal an environmental baseline failure.
