# Progress Log

> Append one entry per completed task. Do not delete old entries.

### 2026-09-11 — T010: Repository Branding and Attribution

Status: DONE

What changed:
- Replaced the root starter-kit placeholder with an accurate TaskPilot Phase 1 landing page.
- Documented the current boundary so readers do not mistake retained upstream chat/runtime examples for unimplemented TaskPilot domains.
- Added an explicit link to the upstream `agent-service-toolkit` project and its retained documentation and MIT license.
- Updated the package description to identify TaskPilot while retaining the existing distribution name and upstream author metadata; changing the distribution name would require a prohibited `uv.lock` update.

Files changed:
- `README.md`
- `pyproject.toml`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pymarkdown scan README.md` -> PASS.

Architecture/security notes:
- No source code, dependencies, lock file, public API, persistence adapter, or TaskPilot business domain was changed.
- `README_UPSTREAM.md` and `LICENSE` were inspected and deliberately left unchanged to preserve upstream attribution and license text.

Known limitations:
- The repository remains a Phase 1 foundation; TaskPilot product domains are intentionally not implemented.

Learner notes:
- Problem solved: the repository now identifies TaskPilot without presenting upstream examples as completed TaskPilot capabilities.
- Read these files: `README.md`, `README_UPSTREAM.md`, `LICENSE`, `pyproject.toml`, `docs/ARCHITECTURE.md`.
- Key concept: downstream branding can be accurate and transparent when it preserves upstream license and attribution.
- Small exercise: compare `README.md` with `README_UPSTREAM.md`, then identify which stated capabilities are upstream runtime examples versus planned TaskPilot domains.
- Ignore for now: package renaming, domain schemas, and runtime changes; they are outside this documentation-only task.

Recommended next task:
- T011 — Environment template audit. Do not begin it as part of T010.

### 2026-09-10 — Phase 0.5: Docker / PostgreSQL Baseline Verification (resumed)

Status: DONE — READY FOR PHASE 1

What changed:
- Re-ran `docker compose config`; Docker CLI 29.7.2, Compose v5.5.1 and the Docker Desktop Linux Engine all responded normally.
- Started the repository's existing PostgreSQL 16 service without removing its named `postgres_data` volume. It became healthy and exposed port 5432; `agent_service` connection configuration comes from explicit Compose overrides, not an empty `.env` value.
- Verified a configuration defect: `env_file: .env` alone allowed an empty `DATABASE_TYPE` to select SQLite. `compose.yaml` now explicitly supplies `DATABASE_TYPE=postgres`, the `postgres` hostname, and Compose PostgreSQL defaults to `agent_service`, preserving SQLite for local `uv` development.
- Found and fixed two small, environment-layer issues during real verification: Windows Uvicorn's `loop=auto` forced a psycopg-incompatible Proactor loop, and the two slim images did not contain `curl` even though their Compose healthchecks used it. The entrypoint now leaves the configured Windows Selector loop intact; Compose healthchecks use Python's standard library.
- No TaskPilot feature, Agent behavior, domain schema, dependency version, SQLite support, or upstream reference file was changed. No `down -v` command was used.

Files changed:
- `compose.yaml`
- `src/run_service.py`
- `docs/DEVELOPER_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `docker --version`, `docker compose version`, `docker info` -> PASS: CLI 29.7.2, Compose v5.5.1, Desktop Linux Engine.
- `docker compose config` -> PASS before and after the configuration repair.
- `docker compose up -d postgres` -> PASS: PostgreSQL 16 healthy on host port 5432 with named `postgres_data` volume.
- PostgreSQL `psql` connection -> PASS: configured development database and user responded; no secret values recorded.
- Host FastAPI with `DATABASE_TYPE=postgres` and `USE_FAKE_MODEL=true` -> PASS after the Windows event-loop fix.
- `uv run pytest tests/smoke/test_persistence.py -v --run-docker` -> PASS: 2 passed against host FastAPI; the unique smoke thread had 8 PostgreSQL checkpoint rows.
- Direct LangGraph PostgreSQL Store put/get -> PASS on host and inside `agent_service`; database records identify the verification backend as PostgreSQL.
- Docker Compose full stack -> PASS: PostgreSQL, FastAPI and Streamlit all healthy; FastAPI `/health` and `/info`, Streamlit health/root each returned 200; FastAPI reports fake as default model.
- Docker FastAPI smoke -> PASS: 2 passed; the unique Docker smoke thread had 8 PostgreSQL checkpoint rows and `/app/checkpoints.db` was absent.
- `uv run ruff format --check src/run_service.py` and `uv run ruff check src/run_service.py` -> PASS.
- `uv run pytest` -> PASS: 191 passed, 4 skipped, 18 warnings in 63.36s.

