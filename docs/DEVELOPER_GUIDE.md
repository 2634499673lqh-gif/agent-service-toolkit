# Developer Guide — Verified local baseline

## Command status

The repository supports Python 3.12–3.14 and CI pins `uv 0.12.5`. The verified Windows baseline on 2026-09-10 used Python 3.12.4 and uv 0.12.12. `uv sync --frozen` created the project-managed `.venv` from the existing `uv.lock` without changing dependency definitions or the lock file.

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
uv run ruff check --output-format github

# Types and documentation
uv run pyrefly check
uv run pymarkdown scan README.md docs/

# API, then UI in a second terminal. USE_FAKE_MODEL=true avoids real LLM calls.
python src/run_service.py
streamlit run src/streamlit_app.py

# PostgreSQL + API + UI development stack
docker compose watch
```

On this Windows installation, Docker Desktop is installed per-user. If a terminal opened before installation cannot find `docker`, open a new terminal after Docker Desktop is running; the CLI is installed under the Docker Desktop per-user program directory. Do not set a project-specific `DOCKER_HOST` as a workaround.

### Docker versus local persistence

Local `uv` development follows `.env`: an empty `DATABASE_TYPE` selects the code's SQLite default. Docker Compose deliberately overrides only the `agent_service` database settings to use `DATABASE_TYPE=postgres` and the same user/password/database defaults as its `postgres` service; its host is the internal Compose hostname `postgres`. This keeps the Docker stack on PostgreSQL while preserving SQLite as a simple local, no-container path.

The verified Compose stack is `postgres` (PostgreSQL 16, host port 5432, named volume `postgres_data`), `agent_service` (FastAPI, port 8080), and `streamlit_app` (port 8501). Use the existing stack rather than a separately created development database:

```powershell
docker compose config
docker compose up -d --build
docker compose ps
```

For a PostgreSQL smoke check that preserves existing containers and volumes, start the service stack and run the test directly:

```powershell
$env:AGENT_URL = 'http://127.0.0.1:8080'
$env:SMOKE_THREAD_ID = 'local-postgres-smoke'
uv run pytest tests/smoke/test_persistence.py -v --run-docker
```

Do not run `scripts/smoke_test.sh postgres` against a reusable local environment without first reading it: its cleanup currently uses `docker compose down -v`. The direct test above is the safe equivalent for an existing named volume.

On Windows, run the API through `python src/run_service.py`, not an ad-hoc `uvicorn service:app` command, when using async PostgreSQL. The entrypoint selects the Windows Selector event loop that psycopg requires. Docker uses Linux and is unaffected.

The service health endpoint is `GET /health`; metadata is `GET /info`; OpenAPI is `GET /openapi.json`. When configured, the shared development secret requires `Authorization: Bearer <AUTH_SECRET>` on router endpoints.

## Persistence and migrations

There is no application migration command: the repository has no SQLAlchemy, Alembic, ORM, migration directory, or TaskPilot business schema. SQLite is the lightweight local-development checkpoint. PostgreSQL checkpoint and Store persistence is LangGraph-owned. Future TaskPilot business persistence is not implemented and requires separate migration ownership. Phase 1 therefore adds no framework, placeholder migration, ORM, or business table.

After Docker/dependencies are ready, optional upstream confidence checks are:

```bash
./scripts/smoke_test.sh postgres
./scripts/smoke_test.sh mongo
./scripts/smoke_test.sh agui
```

They are not default CI and need Docker. Langfuse is a separate heavy target.

## Results observed in the environment repair

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
