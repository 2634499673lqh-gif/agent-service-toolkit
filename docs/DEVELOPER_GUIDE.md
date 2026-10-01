# Developer Guide

This guide is the verified local development path for the current repository.

## Prerequisites and setup

- Python 3.12–3.14
- `uv` 0.12.5 (the CI version)
- Docker Desktop with Compose when using the container or PostgreSQL workflows
- PostgreSQL 16 for business-persistence and PostgreSQL integration tests

From a writable clone:

```powershell
Copy-Item .env.example .env
# Set USE_FAKE_MODEL=true for deterministic local checks without an LLM provider.
uv sync --frozen
```

Keep real provider keys, passwords, bearer tokens, and credential paths in the ignored `.env` or the host environment. `AGENT_URL` points the Streamlit client at the API; local defaults resolve to `http://{HOST}:{PORT}`. `DATABASE_TYPE=sqlite` is the simple local LangGraph checkpoint default. PostgreSQL settings are required when `DATABASE_TYPE=postgres`. `TASKPILOT_DATABASE_URL` is a separate PostgreSQL-only URL for TaskPilot business persistence.

## Persistence, migrations, and bootstrap

TaskPilot business tables live in the `taskpilot` PostgreSQL schema and are owned by Alembic. Application startup does **not** run migrations:

```powershell
uv run alembic upgrade head
uv run alembic check
```

Create the first organization owner with the controlled hidden-password CLI after the business database is reachable:

```powershell
uv run python scripts/bootstrap_owner.py --organization-name "Acme" --email owner@example.com
```

The password is entered through two hidden prompts; it is not accepted as a command-line or environment value. The CLI is idempotent for an exact active-owner state. See `docs/DATABASE_DESIGN.md` and `docs/SECURITY_HITL.md` for the data and authorization contracts.

## Local startup

The normal local sequence is:

```powershell
uv run alembic upgrade head       # only when using TaskPilot business persistence
uv run python src/run_service.py  # FastAPI, usually :8080
uv run streamlit run src/streamlit_app.py  # Product UI, usually :8501
```

Run the API and Streamlit commands in separate terminals. The API exposes `GET /health` and `/health/live` for dependency-free liveness, `GET /health/ready` for configured database readiness, `GET /info` for metadata, and `GET /openapi.json` for the API contract. Local API startup does not implicitly migrate the database.

For the portfolio flow, open `http://localhost:8501`, sign in with the owner
created above, create a task describing the supported Wuhan East Lake July 2023
versus July 2024 comparison, and choose **Start run**. The API dispatches the
tenant-scoped TaskRun to the bounded process-local runtime. See
`docs/DEMO_GUIDE.md` for the result-reading order and the cached-raster/live-
metadata distinction.

The Windows entrypoint is `src/run_service.py`; it selects the Selector event loop required by psycopg async connections. Do not replace it with an ad-hoc Uvicorn command for the PostgreSQL path.

## Docker Compose startup

Compose deliberately uses PostgreSQL for the API and runs migrations as a separate one-shot service:

```powershell
docker info
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs --tail 50 postgres migrate agent_service streamlit_app
docker compose stop
```

The topology is:

```text
postgres (healthy)
  → migrate (alembic upgrade head, exits successfully)
  → agent_service (:8080, /health/ready)
  → streamlit_app (:8501, /healthz)
```

The API service uses the internal host name `postgres`; the UI uses `AGENT_URL=http://agent_service:8080`. `postgres_data` is a named volume. `docker compose config` is a static configuration check; `up`, `ps`, logs, and health checks require a running Docker Engine. `docker compose stop` preserves the named volume. The Compose files do not make application startup itself responsible for migrations.

## Validation commands

The normal local checks are:

```powershell
uv run pytest
uv run ruff format --check
uv run ruff check
uv run pyrefly check
uv run pymarkdown scan README.md docs/
```

Focused Product evidence can be run with:

```powershell
uv run pytest tests/app tests/client -q
```

PostgreSQL integration tests require a disposable database URL whose database name contains `test`:

```powershell
$env:TASKPILOT_TEST_DATABASE_URL = 'postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test'
uv run pytest tests/persistence -q
```

CI separates lock validation, evaluation smoke, Ruff, Pyrefly, ordinary pytest, Markdown lint, Docker image checks, PostgreSQL migration/race checks, and an opt-in Compose end-to-end job. See `.github/workflows/test.yml` for the exact CI environment and gates. Optional smoke scripts under `scripts/` may remove Compose volumes; read them before use.

## Troubleshooting references

- `docs/DEMO_GUIDE.md` — deterministic existing-behavior Product walkthrough.

- `docs/TROUBLESHOOTING.md` — common local, provider, database, and Docker failures.
- `docs/DEPLOYMENT_RUNBOOK.md` — deployment and readiness operations.
- `docs/DATABASE_DESIGN.md` — schema and migration ownership.
- `docs/SECURITY_HITL.md` — authentication, tenant isolation, roles, and approval rules.
- `docs/ARCHITECTURE.md` — system boundaries and deferred scope.

When a command fails, first confirm `.env` values, the selected persistence backend, migration state, and whether Docker Engine or PostgreSQL is actually running. Do not treat a skipped environment-gated PostgreSQL test as database evidence.