Architecture/security notes:
- PostgreSQL holds LangGraph's library-managed checkpointer tables for thread-scoped conversation history and Store tables for long-term cross-thread values. It is not a TaskPilot business schema and has no application migration layer yet.
- The explicit Compose environment plus observed PostgreSQL rows and absence of the configured SQLite probe/file rule out a silent SQLite fallback for the verified Docker and host PostgreSQL paths.
- All application invokes used `USE_FAKE_MODEL=true`; no real LLM provider credential or request was used. `.env` values were not output.

Known limitations:
- The upstream wrapper `scripts/smoke_test.sh postgres` was not run as a wrapper because it ends with `docker compose down -v`; its persistence test was run directly and its PostgreSQL row check was reproduced safely.
- Full documentation Markdown lint still has pre-existing failures outside the touched Phase 0.5 files. Dependency deprecation warnings remain upstream maintenance items.

Learner notes:
- Problem solved: a running database alone is not enough; the application must be explicitly pointed at it, and proof comes from observing its own checkpoint and Store records.
- Read these files: `compose.yaml`, `src/run_service.py`, `src/memory/postgres.py`, `src/service/service.py`, `tests/smoke/test_persistence.py`.
- Key concept: PostgreSQL is the durable server-backed option for LangGraph state; SQLite is the lightweight local default. They are alternative persistence backends, not different TaskPilot features.
- Small exercise: run `docker compose up -d`, `docker compose ps`, and `docker compose logs --tail 50 agent_service`, then run the direct smoke test from the developer guide.
- Ignore for now: the internal `checkpoints`/`store` column layout, Docker build cache details, and the upstream deprecation warnings.

Recommended next task:
- Begin only the separately authorized Phase 1 foundation work. Preserve the verified Docker/PostgreSQL wiring and do not introduce TaskPilot domain features as part of this baseline task.

### 2026-09-10 — Phase 0.5: Docker / PostgreSQL Baseline Verification

Status: BLOCKED by Docker Desktop Engine runtime; no repository or database change was made

What changed:
- Re-read the repository constraints, verified Compose/Dockerfile/environment configuration, and inspected local `.env` key names with all values redacted.
- Located Docker Desktop's per-user installation and confirmed Docker CLI 29.7.2 plus Compose v5.5.1.
- Confirmed the Docker context and expected named pipes exist, but Engine requests (`docker info` / `docker version`) did not return.
- Inspected recent Docker Desktop backend status logs; they explicitly report that the backend is not running.
- Repaired a confirmed Compose-only environment gap: `agent_service` now explicitly selects PostgreSQL and receives the same Compose user/password/database defaults plus the internal `postgres` hostname. Local `uv` development remains SQLite by default.
- Updated developer/troubleshooting documentation only. PostgreSQL, Compose, containers, volumes and application code were left untouched.

Files changed:
- `compose.yaml`
- `docs/DEVELOPER_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- Docker CLI by verified per-user path -> PASS: Docker 29.7.2.
- Compose plugin by verified per-user path -> PASS: v5.5.1.
- Docker context/named-pipe inspection -> PASS: `desktop-linux` context and both expected pipes exist.
- `docker info` / Engine version request -> BLOCKED: no response within the bounded check.
- Docker backend log inspection -> BLOCKED cause identified: backend reports it is not running.
- `docker compose config`, Compose startup, PostgreSQL smoke test, Docker integration tests -> NOT RUN because the Engine is not healthy.
- Compose PostgreSQL wiring -> static repair applied; live Compose validation remains pending Engine readiness.

Architecture/security notes:
- `compose.yaml` is the sole intended local stack: PostgreSQL 16 on host port 5432 with named volume `postgres_data`, agent service on 8080, and Streamlit on 8501.
- Compose derives `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` from `.env` or safe Compose defaults; no local `.env` value was output.
- When `DATABASE_TYPE=postgres`, `src/memory/postgres.py` creates separate LangGraph `AsyncPostgresSaver` and `AsyncPostgresStore` pools and their library-managed schemas. This cannot yet be verified without a running Engine.

Known limitations:
- Docker Desktop must show its Engine as ready and make `docker info` return before Compose configuration, PostgreSQL container health, checkpoint/Store smoke tests, Docker FastAPI, and Docker Streamlit can be evaluated.
- The current Codex process predates the per-user Docker PATH update; it can invoke Docker by verified absolute path, but a new user terminal should pick up the normal PATH entry.

Learner notes:
- Problem solved: separated Docker CLI installation from Docker Engine readiness, preventing unsafe attempts to alter Compose/database configuration.
- Read these files: `compose.yaml`, `docker/Dockerfile.service`, `src/memory/postgres.py`, `src/memory/__init__.py`, `scripts/smoke_test.sh`.
- Key concept: Docker CLI is only a client; the Docker Engine is the process that can actually create containers and volumes.
- Small exercise: after Docker Desktop reports ready, run `docker info` and then `docker compose config` from the repository root.
- Ignore for now: Docker Desktop log internals, WSL implementation details, and PostgreSQL SQL tables.

Recommended next task:
- After the owner restarts/repairs Docker Desktop until `docker info` succeeds, resume this same Phase 0.5 task: validate Compose, PostgreSQL health, the Postgres checkpointer/Store smoke test, and Docker services. Do not start Phase 1 yet.

### 2026-09-10 — Development Environment Repair & Baseline Verification

Status: DONE, except Docker/PostgreSQL container verification blocked by missing Docker Desktop

What changed:
- Used the existing `uv.lock` with `uv sync --frozen` to create `.venv`; no dependency or lock version changed.
- Created ignored local `.env` from `.env.example` with only `USE_FAKE_MODEL=true`; no real secret was used or logged.
- Fixed the Windows-only Streamlit test-environment failure in `tests/conftest.py`: `mock_env` now retains only `USERPROFILE`, `HOMEDRIVE`, and `HOMEPATH`, which Streamlit requires for `Path.home()`.
- Started and verified FastAPI against `/health`, `/info`, `/openapi.json`, `/invoke`, and `/history` with the fake model and SQLite checkpoint; started and verified Streamlit `/healthz` and base page; then stopped both baseline processes.

Files changed:
- `tests/conftest.py`
- `docs/DEVELOPER_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv --version` -> PASS: 0.12.12
- `python --version` -> PASS: 3.12.4
- `python -m pip --version` -> PASS: pip 24.0
- `uv sync --frozen` -> PASS: project `.venv` created from lock.
- `uv run pytest` -> PASS: 191 passed, 4 skipped, 18 warnings in 65.36s.
- `uv run ruff format --check` -> PASS after formatting the changed fixture.
- `uv run ruff check --output-format concise` -> PASS.
- `uv run pyrefly check` -> PASS: 0 errors; 11 existing suppressions.
- `uv run pymarkdown scan docs/DEVELOPER_GUIDE.md docs/TROUBLESHOOTING.md` -> PASS after synchronizing the touched troubleshooting lists.
- `uv run pymarkdown scan README.md docs/` -> FAIL on pre-existing formatting violations in unrelated README/docs files; no broad formatting rewrite was performed.
- Fake-model API request plus checkpoint history -> PASS.
- Streamlit `/healthz` and base page -> PASS.
- Docker checks -> BLOCKED: Docker command, Desktop executable, and service are absent.

Architecture/security notes:
- The fake-model configuration validates local API/graph/checkpoint wiring only; it does not validate external LLM credentials or provider connectivity.
- `.env` remains ignored by Git and contains no production secret. The shared `AUTH_SECRET` remains blank for the local baseline, so service logs correctly warn that endpoints are unauthenticated.
- Docker/PostgreSQL container checks were not replaced with a different database installation.

Known limitations:
- Docker Desktop must be installed and running before Compose validation, PostgreSQL container health checks, and Docker-marked integration tests can run.
- The suite reports 18 dependency deprecation warnings; these are recorded upstream maintenance items, not baseline failures.

Learner notes:
- Problem solved: the project can now create its own virtual environment, run the full non-Docker test suite, and start both local services without a real model key.
- Read these files: `pyproject.toml`, `.env.example`, `tests/conftest.py`, `src/run_service.py`, `compose.yaml`.
- Key concept: project dependencies belong in the lock-managed `.venv`, while environment-specific secrets/config belong in ignored `.env`.
- Small exercise: in a terminal, run `uv run pytest`, then set `USE_FAKE_MODEL=true` and call `http://127.0.0.1:8080/health` after starting the API.
- Ignore for now: Docker internals, PostgreSQL schemas, real provider credentials, and the upstream deprecation warnings.

Recommended next task:
- Before Phase 1 feature work, install/start Docker Desktop, then validate `docker compose config`, PostgreSQL health, and the Docker-marked integration tests. No TaskPilot domain feature was started here.

### 2026-09-10 — T000–T004: Phase 0 Repository Assessment

Status: DONE (assessment); baseline execution environment BLOCKED as recorded below

What changed:
- Read TaskPilot instructions, Phase 0 prompt, repository tree, source, tests, containers and upstream documentation.
- Mapped FastAPI flow, agent registry/graphs, checkpointer/Store backends, RAG, interrupt, supervisor, tracing, UI, Docker and CI.
- Recorded an evidence-based reuse/gap plan. No production feature, schema or upstream reference file was changed.

Files changed:
- `docs/ARCHITECTURE.md`
- `docs/CODE_READING_ORDER.md`
- `docs/DEVELOPER_GUIDE.md`
- `process/DECISION_LOG.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `python --version` -> PASS: Python 3.12.4
- `python -m pytest --version` -> PASS: pytest 7.4.4
- `USE_FAKE_MODEL=true python -m pytest` -> BLOCKED during collection: `ModuleNotFoundError: No module named 'httpx'`; cache-write warnings arose from this read-only assessment workspace.
- `USE_FAKE_MODEL=true python src/run_service.py` -> BLOCKED: `ModuleNotFoundError: No module named 'uvicorn'`.
- `uv sync --frozen` -> NOT RUN: `uv` absent.
- `docker compose watch` -> NOT RUN: Docker absent.
- `python -m compileall -q src` -> not a source verdict: it could not create `__pycache__` in this read-only workspace.

Architecture/security notes:
- `src/run_service.py` launches `service:app`; `src/service/service.py` owns lifespan and chat/SSE/AG-UI routes.
- `src/agents/agents.py` registers ten upstream/demo graphs; default is `research-assistant` in `src/agents/research_assistant.py`.
- SQLite is the default LangGraph checkpointer with an in-memory long-term Store; PostgreSQL supports `AsyncPostgresSaver` and `AsyncPostgresStore`. There are no application ORM models/migrations/business tables.
- Optional `AUTH_SECRET` is a shared bearer secret, not user/org/RBAC authorization; request `user_id` is caller supplied.
- RAG, interrupt, multi-agent and tracing are prototypes/integrations, not TaskPilot tenant-safe skill, approval, audit or observability implementations.

Known limitations:
- Rerun test, startup and Docker baselines after installing `uv 0.12.5`, syncing dependencies and enabling Docker in a writable clone. This host cannot establish an upstream test pass/fail result.
- No TaskPilot identity, task/run/step, planner/executor/verifier, approval, structured task trace or evaluation suite exists yet.

Learner notes:
- Problem solved: separated actual upstream behavior from TaskPilot plans.
- Read these files: `src/run_service.py`, `src/service/service.py`, `src/agents/agents.py`, `src/agents/research_assistant.py`, `src/memory/__init__.py`.
- Key concept: a LangGraph conversation checkpoint is not a business task lifecycle.
- Small exercise: after setup, call `GET /info`, then trace `POST /invoke` through the five files above and inspect its SQLite checkpoint.
- Ignore for now: supervisor graphs, GitHub MCP, voice, AG-UI internals, Bedrock KB, MongoDB and Langfuse; they are optional upstream examples.

Recommended next task:
- T010–T016 / Phase 1 Foundation: make the `uv` baseline reproducible, audit configuration, add request correlation/redaction, then verify migration boundaries. Do not implement identity or Task domain yet.

## Template

### YYYY-MM-DD — TASK-ID: Title

Status: DONE / PARTIAL / BLOCKED

What changed:
- ...

Files changed:
- ...

Commands/tests run:
- `...` → PASS/FAIL

Architecture/security notes:
- ...

Known limitations:
- ...

Learner notes:
- Problem solved:
- Read these files:
- Key concept:
- Small exercise:
- Ignore for now:

Recommended next task:
- ...
