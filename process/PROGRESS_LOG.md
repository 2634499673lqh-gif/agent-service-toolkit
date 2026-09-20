# Progress Log

> Append one entry per completed task. Do not delete old entries.

### 2026-09-20 — T041: Planner schema

Status: IMPLEMENTED — READY FOR T041 STRONG REVIEW (uncommitted)

- Added the minimal JSON-serializable runtime-only `Plan` and `PlanStep`
  Pydantic schemas.
- Enforced one-to-eight steps, positive positions, canonical `1..N` ordering,
  non-blank instructions, the 500-character instruction limit, and forbidden
  extra fields.
- Added focused schema validation and JSON round-trip tests.

Scope remains limited to T041. No planner node, repair loop, executor,
verifier, recovery, persistence, API, authority fields, or dependency was
added. PostgreSQL is not applicable to this pure schema task.

### 2026-09-20 — T042: Planner node

Status: IMPLEMENTED — READY FOR T042 STRONG REVIEW (uncommitted)

- Added a narrow async, injectable planner model boundary.
- Added one-shot structured-output repair using the T041 `Plan` validator.
- Added stable terminal `planner_output_invalid` behavior after exactly two
  invalid attempts, with validation summaries that omit invalid values.
- Added deterministic call-count, repair-context, authority-field, and input
  boundary tests.

Scope remains limited to T042. No executor, verifier, classifier, retry/replan
runtime, LangGraph graph, persistence, API, external model call, or dependency
was added.

### 2026-09-20 — T043: Executor interface

Status: IMPLEMENTED — READY FOR T043 STRONG REVIEW (uncommitted)

- Added the narrow async `Executor` Protocol for a validated `PlanStep` and
  sanitized task input.
- Added JSON-serializable `ExecutionResult` validation for bounded output,
  normalized failure fields, and mutually exclusive success/failure shapes.
- Added focused interface, invariant, serialization, and fake-implementation
  tests.

Scope remains limited to T043. No deterministic executor, provider, tool/skill
registry, classifier, retry/replan, persistence, API, or external effect was
added. PostgreSQL is not applicable to this interface/schema task.

### 2026-09-20 — T044: Deterministic execution path

Status: IMPLEMENTED — READY FOR T044 STRONG REVIEW (uncommitted)

- Added one side-effect-free `DeterministicExecutor` implementation using the
  committed T043 interface and result contract.
- Added stable bounded text transformation from sanitized task input and
  `PlanStep` data.
- Added explicit per-instance `none`, `fail_once`, and `always_fail` behavior
  for deterministic recovery tests; no global failure state was introduced.
- Added repeatability, bounds, normalized failure, and Protocol compatibility
  tests.

Scope remains limited to T044. No verifier, classifier, retry/replan runtime,
LangGraph graph, persistence, API, provider, tool registry, or external effect
was added.

Files changed:

- `src/runtime/executor.py`
- `src/runtime/__init__.py`
- `tests/runtime/test_deterministic_executor.py`
- `process/PROGRESS_LOG.md`

Commands/tests run:

- Focused T044 tests: `5 passed`.
- T041/T042/T043 regression: `39 passed`.
- Runtime/schema regression: `54 passed`.
- Full pytest: `427 passed, 81 skipped`.
- Ruff, Ruff format, Pyrefly, `uv lock --check`, import smoke, and
  `git diff --check`: PASS.

Result: T044 is implemented and ready for independent Strong Review.

Known limitations: the executor intentionally formats bounded runtime data;
semantic task execution, verification, recovery, persistence, and external
effects remain deferred to later tasks.

Learner notes: the production executor is deliberately pure and instance-local;
`fail_once` state is explicit and is not a retry or failure-classification
framework.

Suggested next task: T045 — Verifier schema. Do not implement it as part of T044.

### 2026-09-20 — Phase 4 Planning Acceptance Status

Status: APPROVED / COMPLETE

Independent Planning Strong Review approved ADR-006 and T040. The Phase 4
planning contract is now accepted; T041 is the first executable implementation
task. No runtime implementation is marked started or complete.

### 2026-09-20 — Phase 4 blocker-only planning correction

Status: CORRECTED — READY FOR FOCUSED PHASE 4 PLANNING RE-REVIEW

Resolved the four independent Strong Review blockers without changing the
already-approved Phase 4 boundaries:

- Reconciled all live ROADMAP task IDs through Phase 11 to canonical
  TASK_BACKLOG IDs and documented the complete old→canonical mapping,
  including split mappings such as old T058 → T070/T072.
- Expanded ADR-006/T040 with exact JSON AgentState fields, types,
  initialization/mutation rules, Plan/PlanStep bounds, executor and verifier
  result contracts, classifier literals, routing, and finite-budget proof.
- Froze the trusted `TaskRuntimeService.execute_run(...)` boundary, PENDING /
  RUNNING / terminal sequence, stale-state revalidation, and lifecycle-race
  behavior.
- Froze concurrent/repeated resume invariants: duplicate deterministic graph
  work is allowed, exactly-once node execution is not claimed, and T035 alone
  decides one consistent terminal business state.
- Updated T041–T051 acceptance contracts and T051 audit evidence requirements.

No production source, migration, dependency, or lockfile changed. Runtime and
PostgreSQL tests remain intentionally deferred because this correction is
planning-only.

Suggested next task: focused independent Planning Strong Re-review.

### 2026-09-20 — Phase 4 Planning Fix — Agent Runtime Contract

Status: PLANNING ARTIFACTS CREATED — READY FOR INDEPENDENT PLANNING STRONG REVIEW

What changed:
- Added proposed ADR-006 and implementation-ready T040–T051 Task Cards.
- Canonicalized Phase 4 as T040–T051, including read-only T051 Final Audit,
  with a linear dependency DAG and no duplicate IDs.
- Resolved T033 explicitly: PlanStep is runtime-only; persistent TaskStep ORM,
  migration, repository, API, and lifecycle remain deferred.
- Froze internal runtime entry, unchanged Phase 3 enums and T035 lifecycle
  ownership, minimal serializable AgentState, one retry and one replan budgets,
  tenant-trusted checkpoint identity/resume, independent persistence, and
  side-effect-free scope.
- Reconciled backlog, roadmap, task index, architecture/API/README docs.

Files changed: TASK_BACKLOG.md, ROADMAP.md, README.md,
docs/ARCHITECTURE.md, docs/API_CONVENTIONS.md, process/DECISION_LOG.md,
process/tasks/INDEX.md, process/tasks/T033.md, process/tasks/T040.md through
T051.md, and this log.

Historical pre-approval limitation (superseded): ADR-006 and T040 required
Planning Strong Review before Phase 4 production work.

Learner notes:
- Problem solved: Phase 4 now has one implementation authority instead of
  conflicting task numbers and an unfrozen runtime boundary.
- Read ADR-006, T040, T050, `src/service/task_lifecycle.py`, and
  `docs/ARCHITECTURE.md`.
- Key concept: durable TaskRun lifecycle and runtime graph/checkpoint state are
  separate boundaries; checkpoint identity is correlation, never authorization.
- Exercise: trace fail-once through T035 `succeed_run`, then compare always-fail
  exhaustion through T035 `fail_run`.
- Do not worry yet about TaskStep tables, workers, approvals, providers, or
  HTTP idempotency.

Historical suggested next task (completed): independent Planning Strong Review
of ADR-006/T040.

### 2026-09-11 — T014: Structured Logging and Secret Redaction

Status: DONE — ready for Strong Review

Baseline:
- Branch: `phase-1-foundation`
- HEAD before changes: `3101058 fix(settings): validate conditional provider and backend configuration`
- Working tree before changes: clean.

What changed:
- Added a small stdlib-only structured logging helper under `src/service/logging.py`.
- Existing root handlers now emit JSON records with UTC timestamp, level, logger, message, request ID, and safe request metadata.
- Reused T013's generated request ID by binding it to an async-safe `contextvars` context in the existing middleware; the context is reset in `finally` to prevent request crossover.
- Added `request.started`, `request.completed`, and `request.failed` lifecycle events without changing HTTP/SSE payloads or exception semantics.
- Added recursive key-based redaction for nested mappings/lists, common Authorization/Bearer and key-value forms, credential-bearing URLs, registered Settings secrets, and exception text.
- Added real service regression tests for structured request correlation, request-context cleanup, nested redaction, bearer/DSN redaction, configured secrets, and exception messages.

Files changed:
- `src/service/logging.py`
- `src/service/service.py`
- `tests/service/test_logging.py`
- `docs/DEVELOPER_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service -q` → PASS (73 passed, 19 warnings) with repository-local writable `TMP`/`TEMP`.
- `uv run pytest` → PASS (206 passed, 4 skipped, 19 warnings) with repository-local writable `TMP`/`TEMP`.
- `uv run ruff check --output-format concise` → PASS.
- `uv run pyrefly check` → PASS (0 errors; 11 known suppressions).
- `uv run pymarkdown scan docs/DEVELOPER_GUIDE.md docs/TROUBLESHOOTING.md` → PASS.
- `git diff --check` → PASS; only normal Git LF/CRLF advisories for Markdown files.

Architecture/security notes:
- Existing Python stdlib logging remains the logging system; no new dependency or logging framework was added.
- Request IDs are correlation metadata only and remain ephemeral; no tracing, persistence, TaskPilot IDs, metrics, or audit tables were introduced.
- Settings `SecretStr` values are registered without logging their contents. Ordinary fields such as model, host, port, event, and request ID remain available for diagnosis.

Known limitations:
- Records emitted outside an HTTP request intentionally have `request_id: null`.
- A custom handler installed after service configuration must be passed through `configure_logging()` to receive the JSON formatter; LogRecord message/argument redaction still protects standard handlers.
- Redaction is deliberately bounded to registered Settings secrets, sensitive field names, credential-bearing URLs, and common authorization/key-value forms; arbitrary unregistered opaque values cannot be identified reliably without a broader secret-management contract.
- The known T013 limitation remains: an exception escaping to Starlette's outer `ServerErrorMiddleware` can produce a final 500 without `X-Request-ID`; T014 records this but does not expand scope to change it.
- This is application logging hardening, not distributed tracing, LangSmith/Langfuse redesign, or persisted observability.

Learner notes:
- Problem solved: service logs can be parsed by machines, correlated to the server-generated request ID, and inspected without exposing common credentials.
- Read these files: `src/service/logging.py`, `src/service/service.py`, `tests/service/test_logging.py`, `src/service/utils.py`, `docs/TROUBLESHOOTING.md`.
- Key concepts: `contextvars` provide request-local async state; LogRecord formatting is separate from log event creation; redaction must handle structured values and rendered exception text.
- Small exercise: add a temporary logger call with a nested `{"api_key": "demo", "host": "localhost"}` payload, run the logging tests, and verify only the key is masked.
- Ignore for now: distributed tracing, OpenTelemetry, TaskRun/AgentRun persistence, metrics, dashboards, and audit storage.

Recommended next task:
- T015 — Migration baseline verification. Do not execute it as part of T014.

### 2026-09-11 — T013: Request Correlation ID

Status: DONE — ready for GPT-6 Astra review

What changed:
- Added a single FastAPI HTTP middleware that generates a fresh UUID4 per request, stores it in `request.state.request_id`, and echoes the same value in the `X-Request-ID` response header.
- Deliberately ignores client-supplied `X-Request-ID` values so correlation IDs cannot be spoofed; no structured logging, redaction, distributed tracing, Task IDs, or trace records were added.
- Added focused regression tests for generation, request-state propagation, response consistency, fresh IDs, and existing `/health` behavior.
- Documented the request lifecycle and the T014 boundary in `docs/ARCHITECTURE.md`.

Files changed:
- `src/service/service.py`
- `src/service/utils.py`
- `tests/service/test_service.py`
- `docs/ARCHITECTURE.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_service.py -q` → PASS (16 passed, 6 existing warnings).
- `uv run pytest` → first run: 189 passed, 4 skipped, 14 environment fixture errors because the host temp root was not writable; rerun with repository-local `TEMP`/`TMP` → PASS (203 passed, 4 skipped, 19 existing warnings).
- `uv run ruff format --check src/service/service.py src/service/utils.py tests/service/test_service.py` → PASS.
- `uv run ruff check src/service/service.py src/service/utils.py tests/service/test_service.py` → PASS.
- `uv run pyrefly check` → PASS (0 errors; 11 known suppressions).
- `uv run pymarkdown scan docs/ARCHITECTURE.md` → PASS.
- `git diff --check` → PASS.

Architecture/security notes:
- Existing endpoint payloads, LangGraph persistence, Settings, fake-model behavior, SQLite/PostgreSQL baseline, and startup lifecycle remain unchanged.
- The only additive transport surface is the `X-Request-ID` response header; request IDs are ephemeral middleware metadata and are not persisted.
- No client-provided correlation value is trusted. Structured logging and secret redaction remain T014 scope.

Known limitations:
- The default host pytest temp root is permission-restricted; full regression requires a writable `TEMP`/`TMP` directory in this environment.
- This task does not propagate IDs into structured logs or TaskPilot trace entities; those are intentionally deferred.

Learner notes:
- Problem solved: every HTTP request now has one stable identifier available throughout its FastAPI lifecycle and visible to the caller.
- Read these files: `src/service/service.py`, `src/service/utils.py`, `tests/service/test_service.py`, `docs/ARCHITECTURE.md`.
- Key concept: middleware is the narrow transport boundary for request-scoped metadata; it should not become a task or distributed-tracing store.
- Small exercise: call `/health` twice with and without `X-Request-ID` and compare the UUID response headers.
- Ignore for now: log formatting/redaction, TaskRun/AgentRun IDs, persistence, and metrics.

Recommended next task:
- T014 — Structured logging and redaction. Do not begin it as part of T013.

### 2026-09-11 — T012: Settings Validation

Status: DONE

What changed:
- Added instance-safe provider catalogue construction and fail-fast cross-field validation to the existing Pydantic `Settings` model.
- Validated only the selected persistence backend: SQLite remains the default local fallback; PostgreSQL and MongoDB validate their required connection fields, with optional MongoDB authentication kept available.
- Added opt-in validation for partial OpenAI-compatible/Azure/Ollama configuration and enabled tracing credentials, while preserving `USE_FAKE_MODEL=true` and local Ollama fallback behavior.
- Added field bounds for server/database ports and PostgreSQL pool sizes, plus regression tests for valid fallbacks and invalid configurations.

Files changed:
- `src/core/settings.py`
- `tests/core/test_settings.py`
- `docs/DEVELOPER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/core/test_settings.py -q` → PASS (32 passed; one host pytest-cache warning).
- `uv run ruff format src/core/settings.py tests/core/test_settings.py` → PASS.
- `uv run ruff check src/core/settings.py tests/core/test_settings.py` → PASS.
- `uv run pytest` → PASS (199 passed, 4 skipped, 19 warnings) after setting `TMP`/`TEMP` to a repository-local writable directory; the default host temp root is not writable in this environment.
- `git diff --check` → PASS (only Git's normal LF/CRLF advisory for the two Markdown files).

Known limitations:
- Postgres/Mongo validation checks configuration shape only; connectivity and LangGraph `setup()` remain runtime concerns owned by existing adapters.
- No provider framework, TaskPilot business settings, migration, checkpoint ownership, or public API was changed.

Learner notes:
- Problem solved: invalid selected-backend or partially opted-in settings now fail at startup with setting names, without turning supported local fallbacks into mandatory configuration.
- Read these files: `src/core/settings.py`, `tests/core/test_settings.py`, `src/memory/postgres.py`, `src/memory/mongodb.py`, `docs/DEVELOPER_GUIDE.md`.
- Key concept: configuration validation should be conditional on an enabled feature; optional integrations must not break the SQLite/fake-model development path.
- Small exercise: instantiate `Settings(USE_FAKE_MODEL=True, DATABASE_TYPE="postgres", _env_file=None)` and inspect the missing-field error, then add only the five Postgres fields and compare the result.
- Ignore for now: TaskPilot identity/task settings, migrations, and provider redesign.

Recommended next task:
- T013 — Request correlation ID. Do not start T014–T016 in this task.

### 2026-09-11 — T011: Environment Template Audit

Status: DONE

What changed:
- Audited the Pydantic `Settings` fields and the existing direct environment reads, then made `.env.example` a complete Phase 1 runtime inventory.
- Added safe defaults/placeholders for server, tracing, persistence, provider, integration, and client settings; corrected the stale LangSmith names to the implemented `LANGCHAIN_*` names.
- Kept every credential, password, bearer token, and API key value empty; documented that `.env` is local-only and must remain ignored.
- Added a Developer Guide section covering template scope, secret handling, and Git checks.

Files changed:
- `.env.example`
- `docs/DEVELOPER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/core/test_settings.py -q` → initial cache-path error on the host; rerun unchanged with `UV_CACHE_DIR=.uv-cache-t011` → PASS (24 passed, 1 warning).
- `git check-ignore -v .env` → PASS (`.gitignore:142:.env`).
- `git status --short --ignored .env` → PASS (local `.env` is ignored and untracked).
- Secret-safety scan of `.env.example` → PASS: no non-placeholder credential values or private paths found.
- `git diff --check` → PASS.

Architecture/security notes:
- No source code, settings behavior, dependencies, lockfile, persistence architecture, Docker/PostgreSQL wiring, public API, or Phase 2+ domain was changed.
- `.env` was not read or copied; only Git ignore metadata was checked.

Known limitations:
- This task audits the template only; provider-specific credential validation remains a future settings task (T012).
- The full suite is deferred as the task card requires only the focused settings command.

Learner notes:
- Problem solved: developers now have one safe, source-aligned environment template without exposing secrets.
- Read these files: `.env.example`, `src/core/settings.py`, `docs/DEVELOPER_GUIDE.md`.
- Key concept: an environment template documents configuration names and safe defaults, while real secrets stay in an ignored runtime file.
- Small exercise: copy `.env.example` to `.env`, set only `USE_FAKE_MODEL=true`, and run the focused settings tests.
- Ignore for now: TaskPilot identity/task domains and provider-specific production hardening.

Recommended next task:
- T012 — Settings validation. Do not begin it as part of T011.

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

### 2026-09-13 — T015: Migration architecture verification

Status: DONE

- Confirmed separate LangGraph persistence and future TaskPilot business ownership.
- Phase 1 adds no SQLAlchemy, Alembic, ORM, migration directory, placeholder migration, or business table.
- SQLite is local checkpoint; PostgreSQL currently contains LangGraph checkpoint/Store schemas only.
- Phase 2 must decide framework, namespace, shared database, revision ownership, ordering, production migration, downgrade policy, and test database strategy.

Files changed: process/DECISION_LOG.md, docs/ARCHITECTURE.md, docs/DEVELOPER_GUIDE.md, process/PROGRESS_LOG.md.

Verification: documentation diff checks; PostgreSQL smoke not run because runtime/database behavior is unchanged.

Learner Notes: LangGraph persistence is not TaskPilot business truth. Read src/memory/postgres.py and ADR-002.

### 2026-09-13 — T016: Verified developer commands documentation

Status: DONE — ready for Phase 1 Final Audit

Baseline:
- Repository root: `D:\github\agent-service\agent-service-toolkit`
- Branch: `phase-1-foundation`
- HEAD before changes: `47eb5c8 docs: document LangGraph and TaskPilot persistence ownership`
- Working tree before changes: clean; no staged changes.

What changed:
- Synchronized the Developer Guide with the current `pyproject.toml`, Compose
  services, local entrypoints, CI commands, and the safe PostgreSQL smoke-test
  path.
- Documented the Compose lifecycle commands (`config`, `up -d`, `ps`, `logs`, and
  `stop`) and separated static configuration validation from Docker Engine
  readiness.
- Added the verified Windows repository-local `TEMP`/`TMP` and `UV_CACHE_DIR`
  workaround without changing system settings or application code.
- Recorded the existing `scripts/smoke_test.sh` `down -v` cleanup hazard and
  clarified that current runtime examples are not TaskPilot Phase 2+ domains.
- Added a current verification snapshot and distinguished pre-existing full
  Markdown-lint debt from checks on the touched files.

Files changed:
- `docs/DEVELOPER_GUIDE.md`
- `docs/TROUBLESHOOTING.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv sync --frozen` → PASS; checked 248 packages and did not change the lock file.
- `uv run pytest` → PASS: 206 passed, 4 skipped, 18 warnings.
- `uv run ruff format --check` → PASS.
- `uv run ruff check` → PASS.
- `uv run pyrefly check` → PASS: 0 errors, 11 known suppressions.
- `uv run pymarkdown scan README.md docs/` → FAIL only on pre-existing MD022/MD032 violations in untouched documentation.
- `uv run pymarkdown scan docs/DEVELOPER_GUIDE.md docs/TROUBLESHOOTING.md` → PASS after the final documentation change.
- `uv run python src/run_service.py` → PASS: `/health` and `/info` returned HTTP 200; the test process was stopped.
- `uv run streamlit run src/streamlit_app.py` → PASS: `/healthz` and `/` returned HTTP 200; the test process was stopped.
- `docker --version`, `docker compose version` → PASS: Docker 29.7.2, Compose v5.5.1.
- `docker compose config` → PASS.
- `docker compose up -d`, `docker compose ps`, `docker compose logs --tail 20 agent_service streamlit_app postgres`, `docker compose stop` → BLOCKED: Docker Desktop Linux Engine named pipe `dockerDesktopLinuxEngine` was unavailable in the current shell.
- Repository-local `TEMP`/`TMP` plus `UV_CACHE_DIR=.uv-cache`: `uv sync --frozen` and `uv run pytest tests/core/test_settings.py -q` → PASS: 33 passed.

Architecture/security notes:
- This task changed documentation only. No source code, tests, dependencies,
  lockfile, Compose architecture, database schema, public API, or runtime
  behavior changed.
- Local `uv` development remains SQLite/fake-model friendly; Compose explicitly
  targets PostgreSQL as documented by the existing configuration.
- No credentials or secret values were added to documentation or command output.
- No Git add, commit, reset, rebase, merge, push, switch, or checkout operation
  was performed.

Known limitations:
- Docker lifecycle commands remain pending until Docker Desktop's Linux Engine
  is ready. The earlier 2026-09-10 Phase 0.5 entry records the prior successful
  Compose/PostgreSQL verification; this T016 run does not overwrite that history.
- Whole-tree Markdown lint still has pre-existing MD022/MD032 violations in
  untouched docs; T016 does not broaden into a formatting cleanup.
- The repository remains a Phase 1 foundation. Users, organizations, RBAC,
  Task/TaskRun/TaskStep, planner/executor/verifier, approvals, and persisted
  TaskPilot observability are still planned work, not current runtime features.

Learner notes:
- Problem solved: developers now have one source of truth for reproducible local
  commands, Docker lifecycle commands, safe smoke-test boundaries, and Windows
  permission recovery.
- Read these files: `pyproject.toml`, `docs/DEVELOPER_GUIDE.md`,
  `docs/TROUBLESHOOTING.md`, `compose.yaml`, and `.github/workflows/test.yml`.
- Key concept: a command can be syntactically valid and still require an external
  runtime; `docker compose config` validates YAML/configuration, while `up` needs
  a healthy Docker Engine.
- Small exercise: run `uv run pytest`, `uv run ruff check`, then `docker info` and
  compare the local result with `docker compose config`.
- Ignore for now: Docker internals, the smoke wrapper's implementation details,
  and all unimplemented TaskPilot domain phases.

Recommended next task:
- Phase 1 Final Audit only. Do not begin Phase 2 as part of T016.

### 2026-09-13 — Phase 2 planning: T020–T027 identity/RBAC/tenant isolation task cards

Status: DONE — planning only; no Phase 2 production code changed

What changed:
- Audited the real FastAPI service, optional shared bearer auth, settings, schemas, PostgreSQL/SQLite LangGraph persistence, lifespan, AG-UI, thread listing, client/UI identity fields, tests, dependencies, Compose and environment template.
- Added a Phase 2 task-card index and implementation-ready T020–T027 cards.
- Added proposed ADR-003 defining the T020 architecture gate and the server-derived identity/tenant security invariant.
- Recorded the roadmap/backlog numbering drift: ROADMAP lists T020–T025 while TASK_BACKLOG is authoritative for T020–T027.

Files changed:
- process/tasks/INDEX.md
- process/tasks/T020.md through process/tasks/T027.md
- process/DECISION_LOG.md
- process/PROGRESS_LOG.md

Commands/tests run:
- `git rev-parse --show-toplevel`, `git branch --show-current`, `git status`, `git log -3 --oneline` → PASS: expected repository, `phase-2-identity-rbac`, clean tree before edits.
- Read all required planning/security/persistence documents and the real source/tests listed in T020 → PASS.
- Production tests/lint → NOT RUN: this task is documentation/planning only and adds no runtime behavior.

Architecture/security notes:
- Current AUTH_SECRET is an optional shared bearer secret, not user identity, organization identity, RBAC, or tenant authorization.
- `UserInput.user_id`, `/threads` query `user_id`, Streamlit cookie/query IDs, and AG-UI configurable identity are caller-asserted upstream compatibility values and must not authorize TaskPilot business resources.
- PostgreSQL saver/store schemas are LangGraph-owned; TaskPilot business migrations must be independently owned and must not include those tables.

Known limitations:
- The pasted task input ended at “重点决定：cross-tenan”; any requirements after that truncation could not be audited and should be merged into T020 if supplied.
- T020 decisions remain proposed until strong architecture/security review; implementation cards are intentionally blocked on that review.

Learner notes:
- Problem solved: Phase 2 implementation is now decomposed around explicit identity, migration, transaction and tenant-security decisions instead of guessing in CRUD tasks.
- Read these files: `process/tasks/T020.md`, `process/DECISION_LOG.md`, `src/service/service.py`, `src/schema/schema.py`, `src/memory/postgres.py`.
- Key concept: authentication proves who a caller is; authorization derives what that identity may access inside a tenant.
- Small exercise: trace a `/threads` request and list every client-controlled identity value, then explain why each cannot authorize a business query.
- Ignore for now: enterprise SSO, custom policy engines, destructive actions, and production code until T020 is accepted.

Recommended next task:
- Supply any missing prompt text after the attachment truncation, then run T020 as a strong architecture/security review and approve its ADR before starting T021.

### 2026-09-13 — Phase 2 Planning Completion Audit after prompt truncation

Status: DONE — planning documents only; T020 not executed

Audit result:
- Rechecked requirements 1–19. Added explicit decision-freeze language, Membership split contingency, migration verification gates, exact model/review labels, full-regression gates, and expanded negative-test matrix.
- The truncation did not leave an unplanned security domain: all requirements in the follow-up audit are now represented in T020–T027 and the index. The original attachment still ends at `cross-tenan`; no unseen text was inferred.

Files changed: process/tasks/INDEX.md; process/tasks/T020.md–T027.md.

Validation: focused `uv run pymarkdown scan` PASS; `git diff --check` PASS; no `src/`, `tests/`, migration, dependency, Compose, or env files changed.

Next: strong review of T020 only when explicitly authorized.


### 2026-09-14 — T020: Identity / tenancy / authentication / business persistence architecture

Status: ARCHITECTURE COMPLETE — READY FOR STRONG REVIEW

What changed:
- Accepted ADR-004 freezing User + Organization + Membership, enum roles, opaque sessions, Argon2id password handling, PostgreSQL-only TaskPilot business persistence, SQLAlchemy async sessions, Alembic ownership, schema separation, bootstrap order, transaction boundaries, UUID4 IDs, lifecycle, email, bootstrap, error semantics, and the legacy `AUTH_SECRET` boundary.
- Added explicit Membership split as T022A and synchronized Phase 2 task dependencies/model assignments.
- Updated architecture, database, security, API, index, and progress documentation without modifying production code, tests, dependencies, migrations, or AUTH_SECRET behavior.

Files changed:
- `process/DECISION_LOG.md`
- `process/PROGRESS_LOG.md`
- `process/tasks/INDEX.md`
- `process/tasks/T021.md` through `process/tasks/T027.md`
- `process/tasks/T022A.md`
- `docs/ARCHITECTURE.md`
- `docs/DATABASE_DESIGN.md`
- `docs/SECURITY_HITL.md`
- `docs/API_CONVENTIONS.md`

Validation:
- Baseline branch/worktree verified before edits.
- Markdown lint, `git diff --check`, and final status/stat checks are run after documentation edits. No runtime suite is required by T020.

Known limitations and deferred decisions:
- No production implementation, migration, dependency, or AUTH_SECRET behavior change is included. Strong Review must approve ADR-004 before T021. Enterprise SSO, refresh-token families, custom permissions, email verification/change, hard delete, RLS, key rotation, and L3 execution remain deferred.

Learner notes:
- The key concept is separating compatibility transport identity from server-derived business authorization.
- Read ADR-004, `docs/DATABASE_DESIGN.md`, `docs/SECURITY_HITL.md`, `process/tasks/T021.md`, and `process/tasks/T022A.md`.
- Exercise: trace a forged `organization_id` from request input and explain why it cannot affect a repository predicate.
- Do not worry yet about enterprise SSO or custom policy engines.

Suggested next task: Strong Review of T020, then T021 only after approval.


### 2026-09-14 — T020 Strong Review fixes

Status: REVIEW FIXES COMPLETE — READY FOR RE-REVIEW

Updated ADR-004 and the affected task/security/database/API cards with the controlled bootstrap contract (hidden two-step prompt, no plaintext CLI password, idempotent no-op, fail-closed conflicts, atomic transaction), request-time organization activity revalidation, canonical `normalized_email` storage, and precise production downgrade policy. No source, test, migration, dependency, or AUTH_SECRET behavior changes were made.

Validation: focused Markdown lint for T022/T023/T024/T026 passed; `git diff --check` passed; branch and clean staging constraints verified.

### 2026-09-14 — T021: SQLAlchemy foundation, Organization model and Alembic migration

Status: IMPLEMENTATION COMPLETE — READY FOR STRONG REVIEW (uncommitted)

What changed:
- Added a PostgreSQL-only TaskPilot business persistence boundary under `src/persistence/` with schema-scoped SQLAlchemy metadata, async psycopg engine/session factory, lifecycle rollback, Organization ORM model, and a no-commit repository.
- Added `alembic.ini`, async `migrations/env.py`, ownership-filtered autogenerate configuration, and the initial `t021_organization` revision. The revision creates `taskpilot`, `taskpilot.alembic_version`, and `taskpilot.organizations` only.
- Added explicit `TASKPILOT_DATABASE_URL` settings validation and a safe `.env.example` placeholder. Existing `DATABASE_TYPE` and LangGraph SQLite/PostgreSQL/Mongo behavior remain unchanged.
- Added unit and real-PostgreSQL integration verification code for migration isolation, revision metadata, transaction behavior, session independence, timestamps, constraints, and both independent TaskPilot/LangGraph setup orderings. Live execution requires the disposable PostgreSQL environment described below.

Files changed:
- `src/persistence/__init__.py`, `base.py`, `engine.py`, `models.py`, `repositories.py`
- `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/20260914_01_organization.py`, `alembic.ini`
- `src/core/settings.py`, `.env.example`, `tests/conftest.py`, `tests/persistence/test_foundation.py`, `tests/persistence/test_postgres_integration.py`
- `pyproject.toml`, `uv.lock`
- `docs/ARCHITECTURE.md`, `docs/DATABASE_DESIGN.md`, `docs/DEVELOPER_GUIDE.md`, `docs/TROUBLESHOOTING.md`

Validation:
- `uv run pytest tests/persistence -q` → 5 passed, 3 skipped because no disposable `TASKPILOT_TEST_DATABASE_URL`/Docker PostgreSQL was available (HOST_ENVIRONMENT).
- `uv run pytest` → 211 passed, 7 skipped, 18 existing dependency deprecation warnings.
- `uv run ruff format --check`, `uv run ruff check --output-format concise`, `uv run pyrefly check`, `uv lock --check`, focused `uv run pymarkdown scan` and `git diff --check` passed.
- `uv run alembic upgrade head --sql` generated schema/version/Organization DDL successfully; live PostgreSQL migration/coexistence verification remains HOST_ENVIRONMENT-blocked.

Learner notes:
- Problem solved: TaskPilot now has an independently migrated business schema without taking ownership of LangGraph persistence tables.
- Read `src/persistence/engine.py`, `src/persistence/models.py`, `migrations/env.py`, `migrations/versions/20260914_01_organization.py`, and `docs/DATABASE_DESIGN.md`.
- Key concept: repository writes are flushed inside a service-owned transaction; Alembic ownership is constrained by schema and metadata filters.
- Exercise: configure a disposable PostgreSQL database, run `uv run alembic upgrade head`, inspect both `taskpilot.alembic_version` and public LangGraph tables, then downgrade/upgrade once.
- Do not worry yet about User, Membership, authentication, authorization, or task tables; those belong to T022+.

Suggested next task: Strong Review of T021 only.

### 2026-09-15 — T021 Strong Review fixes

Status: CODE FIXES COMPLETE — LIVE POSTGRES VERIFICATION REQUIRED

- Added Alembic `include_name` pre-reflection filtering and retained defensive `include_object` filtering; added focused ownership tests.
- Removed the unscoped `OrganizationRepository.list_active()` API.
- Reworked PostgreSQL integration tests to create one uniquely named disposable database per scenario, cover LangGraph→TaskPilot and TaskPilot→LangGraph ordering, real Saver/Store setup and roundtrip, fresh-state assertions, and downgrade/re-upgrade preservation. The fixture refuses non-test databases and shared-database fallback.
- Broadened the Organization name constraint to reject spaces, tabs, and newlines when no non-whitespace character exists; sanitized chained DSN parsing errors and added regression coverage.
- Corrected documentation to describe the Phase-1 no-ORM statement as historical and to distinguish implemented verification code from unavailable live PostgreSQL execution.

Validation: focused persistence tests 7 passed/3 HOST_ENVIRONMENT skips; full pytest 213 passed/7 skipped; Ruff, Pyrefly, uv lock, focused Markdown, offline Alembic SQL, and `git diff --check` passed. Docker Linux Engine and `TASKPILOT_TEST_DATABASE_URL` remain unavailable on this host.

### 2026-09-15 — T021 focused live-verification defect fixes

Status: CODE DEFECTS FIXED — READY FOR ENVIRONMENT RE-VERIFICATION

What changed:
- Added a persistence-test-only `pytest_asyncio_loop_factories` hook. On Windows
  it selects `asyncio.SelectorEventLoop` for psycopg compatibility; non-Windows
  tests use the current asyncio policy's native `new_event_loop` factory.
- Added a Windows-only regression assertion for the running persistence test loop.
- Replaced both pre-migration bare `to_regclass(...)` expressions with
  parameterized `SELECT to_regclass(:qualified_name)` statements.
- Added two post-migration parameterized relation assertions for
  `taskpilot.alembic_version` and `taskpilot.organizations`.

Files changed by this focused fix:
- `tests/persistence/conftest.py`
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `process/PROGRESS_LOG.md`

Validation:
- `uv run pytest tests/persistence -q` → PASS: 8 passed, 3 skipped; no
  pytest-asyncio deprecation warning after using the installed 1.4.0 hook.
- `uv run pytest` → PASS: 214 passed, 7 skipped, 18 existing dependency
  deprecation warnings.
- `uv run ruff format --check tests/persistence` → PASS.
- `uv run ruff check --output-format concise tests/persistence` → PASS.
- `uv run pyrefly check` → PASS: 0 errors.
- `uv lock --check` → PASS.
- `git diff --check` → PASS.
- `TASKPILOT_TEST_DATABASE_URL` was absent in this implementation session, so the
  live PostgreSQL scenario tests were not executed here and no credential was
  invented. Dedicated Environment & Verification must rerun them.

Scope/security notes:
- No production event-loop policy, SQLAlchemy engine, migration ownership,
  LangGraph setup, credentials, or unrelated behavior was changed.
- Scenario A/B, disposable database guards, downgrade/re-upgrade, LangGraph
  roundtrip, transaction/session coverage, and cleanup code were retained.
- No T022, T022A, T023, or later work was started. No Git add, commit, or push
  was performed.

Learner notes:
- Problem solved: Windows persistence tests now create psycopg-compatible loops,
  and PostgreSQL relation checks are valid parameterized SQL.
- Read `tests/persistence/conftest.py`,
  `tests/persistence/test_postgres_integration.py`,
  `src/persistence/engine.py`, `migrations/env.py`, and ADR-004.
- Key concept: test event-loop policy belongs in the test harness; SQL relation
  inspection still needs a complete SQL statement and bound parameters.
- Exercise: configure a disposable PostgreSQL test URL, run the focused suite,
  then inspect the temporary database before and after `alembic upgrade head`.
- Do not worry yet about User, Membership, authentication, or authorization;
  those remain T022+.

Suggested next step: dedicated Environment & Verification rerun of T021 live
PostgreSQL scenarios, followed by the separately authorized T021 review gate.

### 2026-09-15 — T021 focused Alembic Windows loop fix

Status: CODE FIX COMPLETE — READY FOR LIVE ENVIRONMENT RE-VERIFICATION

What changed:
- Updated `migrations/env.py` so only Windows online Alembic execution calls
  `asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)`.
- Linux/macOS retain the normal `asyncio.run(...)` path.
- Offline SQL generation, metadata ownership filters, schema configuration,
  database URL handling, migration revisions, and application runtime behavior
  were left unchanged.

Files changed by this focused fix:
- `migrations/env.py`
- `process/PROGRESS_LOG.md`

Validation:
- `uv run pytest tests/persistence -q` → PASS: 8 passed, 3 skipped because
  `TASKPILOT_TEST_DATABASE_URL` was absent in this window.
- `uv run ruff format --check` → PASS.
- `uv run ruff check --output-format concise` → PASS.
- `uv run pyrefly check` → PASS: 0 errors.
- `uv lock --check` → PASS.
- `git diff --check` → PASS.
- `uv run alembic upgrade head --sql` → BLOCKED before migration execution:
  `TASKPILOT_DATABASE_URL` was absent, so no URL or credential was invented.
- Live PostgreSQL verification was not executed in this implementation window.

Scope notes:
- The existing persistence pytest Selector-loop configuration remains intact;
  this fix addresses the separate loop created by Alembic's `asyncio.run`.
- No `src/` production runtime, psycopg, pytest behavior, migration revision,
  LangGraph setup, or T022+ work was changed.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: Windows Alembic async migrations no longer create the
  psycopg-incompatible Proactor event loop.
- Read `migrations/env.py`, `tests/persistence/conftest.py`,
  `tests/persistence/test_postgres_integration.py`, and `src/persistence/engine.py`.
- Key concept: the pytest loop and the nested migration loop are separate loop
  creation sites and must be fixed at their own boundaries.
- Exercise: with a disposable PostgreSQL URL configured, run the offline SQL
  command and then the three persistence integration tests on Windows.
- Do not worry yet about T022+ identity models or authentication.

Suggested next step: dedicated Environment & Verification rerun of the complete
T021 PostgreSQL scenarios on Windows, followed by the authorized review gate.

### 2026-09-15 — T021 Alembic logging side-effect fix

Status: CODE FIX COMPLETE — READY FOR FINAL LIVE VERIFICATION

What changed:
- Changed only Alembic's guarded `fileConfig` call in `migrations/env.py` to
  pass `disable_existing_loggers=False`, preserving service and agent loggers
  during migration setup.

Validation:
- `uv run pytest tests/service/test_logging.py tests/service/test_service_lifespan.py -q` → PASS: 4 passed.
- `uv run pytest tests/persistence -q` → PASS: 8 passed, 3 skipped because the live PostgreSQL environment was unavailable in this window.
- `uv run ruff format --check` → PASS.
- `uv run ruff check --output-format concise` → PASS.
- `uv run pyrefly check` → PASS: 0 errors.
- `git diff --check` → PASS.

Scope:
- No application logging code, tests/service files, persistence architecture,
  event-loop configuration, migration metadata, revision, or T022+ work changed.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: Alembic no longer disables existing application loggers while
  loading its logging configuration.
- Read `migrations/env.py`, `alembic.ini`, `src/service/logging.py`, and
  `tests/service/test_logging.py`.
- Key concept: `fileConfig` can alter global logger state unless existing loggers
  are explicitly preserved.
- Exercise: compare logger `.disabled` values before and after loading Alembic.
- Do not worry yet about T022+ identity implementation.

Suggested next step: final live PostgreSQL verification of T021.

### 2026-09-16 — T022: User model, email identity and password field

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- HEAD: `cb603b1 feat: add TaskPilot persistence foundation`
- Working tree before changes: a partial T022 implementation was already
  present and uncommitted (interrupted implementer). Existing edits were
  inspected and continued; nothing was reset, cleaned, checked out, or stashed.

What changed:
- Added `persistence.identity.canonicalize_email`, the single application
  email-identity helper: Unicode `strip` for the display email, then Unicode
  `casefold` for `normalized_email`. Blank input raises `ValueError`; non-string
  input raises `TypeError`.
- Added the `User` ORM model (`taskpilot.users`) with UUID4 `id`, `email`,
  `normalized_email`, opaque `password_hash`, `is_active`, and timezone-aware
  `created_at`/`updated_at`. `normalized_email` is `NOT NULL` with a global
  `UNIQUE` constraint; `password_hash` is excluded from `repr`/`str`.
- Added `UserRepository` following the T021 boundary: query, add, and flush
  only. It never commits or closes the caller's session, so caller rollback
  reverses repository changes.
- Added the second TaskPilot Alembic revision `t022_user`
  (`migrations/versions/20260916_01_user.py`) with `down_revision =
  t021_organization`. Upgrade creates only `taskpilot.users`; downgrade drops
  only the TaskPilot user index and table.
- Extended persistence tests with email canonicalization, Unicode casefold,
  redaction, UUID4, inactive state, UTC timestamps, PostgreSQL `timestamptz`
  mapping, duplicate normalized email, `NOT NULL`, rollback, and migration
  upgrade/downgrade/re-upgrade coverage.

Review fixes applied on top of the interrupted work:
- Aligned the ORM and migration check-constraint name to the T021 naming
  convention, which produces `ck_users_user_email_not_blank`.
- Corrected the scenario helper so it no longer reflects `taskpilot.users`
  after a T022 -> T021 downgrade has removed the table.
- Made the users index assertion robust to PostgreSQL's same-named index that
  backs `UNIQUE(normalized_email)`.
- Strengthened `updated_at` verification to assert a strictly advancing value
  that survives commit, and added real Unicode `casefold` and repository
  email-coercion tests.

Files changed:
- `src/persistence/identity.py` (new)
- `src/persistence/models.py`
- `src/persistence/repositories.py`
- `src/persistence/__init__.py`
- `migrations/env.py`
- `migrations/versions/20260916_01_user.py` (new)
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence -q` → PASS (15 passed) with a live
  disposable PostgreSQL instance configured through
  `TASKPILOT_TEST_DATABASE_URL`; the integration tests actually executed.
- `uv run pytest tests/persistence/test_foundation.py -q` → PASS (14 passed).
- `uv run pytest` → full regression executed; see the task report for numbers.
- `uv run ruff format --check` → PASS.
- `uv run ruff check --output-format concise` → PASS.
- `uv run pyrefly check` → PASS.
- `uv lock --check` → PASS.
- `git diff --check` → PASS.
- Migration verification ran live: fresh database -> `head`,
  T021 -> T022, T022 -> T021 downgrade, and T021 -> T022 re-upgrade, each with
  LangGraph saver/store coexistence checked.

Security/scope notes:
- `password_hash` is opaque storage only. No password library, hashing,
  verification, login, token, session, bootstrap, `CurrentPrincipal`,
  Membership, role, authorization, or Task domain code was added.
- No `AUTH_SECRET` behavior, LangGraph table, `src/memory/*`, or event-loop
  handling was changed. `fileConfig(..., disable_existing_loggers=False)` is
  unchanged.
- Canonicalization is application-side `str.casefold`; PostgreSQL `lower()`,
  `citext`, collation identity, IDNA/punycode, and provider-specific email
  rules are deliberately not used.
- No Git add, commit, or push was performed.

Known limitations:
- T022 stores `password_hash` without generating or verifying it; Argon2id
  arrives with the T023 dependency.
- Email uniqueness is global, not organization-scoped, matching ADR-004.
- Alembic emits a pre-existing `path_separator` deprecation warning from
  `cb603b1`; it is historical debt, not introduced or widened by T022.

Learner notes:
- Problem solved: the User identity row now exists with one shared email
  canonicalization contract and a database-enforced unique identity.
- Read `src/persistence/identity.py`, `src/persistence/models.py`,
  `migrations/versions/20260916_01_user.py`,
  `tests/persistence/test_postgres_integration.py`, and
  `docs/DATABASE_DESIGN.md`.
- Key concept: canonical identity belongs in one application helper, while the
  database only enforces `NOT NULL`/`UNIQUE` on the stored normalized value.
- Exercise: insert two users whose display emails differ only by surrounding
  whitespace or case and observe the `UNIQUE(normalized_email)` violation.
- Do not worry yet about password hashing, login, sessions, or Membership; those
  are T023 and T022A.

Suggested next step: strong review of the uncommitted T022 diff, then T022A.

### 2026-09-16 — T022A: Membership model, role enum and active organization

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `4947347 feat: add TaskPilot user identity persistence`
- Working tree before changes: clean.

Task Card interpretation:
- Scope: the `memberships` table with UUID4 `id`, `user_id`, `organization_id`,
  constrained role `owner|admin|member`, `is_active`, UTC timestamps, unique
  `(user_id, organization_id)`, explicit indexes, and non-destructive foreign
  keys, plus the repository and migration that own it.
- Forbidden: login/token issuance, role helper, client-selected organization,
  public registration, LangGraph table changes, and `AUTH_SECRET` changes.

What changed:
- Added the frozen `Role` enum (`owner`, `admin`, `member`) and the `Membership`
  ORM model in `src/persistence/models.py`.
- Added `MembershipRepository` with `add`, `get`,
  `get_for_user_in_organization`, and `list_for_organization`. Every lookup
  names its scope; there is no global membership listing and the repository
  never commits or closes the caller's session.
- Added the third TaskPilot Alembic revision `t022a_membership`
  (`migrations/versions/20260916_02_membership.py`, `down_revision =
  t022_user`). Upgrade creates only `taskpilot.memberships`; downgrade drops
  only the membership indexes and table.
- Registered `Membership` in the Alembic metadata import and exported
  `Membership`, `MembershipRepository`, and `Role` from `persistence`.
- Added unit and live PostgreSQL integration tests for metadata, role
  constraint, unique pair, both foreign keys, RESTRICT delete behavior,
  active/inactive lifecycle, timestamps, rollback, cross-tenant scope, and the
  migration chain.

Role storage decision:
- The role is stored as `VARCHAR(16)` with a PostgreSQL check constraint rather
  than a native PostgreSQL enum type. SQLAlchemy is configured with
  `native_enum=False` and `values_callable`, so the ORM and migration render
  identical DDL with no enum type to create or drop. `alembic check` confirms
  there is no metadata/schema drift.

Files changed:
- `src/persistence/models.py`
- `src/persistence/repositories.py`
- `src/persistence/__init__.py`
- `migrations/env.py`
- `migrations/versions/20260916_02_membership.py` (new)
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`
- `docs/DEVELOPER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence -q` → PASS (25 passed) with a live disposable
  PostgreSQL instance; the PostgreSQL integration tests actually executed.
- `uv run pytest` → full regression executed; see the task report for numbers.
- `uv run ruff format --check` / `uv run ruff check` → PASS.
- `uv run pyrefly check` → PASS.
- `uv lock --check`, `git diff --check` → PASS.
- Migration verification ran live: fresh -> head, T022 -> T022A,
  T022A -> T022 downgrade (users and organizations preserved),
  T022 -> T022A re-upgrade, LangGraph coexistence, and
  `alembic check` (no new upgrade operations).

Security/scope notes:
- No AuthSession, login, token, password hashing, bootstrap, `CurrentPrincipal`,
  authorization dependency, cross-tenant HTTP behavior, role helper, Task
  domain, approval, or organization-switching code was added.
- The repository exposes no global membership listing and no tenant selection
  by client input; scope parameters are explicit.
- No `AUTH_SECRET`, `src/memory/*`, LangGraph table, T021/T022 migration, or
  event-loop behavior was changed.
- No Git add, commit, or push was performed.

Known limitations:
- T022A only stores memberships. Enforcing "exactly one active organization per
  session", validating active user/organization/membership before principal
  construction, and the cross-tenant 404 behavior remain T023/T024/T025 work.
- Because V1 deactivates instead of deleting, downgrading T022A removes the
  memberships table and its rows; re-upgrading recreates it empty while users
  and organizations survive.
- The pre-existing Alembic `path_separator` deprecation warning from `cb603b1`
  remains historical debt, not introduced or widened by T022A.

Learner notes:
- Problem solved: the identity model now records which user belongs to which
  organization with which role, and PostgreSQL enforces the tenant pair and the
  role set instead of trusting application pre-checks.
- Read `src/persistence/models.py`, `src/persistence/repositories.py`,
  `migrations/versions/20260916_02_membership.py`, and
  `tests/persistence/test_postgres_integration.py`.
- Key concept: uniqueness and referential integrity are database invariants, so
  a concurrent or buggy caller cannot create a duplicate or dangling
  membership.
- Exercise: insert two memberships for the same user/organization pair and
  observe the unique violation, then deactivate the first row and confirm the
  duplicate is still rejected.
- Do not worry yet about sessions, login, tokens, or authorization helpers;
  those are T023-T025.

Suggested next step: strong review of the uncommitted T022A diff, then T023.

### 2026-09-16 — T023: Opaque authentication session/token service

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `203a720 feat: add TaskPilot membership persistence`
- Working tree before changes: clean.

Task Card interpretation:
- Scope: `pwdlib[argon2]` Argon2id verification, generic login failure, opaque
  token from >=32 random bytes with SHA-256 hash-at-rest and indexed lookup,
  UUID4 session ID, 24-hour expiry, explicit revocation, expired-session
  cleanup, user+membership binding with active-state revalidation, and the
  controlled bootstrap CLI contract.
- Forbidden: JWT, role claims as authority, refresh-token family, SSO, email
  verification/change, any `AUTH_SECRET` behavior change, and general
  authorization dependencies.

What changed:
- Added `persistence/passwords.py` (Argon2id `hash_password` / `verify_password`
  via `pwdlib`) and `persistence/tokens.py` (32-byte CSPRNG base64url token and
  SHA-256 digest helper).
- Added the `AuthSession` model (`taskpilot.auth_sessions`) and
  `AuthSessionRepository` (`add`, `get`, `get_by_token_hash`, `revoke`,
  `delete_expired`). Added `OrganizationRepository.get_by_name` and
  `MembershipRepository.list_for_user` for bootstrap and login resolution.
- Added `service/session.py`: login, session lookup by digest, validity check,
  revocation, and expired cleanup, with injectable repositories for tests.
- Added `service/bootstrap.py`, `service/bootstrap_cli.py`, and
  `scripts/bootstrap_owner.py` implementing the frozen bootstrap contract.
- Added the fourth Alembic revision `t023_auth_session`
  (`migrations/versions/20260916_03_auth_session.py`, `down_revision =
  t022a_membership`). Upgrade creates only `taskpilot.auth_sessions`; downgrade
  drops only its indexes and table.

Security decisions preserved from ADR-004:
- Only the SHA-256 digest is stored; the raw token is returned once at issuance
  and never appears in a column, parameter name, `repr`, or log line.
- Unknown email, wrong password, inactive user, missing/inactive membership, and
  ambiguous (multiple) memberships all raise one generic
  `LoginError("Invalid credentials")`. One Argon2id verification is always
  performed so a missing account is not distinguishable by timing.
- Session binding is server-derived: the service resolves the caller's own
  membership and refuses to issue when it is not exactly one active row.
- Role and organization remain database truth, not token claims.
- `AUTH_SECRET` compatibility behavior is untouched.

Files changed:
- `pyproject.toml`, `uv.lock` (added `pwdlib[argon2]`; argon2-cffi pulled in)
- `src/persistence/models.py`, `repositories.py`, `__init__.py`
- `src/persistence/passwords.py` (new), `src/persistence/tokens.py` (new)
- `src/service/session.py` (new), `src/service/bootstrap.py` (new),
  `src/service/bootstrap_cli.py` (new)
- `scripts/bootstrap_owner.py` (new)
- `migrations/env.py`, `migrations/versions/20260916_03_auth_session.py` (new)
- `tests/service/test_auth_session.py` (new),
  `tests/service/test_bootstrap.py` (new)
- `tests/persistence/test_foundation.py`, `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`, `docs/DEVELOPER_GUIDE.md`, `docs/SECURITY_HITL.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence -q` → PASS (34 passed) with a live disposable
  PostgreSQL instance; the PostgreSQL integration tests executed.
- `uv run pytest -q` → PASS (276 passed, 4 skipped, 35 warnings); the four skips
  are the unrelated `--run-docker` gates.
- `uv run ruff format --check` / `uv run ruff check` → PASS.
- `uv run pyrefly check` → PASS (0 errors).
- `uv lock --check` → PASS.
- `git diff --check` → PASS.
- Live migration verification: fresh -> head, T022A -> T023,
  T023 -> T022A downgrade (users, memberships, and organizations preserved),
  T022A -> T023 re-upgrade, LangGraph coexistence, and `alembic check`
  ("No new upgrade operations detected"). Offline SQL verified for both the
  upgrade and downgrade paths.
- Bootstrap CLI was exercised end to end against PostgreSQL: first run created
  the owner, the second run was an idempotent no-op, and a `--password` argument
  was rejected with exit code 2 without touching the database.

Scope/security notes:
- No `CurrentPrincipal`, request authentication dependency, authorization
  middleware, cross-tenant 404, same-tenant 403, Task domain, approval, or
  organization-switching code was added.
- No JWT, refresh token, SSO, bcrypt/passlib, Redis session store, or OAuth/OIDC
  stack was introduced.
- No `AUTH_SECRET`, `src/memory/*`, LangGraph table, T021/T022/T022A migration,
  or event-loop behavior was changed.
- No Git add, commit, or push was performed.

Known limitations:
- `service/session.py` deliberately issues a session only when the user has
  exactly one active membership. Organization switching (a new session for a
  different membership) is a later decision, and login therefore rejects users
  who already hold several active memberships.
- Expired-session cleanup is an explicit call (`cleanup_expired_sessions`); no
  scheduled job invokes it yet.
- The principal/authorization layer (fresh per-request revalidation, 401/403/404
  policy, tenant scoping) is still T024/T025.
- The pre-existing Alembic `path_separator` deprecation warning from `cb603b1`
  remains historical debt.

Learner notes:
- Problem solved: TaskPilot can now verify a password with Argon2id and issue an
  opaque, revocable, 24-hour session that is bound to one user and one
  membership, without ever storing the token a client presents.
- Read `src/persistence/tokens.py`, `src/persistence/passwords.py`,
  `src/service/session.py`, `migrations/versions/20260916_03_auth_session.py`,
  and `tests/service/test_auth_session.py`.
- Key concept: a bearer token is verified by hashing it and comparing digests,
  so the database only ever holds a one-way fingerprint; the same trick does not
  work for passwords, which need a slow salted KDF such as Argon2id.
- Exercise: log in twice and compare the stored `token_hash` values, then
  confirm that neither the raw token nor `"correct horse battery staple"` can be
  recovered from the `auth_sessions` or `users` rows.
- Do not worry yet about request dependencies, 403/404 policy, or tenant
  resource scoping; those are T024 and T025.

Suggested next step: strong review of the uncommitted T023 diff, then T024.

### 2026-09-16 — T023 Strong Review blocker fixes (1–3)

Status: BLOCKER FIXES COMPLETE — BLOCKER 4 BLOCKED ON ARCHITECTURE DECISION

Baseline:
- Branch: `phase-2-identity-rbac`
- T023 baseline HEAD: `203a720 feat: add TaskPilot membership persistence`
- The uncommitted T023 implementation was fixed in place; nothing was reset,
  cleaned, restored, checked out, or stashed.

BLOCKER 1 — argument rejection echoed the supplied value:
- `argparse` printed `unrecognized arguments: <value>` on stderr, so a password
  typed as a positional or unknown argument was echoed verbatim.
- `service/bootstrap_cli.py` now uses a private `_SafeArgumentParser` whose
  `error()` raises a `BootstrapArgumentError` that renders only the fixed
  `bootstrap failed: invalid bootstrap arguments` string. The CLI prints that
  fixed message and exits `2` before any database work.
- Tests assert a synthetic secret (`SuperSecret123!`) never reaches stdout,
  stderr, or the exception text, including a subprocess run of the shipped
  entry point.

BLOCKER 2 — database exceptions could expose bind parameters:
- The CLI caught only `BootstrapError`/`PwdlibError`, so a `SQLAlchemyError`
  could surface a traceback containing SQL text and bind parameters such as
  `password_hash`.
- `_run` now converts `SQLAlchemyError` and connection `OSError`/`TimeoutError`
  into the fixed `bootstrap failed: database operation failed; no changes were
  committed` message without printing the exception, its repr, or a chained
  cause. The session context manager still performs the rollback.
- Tests cover both a fault-injected `IntegrityError` carrying a synthetic
  `password_hash`, and a live PostgreSQL failure path that asserts full
  rollback (no partial bootstrap rows) plus absence of the plaintext password,
  the stored hash, SQL text, and bind parameters from all output.

BLOCKER 3 — environment variable bypassed the hidden double confirmation:
- Removed the unauthorized `TASKPILOT_BOOTSTRAP_PASSWORD` source and the
  `--no-input` flag, plus every doc, help-text, and test reference to them.
- The password is now read exclusively from the two hidden `getpass` prompts.
  Non-secret organization name and email may still come from flags, the
  `TASKPILOT_BOOTSTRAP_ORGANIZATION_NAME` / `TASKPILOT_BOOTSTRAP_EMAIL`
  variables, or interactive prompts.
- Tests assert both prompts are always used, a mismatch and a blank entry fail
  closed, the environment variable cannot supply a password, an interrupted
  prompt produces no traceback, and `--no-input` no longer exists.

BLOCKER 4 — membership selection is not frozen:
- Reported as `ARCHITECTURE GAP — MEMBERSHIP SELECTION NOT FROZEN`. No selector
  was invented. `AuthService` still rejects a login that does not resolve to
  exactly one active membership; see the task report for the exact frozen text.

Files changed by this fix round:
- `src/service/bootstrap_cli.py`
- `tests/service/test_bootstrap.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DEVELOPER_GUIDE.md`, `docs/DATABASE_DESIGN.md`, `docs/SECURITY_HITL.md`
- `scripts/bootstrap_owner.py`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- focused auth + bootstrap: PASS (56 passed).
- `uv run pytest tests/persistence -q` with live PostgreSQL: PASS (36 passed).
- `uv run pytest -q`: PASS (298 passed, 4 skipped, 37 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.
- Migrations were not modified, so the previously verified migration behavior
  is unchanged and its tests remain green.

Security notes:
- No new secret input channel exists; no JWT, refresh token, authorization
  dependency, or T024 work was added.
- `AUTH_SECRET`, `src/memory/*`, and the existing service routes are unchanged.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: the CLI no longer echoes a mistyped secret and no longer
  prints raw database exceptions, and the bootstrap password can only come from
  the hidden double prompt.
- Read `src/service/bootstrap_cli.py` and `tests/service/test_bootstrap.py`.
- Key concept: an error boundary must print fixed text, because both argument
  text and SQL bind parameters are attacker- or user-controlled and can contain
  a credential.
- Exercise: run the entry point with a fake password as a positional argument
  and confirm the value never appears in the output.
- Do not worry yet about organization switching; the selection mechanism is the
  open architecture question recorded for BLOCKER 4.

Suggested next step: architecture decision on membership selection, then
focused re-review.

### 2026-09-16 — T023 membership selection architecture gap

Status: architecture decision complete; implementation pending

What changed:

- Confirmed ADR-004/T023 omitted multi-organization login selection and current session code counts all memberships before active filtering.
- Froze a password-authenticated optional organization selector, typed selection-required result containing eligible IDs only, generic invalid-selector failures, eligibility-before-counting, and fresh login for switching.
- Added future selection acceptance tests to T023; no production/test implementation was edited.

Files changed: `process/DECISION_LOG.md`, `process/tasks/T023.md`, `process/PROGRESS_LOG.md`.

Validation:

- Read T020, T022A, T023, T024, INDEX, ADR-004, current session service and auth-session tests.
- T023 Markdown check PASS; touched-file Markdown scan reports pre-existing log formatting violations only after the new entry is corrected.
- `git diff --check` and non-target SHA-256 comparison PASS; all 216 non-target files preserved.
- Runtime tests not run: architecture documentation only.

Known limitations:

- Existing T023 implementation still requires the selection correction and Strong Review; this decision does not certify it.
- The workspace already contained production, migration, dependency, test and documentation changes before this task; those changes are preserved.

Learner notes:

- Problem solved: legitimate multi-org users now have an explicit login selection contract.
- Read `process/DECISION_LOG.md`, `process/tasks/T023.md`, and `src/service/session.py`.
- Key concept: a selector requests a context; verified server Membership grants that context.
- Exercise: trace active A plus inactive B, then two active memberships without a selector.
- Ignore for now: chooser tokens, switching UI and HTTP status mapping.

Suggested next task: implement only the frozen T023 selection correction when authorized, then Strong Review; do not start T024 here.

### 2026-09-17 — T023 BLOCKER 4: frozen membership selection implemented

Status: BLOCKER 4 FIXED PER FROZEN CONTRACT — READY FOR FOCUSED RE-REVIEW

Baseline:
- Branch: `phase-2-identity-rbac`
- Committed baseline HEAD: `203a720 feat: add TaskPilot membership persistence`
- Working tree already contained the uncommitted T023 implementation and the
  BLOCKER 1–3 fixes; nothing was reset, cleaned, restored, checked out, or stashed.

Frozen contract implemented:
- Source: `process/DECISION_LOG.md` → "T023 membership selection addendum —
  frozen 2026-09-16", plus `process/tasks/T023.md` → "Frozen membership
  selection contract" and "Selection acceptance cases".
- `AuthService.login(email, password, *, organization_id: UUID | None = None)`
  now returns `AuthenticatedSession | OrganizationSelectionRequired`.

What changed:
- Added the immutable `OrganizationSelectionRequired` result carrying only
  `code = "ORGANIZATION_SELECTION_REQUIRED"` and a deduplicated
  `organization_ids: tuple[UUID, ...]` sorted by canonical UUID string. It holds
  no token, session, role, membership id, or user data, and it cannot be
  mutated into a credential.
- Added `MembershipRepository.list_eligible_for_user`, which joins memberships
  to organizations in PostgreSQL and requires user scope, active membership,
  and an existing active organization. Eligibility is no longer decided in
  Python and inactive or dangling rows are never counted.
- Rewrote login selection: credentials and active user are verified first, then
  membership metadata is read. No selector issues for one eligible membership,
  fails generically for zero, and returns the typed selection result for several
  without choosing a first/owner/most-recent default. An explicit selector must
  match a verified eligible membership of that same user; malformed, unknown,
  foreign, inactive, or ambiguous matches all raise the generic
  `LoginError("Invalid credentials")` with no fallback and no list.
- Selection is resolved before any token is generated or session staged, so a
  selection-required outcome writes nothing and produces no raw token.
- Removed the now-unused `LoginFailure.INACTIVE_MEMBERSHIP` and the unused
  organization repository injection from `AuthService`.

Files changed by this fix round:
- `src/service/session.py`
- `src/persistence/repositories.py`
- `tests/service/test_auth_session.py`
- `tests/service/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/SECURITY_HITL.md`, `docs/DATABASE_DESIGN.md`, `docs/DEVELOPER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- focused auth/session + bootstrap: PASS (72 passed).
- `uv run pytest tests/persistence -q` with live PostgreSQL: PASS (39 passed).
- `uv run pytest -q`: PASS (317 passed, 4 skipped, 39 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.
- Migrations were not modified, so migration verification is unchanged and its
  tests remain green.

Security notes:
- BLOCKER 1–3 fixes are preserved: argument values are never echoed, database
  exceptions are converted to a fixed message with no SQL/bind/`password_hash`,
  and the bootstrap password still comes only from the hidden double prompt.
- The selector is a requested context only; ownership, membership state, and
  organization state are all re-read from the database on every login, so a
  selection result is never a credential.
- Existing sessions are never mutated or implicitly revoked by switching.
- No `CurrentPrincipal`, authorization dependency, 403/404 policy, Task domain,
  chooser credential, JWT, or refresh token was added. T024 was not started.
- No Git add, commit, or push was performed.

Known limitations:
- The HTTP mapping of `OrganizationSelectionRequired` (status code, response
  envelope, and how a browser resubmits the selector) is deliberately outside
  T023 and belongs to a later card.
- The identifier is still `organization_id`; a membership-id selector was
  considered and rejected by the frozen addendum.

Learner notes:
- Problem solved: a user in several organizations can authenticate, see only
  their own eligible organization IDs, and then obtain a session bound to the
  organization they actually chose.
- Read `src/service/session.py`, `src/persistence/repositories.py`, and the
  selection tests in `tests/service/test_auth_session.py`.
- Key concept: authentication and selection are separate phases. Eligibility is
  a database question, and a "which organization?" answer is not a credential.
- Exercise: create a user with two active memberships, log in without a
  selector and observe the typed result, then log in twice with each selector
  and compare the two sessions' memberships and revocation state.
- Do not worry yet about the HTTP layer for the selection result, or about the
  per-request principal; those are later cards.

Suggested next step: focused re-review of the T023 diff, then T024.

### 2026-09-17 — T023 BLOCKER 4 trust-chain fix: service-boundary ownership check

Status: BLOCKER 4 CLOSED — READY FOR FINAL FOCUSED RE-REVIEW

Baseline:
- Branch: `phase-2-identity-rbac`
- Committed baseline HEAD: `203a720 feat: add TaskPilot membership persistence`
- Working tree already contained the uncommitted T023 implementation, the
  BLOCKER 1–3 fixes, and the frozen membership-selection addendum; nothing was
  reset, cleaned, restored, checked out, or stashed.

Root cause:
- `AuthService._resolve_selection` trusted every row returned by
  `MembershipRepository.list_eligible_for_user(user_id)`. ADR-004's selection
  addendum step 2 requires that ownership be checked *even when consuming
  repository results*, so the service boundary itself must verify
  `membership.user_id == authenticated_user.id` rather than relying on the
  repository method name or its SQL predicate.

Exact fix:
- Added `AuthService._require_owned_memberships(user_id, memberships)`.
- `_resolve_selection` calls it immediately after the repository query and
  before any selector matching, `OrganizationSelectionRequired` construction,
  token generation, or session issuance. A single foreign row fails the entire
  result set closed with the frozen generic `LoginError("Invalid credentials")`;
  rows are never silently filtered and there is no fallback.
- The repository's user-scoped SQL predicate is unchanged; this is defense in
  depth at the service boundary, not a replacement for tenant scoping.

Tests added (focused, repository stub that ignores user scope):
- foreign row only → generic failure, no `OrganizationSelectionRequired`, no
  metadata, token factory called 0 times, no session staged or persisted.
- mixed own + foreign rows → generic failure even though a legitimate own row
  exists; the broken invariant is never silently downgraded, for both the
  no-selector and explicit-selector paths.
- owned rows through the same boundary still issue normally, still honor an
  explicit selector, and still return `OrganizationSelectionRequired` for two
  eligible memberships.
- live PostgreSQL variant: a scope-leaking repository method returns a
  stranger's real membership row for the authenticated user and the service
  still fails closed with no `auth_sessions` row written.

Files changed by this fix round:
- `src/service/session.py`
- `tests/service/test_auth_session.py`
- `tests/persistence/test_postgres_integration.py`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_auth_session.py -q` → PASS (51 passed).
- `uv run pytest tests/service/test_auth_session.py tests/service/test_bootstrap.py -q`
  → PASS (78 passed).
- `uv run pytest tests/persistence -q` with live PostgreSQL → PASS (40 passed).
- `uv run pytest -q` → PASS (324 passed, 4 skipped, 40 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.
- No migration was modified.

Security notes:
- BLOCKER 1–3 fixes and all previously frozen selection tests still pass.
- The ownership failure is indistinguishable from a bad credential, so a
  foreign organization id is never disclosed through an error or a selection
  list.
- No `CurrentPrincipal`, authorization dependency, 403/404 policy, Task domain,
  or repository redesign was introduced. T024 was not started.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: the service no longer trusts "the repository is scoped" as a
  security argument; it re-checks ownership before using any membership row.
- Read `src/service/session.py` (`_resolve_selection` and
  `_require_owned_memberships`) and the ownership tests in
  `tests/service/test_auth_session.py`.
- Key concept: defense in depth means the component that makes the security
  decision validates its inputs, even when a lower layer already promised to.
- Exercise: make the stub return one own and one foreign membership and confirm
  the login fails instead of issuing a session for the own membership.
- Do not worry yet about the HTTP mapping of the selection result or the
  per-request principal.

Suggested next step: final focused re-review of the T023 diff.

### 2026-09-17 — T024: CurrentPrincipal FastAPI dependency

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `0c3ccd2 feat: add TaskPilot authentication and sessions` (T023)
- Working tree before changes: clean.

Task Card interpretation:
- Scope: a typed request-scoped dependency consuming T023 opaque sessions, with
  `CurrentPrincipal` holding `user_id`, `membership_id`, `organization_id`,
  `role`, and `session_id`, all server-derived, plus fresh per-request checks on
  session, user, membership, and organization state, and one generic 401.
- Forbidden: any authorization policy (403/404, role matrix, tenant resource
  enforcement), changes to legacy `AUTH_SECRET` behavior, and a new schema.

What changed:
- Added `CurrentPrincipal` and `build_principal` to `src/service/session.py`.
  The value object is a frozen, slots-based dataclass of plain scalars: no ORM
  row, no `AsyncSession`, no repository, no request object, and no credential
  material. `build_principal` re-asserts the trust chain (user, membership
  owner, session binding, organization match) and fails closed on any
  inconsistency.
- Added `AuthService.authenticate(raw_token)`, which resolves a credential to a
  principal by hashing the token through the existing T023 helper and re-reading
  session, user, membership, and organization state on every call. It returns
  `None` for every failure and never writes.
- Added `src/service/auth_dependency.py` with `require_principal`,
  `get_session_factory`, and `get_session`. The dependency accepts only
  `Authorization: Bearer <opaque-token>`, acquires a session per request, closes
  it in `finally`, rolls back on exceptions, never commits, and raises one
  generic 401 (`{"detail": "Not authenticated"}` with `WWW-Authenticate: Bearer`)
  for every authentication failure.
- `get_session_factory` and `get_session` are first-class `Depends` providers so
  tests can override the database without monkeypatching module globals.

Files changed:
- `src/service/session.py`
- `src/service/auth_dependency.py` (new)
- `tests/service/test_current_principal.py` (new)
- `tests/persistence/test_current_principal_integration.py` (new)
- `docs/SECURITY_HITL.md`, `docs/DEVELOPER_GUIDE.md`, `docs/API_CONVENTIONS.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_current_principal.py -q` → PASS (21 passed).
- `uv run pytest tests/persistence/test_current_principal_integration.py -q` with
  live PostgreSQL → PASS (21 passed, genuinely executed against a disposable
  database and real FastAPI requests).
- `uv run pytest -q` → PASS (366 passed, 4 skipped, 61 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.

Security notes:
- The principal is derived only from the server-side session lookup plus current
  rows; `user_id`, `organization_id`, and role supplied through query strings or
  headers were verified to have no effect.
- Role freshness is tested: changing the membership role changes the next
  principal's role while the session stays valid and bound to the same tenant.
- Deactivated user, membership, or organization after issuance yields 401 on the
  next request; revoked, expired, unknown, malformed, mismatched, and
  foreign-membership sessions all yield the same 401.
- The dependency never emits the raw token, its digest, or the `Authorization`
  header in a response, and authentication performs no writes or commits.
- The legacy `AUTH_SECRET` bearer does not authenticate a TaskPilot request, and
  its upstream behavior is unchanged.

Known limitations:
- `AuthService.authenticate` issues one query per checked row (session,
  user, membership, organization). That is deliberate freshness over speed for
  V1 and can be optimized later without changing the contract.
- The database-level "dangling membership" case cannot be constructed because
  the foreign keys forbid it; it is covered by service-level tests instead.
- The 403 authorization policy and cross-tenant 404 semantics remain T025.

Learner notes:
- Problem solved: a protected request now receives a typed identity built from
  current server state, independent of anything the caller claims.
- Read `src/service/auth_dependency.py`, `CurrentPrincipal` and `authenticate` in
  `src/service/session.py`, and the live tests in
  `tests/persistence/test_current_principal_integration.py`.
- Key concept: authentication answers "is there a currently valid principal?",
  while authorization answers "may that principal do this?" - T024 implements
  only the first, and re-derives it on every request.
- Exercise: issue a token, deactivate the membership, and observe the next
  request return the same 401 as an unknown token.
- Do not worry yet about role policy, 403/404 semantics, or Task resources.

Suggested next step: strong review of the T024 diff, then T025.

### 2026-09-17 — T024 Strong Review blocker fix: session lifecycle ordering

Status: BLOCKER FIXED — READY FOR FOCUSED RE-REVIEW

Baseline:
- Branch: `phase-2-identity-rbac`
- Committed baseline HEAD: `0c3ccd2 feat: add TaskPilot authentication and sessions`
- Working tree already contained the uncommitted T024 implementation; nothing
  was reset, cleaned, restored, checked out, or stashed.

Root cause:
- `require_principal` extracted the bearer token and raised the generic 401
  *before* entering the `try/finally`, while the FastAPI-injected
  `AsyncSession` already existed. Every missing/malformed credential path
  therefore returned the session to nobody, leaking one pooled connection per
  rejected request.

Exact lifecycle fix:
- The cleanup boundary now starts immediately after the injected session
  exists, and the token check moved inside it, so all outcomes share one exit
  point: missing header, wrong scheme, empty or extra-word bearer, unknown
  token, revoked/expired session, inactive user/membership/organization,
  relation mismatch, unexpected exception, and success.
- `finally` performs the cleanup, `_close_session` is the single `close()`
  call site, and `_cleanup_session` (rollback then close) is used only for
  unexpected exceptions. Exactly-once close holds because there is one
  `finally` and no close call in any branch.
- A rollback failure is logged without exception text and can neither prevent
  the close nor replace the original error.

Tests added:
- Focused (`tests/service/test_current_principal.py`, now 33 tests): a tracked
  session double asserts `close == 1` and `rollback == 0` for missing header,
  wrong scheme, empty bearer, and extra-word bearer; `close == 1` for an
  unknown/invalid token and for success; `rollback == 1` and `close == 1` for an
  unexpected exception; and `close == 1` even when rollback itself fails. One
  test drives the real `require_principal` dependency through FastAPI so the
  cleanup boundary itself is covered, not just a helper.
- PostgreSQL integration (now 22 tests): five real requests (four rejected,
  one valid) each use a real session and a proxy proves one session per request
  with exactly one close each.
- The new integration test was verified to detect the original defect: with the
  raise reverted to its old position the close counts drop to `[0, 0, 0, 1, 1]`.

Files changed by this fix round:
- `src/service/auth_dependency.py`
- `tests/service/test_current_principal.py`
- `tests/persistence/test_current_principal_integration.py`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_current_principal.py -q` → PASS (33 passed).
- `uv run pytest tests/persistence/test_current_principal_integration.py -q` with
  live PostgreSQL → PASS (22 passed).
- `uv run pytest tests/service/test_auth_session.py -q` → PASS (51 passed).
- `uv run pytest -q` → PASS (379 passed, 4 skipped, 62 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.

Security notes:
- HTTP semantics are unchanged: 401 with `{"detail": "Not authenticated"}` and
  `WWW-Authenticate: Bearer`; no credential, token digest, or database error
  text is emitted.
- Normal authentication rejections do not roll back (nothing was written); only
  unexpected exceptions roll back, then close.
- All approved T024 behavior is untouched, and no schema, migration, or T025
  authorization code was added.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: a request-scoped session is now always returned, even when the
  request is rejected before authentication begins.
- Read `src/service/auth_dependency.py` (`require_principal`, `_close_session`,
  `_cleanup_session`) and the lifecycle tests in
  `tests/service/test_current_principal.py`.
- Key concept: an injected resource's cleanup boundary must be entered at the
  moment the resource exists, before any validation that can raise.
- Exercise: move the token check back above the `try` and watch the tracked
  close counts drop to zero for the malformed-credential cases.
- Do not worry yet about authorization or tenant 403/404 policy.

Suggested next step: focused re-review of the T024 diff.

### 2026-09-17 — T025: central tenant authorization helper

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `4d00359 feat: add request-scoped TaskPilot principal` (T024),
  in sync with `origin/phase-2-identity-rbac`
- Working tree before changes: clean.

Task Card interpretation:
- Scope: the single server-side policy boundary with
  `require_authenticated`, `require_active_membership`, `require_role(roles)`,
  and `require_resource_tenant(resource_organization_id)`; tenant-scoped
  repositories carry an explicit `organization_id` predicate; same-tenant allow,
  in-tenant insufficient role 403, cross-tenant/nonexistent 404.
- Forbidden: endpoint-specific cross-tenant semantics, an enterprise policy
  engine, client role trust, and LangGraph state/session coupling.
- T026 boundary: the authoritative negative security/persistence test matrix.
  T025 supplies the policy boundary and the focused proof; T026 owns the full
  matrix.
- The card also mentions approval reads/decisions. No approval domain, model, or
  table exists in any frozen Phase 2 card, so implementing approval
  authorization would require inventing that schema. The one role set the ADR
  states explicitly (`owner` or `admin`) is frozen as `APPROVAL_DECISION_ROLES`;
  approval-specific authorization and duplicate-decision idempotency are
  deferred to the task that introduces those records. Recorded, not invented.

What changed:
- Added `src/service/authorization.py`: the four guards plus `AuthorizationError`
  (fixed 403/404 status and non-disclosing detail), FastAPI guard wrappers
  (`require_authenticated_principal`, `require_role_dependency`,
  `require_resource_tenant_dependency`), and `APPROVAL_DECISION_ROLES`.
- Role policy is an explicit set match on `principal.role`, never a hierarchy:
  `owner > admin > member` is not assumed, so an owner is rejected by a
  member-only operation.
- `require_resource_tenant` answers 404 for foreign and for nonexistent
  resources alike; an owner is never a global owner.
- Added `OrganizationRepository.get_in_principal_tenant(organization_id,
  principal_organization_id)` as the tenant-scoped query pattern
  (`WHERE id = :id AND organization_id = :principal_organization_id`), so a
  foreign row is not found rather than fetched and compared in Python.

Files changed:
- `src/service/authorization.py` (new)
- `src/persistence/repositories.py`
- `tests/service/test_authorization.py` (new)
- `tests/persistence/test_authorization_integration.py` (new)
- `tests/persistence/test_foundation.py`
- `docs/SECURITY_HITL.md`, `docs/API_CONVENTIONS.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_authorization.py -q` → PASS (31 passed).
- `uv run pytest tests/persistence/test_authorization_integration.py -q` with
  live PostgreSQL → PASS (10 passed, genuinely executed through real FastAPI
  requests and real tokens against a disposable database).
- `uv run pytest -q` → PASS (421 passed, 4 skipped, 72 warnings; the skips are
  the unrelated `--run-docker` gates).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.

Security notes:
- 401 is never converted into an authorization answer: a missing principal
  still yields T024's 401 envelope, and the guards run only for an
  authenticated principal.
- Tenant existence is resolved before the role check, so a foreign resource
  never returns 403 - including when the caller's role is high or the URL id is
  foreign while query/header inputs claim the caller's own organization.
- Authorization failures reveal no token, organization id, user id, membership
  id, or other-tenant detail.
- No schema or migration change; `AUTH_SECRET` behavior is untouched and no
  legacy auth file was modified.

Known limitations:
- `require_active_membership` performs no extra query because T024 already
  guarantees an active membership before a principal exists; it is the
  documented policy seam, not a second authentication path.
- `OrganizationRepository.get_in_principal_tenant` is intentionally a small
  two-predicate query. For the principal's own organization the id and the
  principal tenant are equal, so the predicate is currently redundant by
  construction - it exists as the enforced pattern that the future
  task-owned resources will follow.
- Approval authorization and duplicate-decision idempotency await the approval
  domain.

Learner notes:
- Problem solved: TaskPilot now has one place that answers "may this valid
  principal do this?", with fixed 403/404 semantics and tenant isolation.
- Read `src/service/authorization.py`, `OrganizationRepository.get_in_principal_tenant`,
  and the live tests in `tests/persistence/test_authorization_integration.py`.
- Key concept: authorization needs the resource's tenant to be resolved *inside*
  the caller's scope, otherwise a 403/404 difference becomes an
  existence-enumeration side channel.
- Exercise: request a foreign organization id as an owner and observe the same
  404 body as a random nonexistent id.
- Do not worry yet about approval records or the broader negative matrix.

Suggested next step: strong review of the T025 diff, then T026.

### 2026-09-18 — T025 Strong Review blocker fix: integration chain must hit the repository

Status: BLOCKER FIXED — READY FOR FOCUSED RE-REVIEW

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `4d00359 feat: add request-scoped TaskPilot principal` (T024)
- Working tree already contained the uncommitted T025 implementation.

Root cause:
- The T025 integration test's test-only route took the path
  `resource_organization_id` straight into `require_resource_tenant`, which is a
  pure UUID comparison. The HTTP flow therefore never called
  `OrganizationRepository.get_in_principal_tenant`, so the suite did not prove
  the frozen chain `request -> require_principal -> tenant-scoped PostgreSQL
  lookup -> resource visibility -> role authorization`.

Exact fix (test wiring and the repository docstring only; no production
abstraction added):
- The tenant routes now depend on a request-scoped `OrganizationRepository` and
  call `get_in_principal_tenant(resource_organization_id,
  principal.organization_id)` as the first decision step. A `None` result raises
  the fixed 404 before any role comparison, so foreign and nonexistent resources
  are indistinguishable.
- `/tenant-admin/{id}` then applies `require_role([Role.ADMIN])`, proving the
  ordering: same tenant + insufficient role is 403, while a foreign or missing
  resource is 404 for every role.
- The principal's organization is always the repository scope. Tests record the
  `(resource_id, scope_id)` pairs actually passed to the repository and assert
  the scope is the principal's, even when the caller supplies a matching
  `organization_id` query parameter, `X-Organization-Id` header, and role claim.
- `OrganizationRepository.get_in_principal_tenant` keeps both SQL predicates
  (`id` and `organization_id`); only its docstring changed, to state precisely
  that for an organization row the tenant is the organization, so the principal
  can only address its own tenant's row, and that later tenant-owned resources
  reuse the same two-predicate shape with a distinct `organization_id` column.

Test corrections found while fixing (test expectations, not production bugs):
- The earlier route/role expectations conflated "another tenant's owner" with
  "a foreign resource". A foreign owner's principal organization is their own
  tenant, so their scoped lookup for the caller's organization correctly returns
  nothing; the expectation is 404, not 403. The 403 case is an in-tenant
  principal with an insufficient role against an in-tenant organization.

SQL boundary proof:
- `test_http_flow_executes_a_tenant_scoped_database_query` listens on the engine
  the request session actually uses (`before_cursor_execute`) and asserts that
  the HTTP flow executes a query on `taskpilot.organizations` whose WHERE clause
  contains the id predicate twice - the requested id and the principal's tenant
  scope. Identity reads use single-key lookups and are filtered out of the set.

Files changed by this fix round:
- `tests/persistence/test_authorization_integration.py`
- `src/persistence/repositories.py` (docstring accuracy only)
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_authorization.py -q` → PASS (31 passed).
- `uv run pytest tests/persistence/test_authorization_integration.py -q` with
  live PostgreSQL → PASS (12 passed, 0 skips).
- `uv run pytest tests/service/test_current_principal.py tests/service/test_authorization.py -q`
  → PASS (64 passed).
- `uv run pytest tests/persistence -q` → PASS (75 passed).
- `uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check`,
  `uv lock --check`, `git diff --check`: PASS.

Security notes:
- 401 is still decided by T024 before any authorization or lookup work.
- Step 1 is the scoped lookup; step 2 is the role check. No route performs a
  role comparison before tenant-scoped existence is resolved.
- Caller-supplied organization id/role cannot change the repository scope, and
  the foreign row is verified to exist in the table while staying invisible to
  the scoped query.
- No T026, Task domain, approval domain, permission table, policy engine,
  schema/migration, or `AUTH_SECRET` change was introduced.
- No Git add, commit, or push was performed.

Learner notes:
- Problem solved: the integration suite now proves the whole authorization
  chain, including the database, instead of only a UUID comparison.
- Read `tests/persistence/test_authorization_integration.py` and
  `OrganizationRepository.get_in_principal_tenant`.
- Key concept: a security test must exercise the layer that enforces the
  invariant. Asserting the HTTP status alone can pass while the real query is
  never executed.
- Exercise: remove the repository call from the route and watch the
  `scoped_queries`/SQL assertions fail even though the status codes still look
  right.
- Do not worry yet about the Task-owned resources that will reuse this pattern.

Suggested next step: focused re-review of the T025 diff, then T026.

### 2026-09-18 — T026: Phase 2 authoritative security and persistence matrix

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `f9e3a98 feat: add TaskPilot authorization boundaries` (T025),
  the approved and committed T025 implementation.
- Working tree before changes: clean (`git status --short` produced no output).

Task Card interpretation:
- Scope: `process/tasks/T026.md` is a tests-only card ("Tests only; do not alter
  production behavior to satisfy tests"). It owns the authoritative negative
  security, transaction and persistence matrix.
- Boundary: T023/T024/T025 keep their approved designs and focused suites. T026
  adds the matrix rows those suites do not prove and does not rewrite them.
- Forbidden: Task domain/CRUD, approval schema or records, permission or role
  tables, policy engine, JWT, refresh tokens, organization-switch endpoint,
  migrations/schema, and any T027 documentation work.
- Conflicts/gaps: two matrix rows cannot be implemented as written.
  (1) The role/approval row names unauthorized approval and duplicate decision,
  but no frozen Phase 2 card introduces approval records; only the frozen
  `APPROVAL_DECISION_ROLES` gate is exercised, and record lookup plus
  duplicate-decision idempotency stay deferred exactly as T025 recorded.
  (2) The persistence row names the "production forward-only policy and
  separately reviewed destructive downgrade", which is a release-process rule;
  the testable half (no application code path can migrate or downgrade) is
  asserted, and the process half stays with T027.

Matrix to test map:
- Authentication - valid, missing, malformed, unknown, expired, revoked,
  inactive user/membership/organization, legacy bearer, generic failure, and
  the explicit active-session + inactive-organization case with no principal
  and no protected-resource access:
  `test_security_matrix_integration.py::test_authentication_matrix_fails_closed_without_reaching_a_resource`
  and `::test_active_session_with_inactive_organization_is_401_and_touches_nothing`.
- Tenant - same-tenant allow, cross-tenant 404, identical nonexistent 404,
  forged user/organization/role claims, the DB-scoped SQL predicate, and a
  buggy lookup that still fails closed:
  `::test_tenant_matrix_allows_same_tenant_and_hides_other_tenants`,
  `::test_forged_identity_inputs_cannot_select_the_taskpilot_scope`, and
  `::test_buggy_repository_result_cannot_turn_a_foreign_row_into_access`.
- `/threads` and AG-UI identity attempts:
  `::test_threads_query_identity_cannot_select_the_taskpilot_scope` and
  `::test_agui_configurable_identity_cannot_select_the_taskpilot_scope`.
- Role/approval - allowed owner/admin/member operations, denied role, approval
  gate, and 401-never-403 without a principal:
  `::test_role_and_approval_matrix_is_decided_by_the_server_derived_role` and
  `::test_unauthenticated_requests_never_reach_a_role_decision`.
- Secrets - password, hash, token and digest absent from responses and
  structured logs, bootstrap log secrecy, legacy secret redaction:
  `::test_credentials_never_appear_in_responses_or_structured_logs`,
  `test_postgres_integration.py::test_bootstrap_never_logs_the_password_or_the_stored_hash`,
  `tests/service/test_logging.py::test_legacy_auth_secret_is_redacted_from_logs`.
- Persistence - fresh database, existing upgrade, revision metadata, LangGraph
  coexistence, one-revision development/test downgrade, rollback on error,
  independent sessions and cleanup were already proven by the T021-T023 rows in
  `test_postgres_integration.py` and are unchanged; the forward-only production
  policy is now asserted by
  `test_foundation.py::test_production_code_never_imports_the_migration_toolchain`.

What changed:
- `tests/persistence/test_security_matrix_integration.py` (new, 10 tests): one
  disposable PostgreSQL database per test, real FastAPI request handling, the
  real T024 dependency and T025 guards, and the real upstream `/threads` and
  `/agui` routers mounted beside the TaskPilot routes. Every protected route
  body appends to an access list, so "no principal and no protected access" is
  asserted instead of inferred from a status code. A `/tenant-confirmed/{id}`
  route additionally applies the frozen `require_resource_tenant` confirmation
  after the scoped lookup, so an injected buggy repository returning a foreign
  row is proven to fail closed with the same 404.
- `tests/persistence/test_postgres_integration.py`: one new test proving a real
  bootstrap writes neither the plaintext password nor the stored Argon2 hash to
  the structured logs, even when the persisted user row is debug-logged.
- `tests/service/test_logging.py`: one new test proving the configured legacy
  `AUTH_SECRET` is redacted through settings-derived secret registration.
- `tests/persistence/test_foundation.py`: one new AST test proving no module
  under `src/` imports the Alembic toolchain, so application startup can never
  migrate or downgrade the schema.
- `process/PROGRESS_LOG.md` (this entry).
- No production file, dependency, schema, migration, or `AUTH_SECRET` behavior
  changed, so no other document became stale; documentation stays T027's scope.

Why each new file/helper was necessary:
- The matrix module exists because the `/threads`/AG-UI identity rows and the
  no-protected-access row need a composition (upstream routers + TaskPilot
  chain + disposable database) that no existing suite builds; adding them to the
  T024/T025 suites would have meant editing approved files and blurring task
  ownership.
- `_RecordingCheckpointer`, `_build_upstream_agui_agent` and `_apply_guard` are
  the smallest doubles needed to reach the real upstream handlers and the frozen
  guard contract. No production abstraction was added.

Commands/tests run:
- `uv run pytest tests/persistence/test_security_matrix_integration.py -q` with
  live PostgreSQL -> PASS (10 passed, 0 skipped, 10 disposable databases).
- `uv run pytest tests/service/test_auth_session.py tests/service/test_current_principal.py
  tests/service/test_authorization.py tests/service/test_bootstrap.py tests/service/test_auth.py -q`
  -> PASS (145 passed).
- `uv run pytest tests/persistence -q` with live PostgreSQL -> PASS (87 passed,
  0 skipped).
- `uv run pytest -q` with live PostgreSQL -> PASS (436 passed, 4 skipped, 86
  warnings; the 4 skips are the unrelated `--run-docker` gates).
- `uv run ruff format --check .`, `uv run ruff check --output-format concise`,
  `uv run pyrefly check` (0 errors), `uv lock --check`, `git diff --check` ->
  PASS.
- Markdown: T026 changed no file matched by the Markdown gate
  (`^(README\.md|docs/.*\.md)$` in `.pre-commit-config.yaml`, and
  `uv run pymarkdown scan README.md docs/` in `.github/workflows/test.yml`);
  `process/*.md` is outside that pattern. That command currently reports 43
  violations across `docs/`, all pre-existing: `git status` shows no docs file
  in the T026 diff. Recorded below rather than fixed here, because docs
  synchronization is T027's scope.

Security notes:
- The authentication matrix asserts the action, not only the status: rejected
  credentials produce zero protected-route executions, and only the valid
  credential produces one.
- Tenant visibility is asserted at three levels: the tenant predicate in the
  executed SQL, the recorded `(resource, scope)` pair handed to the repository,
  and the foreign row that really exists while staying invisible.
- The tenant invariant also holds when the primary enforcement is broken: a
  repository double that returns another tenant's row still produces the same
  404 through the service-side confirmation, with no protected access.
- The `/threads` and AG-UI tests show the caller claims still reaching upstream
  LangGraph scoping - the unchanged upstream trust model - while the TaskPilot
  principal, tenant, scope and responses stay derived from the opaque token.
- No response or captured structured log contains the plaintext password, the
  Argon2 hash, the raw token, or its digest.

Known limitations:
- Approval record lookup and duplicate-decision idempotency remain untestable
  because V1 has no approval records; only the frozen role gate is covered.
- The matrix keeps T025's approved route shape (scoped lookup, then role check)
  for the role-gated routes, and adds one `/tenant-confirmed/{id}` route that
  also applies the frozen `require_resource_tenant` confirmation. A future
  Task-owned route should keep the SQL predicate as the primary enforcement and
  apply the confirmation as the second line of defence, as that route does.
- The four persistence integration modules still each carry their own
  disposable-database helper. Consolidating them is a test-infrastructure
  refactor and was deliberately not part of T026.
- The "separately reviewed destructive production downgrade" half of the
  persistence row is a release-process rule; T026 asserts only that no
  application code path can migrate or downgrade.
- Pre-existing baseline: `uv run pymarkdown scan README.md docs/` reports 43
  MD012/MD022/MD032 violations in `docs/` (for example `docs/USER_GUIDE.md:36`
  and `docs/API_CONVENTIONS.md`). None are in a file T026 touched and none were
  introduced here; T027 should decide whether to fix them during the docs sync.

Learner notes:
- Problem solved: Phase 2 now has one suite that answers "does the security
  boundary actually hold end to end?" with evidence from the database and the
  real HTTP layer, instead of a collection of separated unit checks.
- Read `tests/persistence/test_security_matrix_integration.py` (start with the
  authentication matrix test and the tenant matrix test), then
  `src/service/auth_dependency.py`, `src/service/authorization.py`, and
  `OrganizationRepository.get_in_principal_tenant`.
- Key concept: a security test must assert the decision, not the status code -
  record who reached the protected body, which scope reached the database, and
  which tenant rows the SQL could see.
- Exercise: delete the `organization_id` predicate from
  `get_in_principal_tenant` and watch the tenant matrix fail while the
  401/403 cases keep passing.
- Do not worry yet about approval records, Task CRUD, or consolidating the
  disposable-database helpers.

Suggested next step: Strong Review of the T026 diff, then T027 documentation
synchronization.

### 2026-09-18 — T027: Phase 2 documentation synchronization

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-2-identity-rbac`
- Baseline HEAD: `7a5f89d test: add Phase 2 security matrix` (T026), the
  approved and committed T026 implementation.
- Working tree before changes: clean (`git status --short` produced no output).

Task Card interpretation:
- Scope: documentation only. `process/tasks/T027.md`: "Synchronize only
  architecture, database, security, API, developer/troubleshooting,
  decision/progress, and task/index docs with actual code. Document opaque
  credentials, CurrentPrincipal, role policy, tenant 404 rule, migration
  ownership/order, bootstrap, transaction lifecycle, and legacy `AUTH_SECRET`.
  Never claim unimplemented behavior."
- Forbidden: changing any T023–T026 production behavior, Phase 3 domains, new
  endpoints, schema or migrations, new dependencies, or new files. No
  production file, test, migration, dependency, or `AUTH_SECRET` behavior was
  changed.
- Conflicts/gaps: none with ADR-004. Two judgement calls are recorded. First,
  `README.md`, `ROADMAP.md`, `docs/CODE_READING_ORDER.md`, and
  `docs/USER_GUIDE.md` are not named in the card's document enumeration, but
  each contained a materially false statement about repository truth (README
  claimed a Phase 1 state with no users/organizations/RBAC; ROADMAP described a
  Phase 2 task set with different meanings than the real cards; the code reading
  order omitted every Phase 2 file; the user guide presented a login/task/
  approval workflow as available), so they were reconciled under "remove stale
  statements". Second, the
  Markdown violations in files T027 does not edit were deliberately left alone,
  because the card asks for focused Markdown checks.

Reconciliation per document:
- `README.md`: states the Phase 2 status, names what exists (four `taskpilot`
  tables, Argon2id passwords, opaque revocable sessions, server-derived
  principal, centralized authorization, tenant-scoped lookups, security
  matrix), and states plainly that no TaskPilot HTTP endpoint exists yet.
- `docs/ARCHITECTURE.md`: keeps the Phase 0/1 assessment as history behind a
  header note, updates the persistence bullet to the four business tables, marks
  the identity/RBAC gap row as Phase 2 done, and replaces the T021-only section
  with the T021–T026 status plus an explicit not-implemented list.
- `docs/DATABASE_DESIGN.md`: intro now covers T021–T023, and a new
  "Verification (T021–T026)" section records the linear revision chain,
  LangGraph coexistence, per-revision downgrade/re-upgrade, rollback,
  independent sessions, cleanup, the no-Alembic-import-in-`src/` check, and the
  disposable-database requirement.
- `docs/SECURITY_HITL.md`: stale "(decided; planned)" and "(pending T024/T025)"
  headings corrected, T026 evidence recorded, and a "Known deferrals" section
  added (no approval records, no HTTP endpoints, no Task domain, a skipped suite
  is not evidence, destructive downgrade is a separately reviewed process step).
- `docs/API_CONVENTIONS.md`: now states that no TaskPilot HTTP endpoint exists
  and that every listed path is planned; the legacy `AUTH_SECRET` guard applies
  only to the retained upstream router; the cross-tenant 404 rule is recorded as
  the frozen decision instead of an open question.
- `docs/DEVELOPER_GUIDE.md`: adds an Authorization (T025) section (helper
  contract, no role hierarchy, SQL tenant predicate, decision ordering,
  `APPROVAL_DECISION_ROLES`) and a Security matrix (T026) section with the
  disposable-database command, and corrects the Markdown-debt sentence.
- `docs/TROUBLESHOOTING.md`: adds Phase 2 entries for TaskPilot 401 causes
  (`AUTH_SECRET` is not a TaskPilot credential, expiry/revocation, revalidated
  state, wrong database), 403 versus 404 semantics, the PostgreSQL-only
  `TASKPILOT_DATABASE_URL`, skipped persistence/security suites and the safe way
  to prepare a test database, and fail-closed bootstrap.
- `docs/CODE_READING_ORDER.md`: adds the Phase 2 reading item and states that
  caller-supplied identity is never authorization truth.
- `docs/USER_GUIDE.md`: adds a status section stating that the documented login,
  task, run, and approval workflow is target behavior and none of it exists yet,
  and fixes its single Markdown list-spacing violation.
- `ROADMAP.md`: Phase 2 task list aligned with the real T020–T027 cards, marked
  complete with both exit criteria met and Phase 3 not started.
- `process/DECISION_LOG.md`: ADR-002 and ADR-003 status lines closed (the
  deferred migration-framework decision was made by ADR-004), ADR-003's known
  roadmap drift marked resolved, ADR-004 status updated, and a "Phase 2
  implementation status — closed 2026-09-18" subsection added that separates
  implemented scope from unimplemented scope ("unimplemented scope, not
  defects"). No accepted decision text was changed.
- `process/tasks/INDEX.md`: adds the Phase 2 completion status and the gates
  verified at the T026/T027 boundary.
- Markdown lint: fixed the pre-existing MD012/MD022/MD032 violations inside the
  three gated files T027 edits that carried them (`docs/SECURITY_HITL.md` 9,
  `docs/API_CONVENTIONS.md` 11, `docs/USER_GUIDE.md` 1) so the focused check
  passes.

Commands/tests run:
- `uv run pymarkdown scan README.md docs/ARCHITECTURE.md docs/DATABASE_DESIGN.md
  docs/SECURITY_HITL.md docs/API_CONVENTIONS.md docs/DEVELOPER_GUIDE.md
  docs/TROUBLESHOOTING.md docs/CODE_READING_ORDER.md docs/USER_GUIDE.md` ->
  PASS (0 violations).
- `uv run pymarkdown scan README.md docs/` -> 22 violations remain in files T027
  does not touch (`docs/AGENT_DESIGN.md` 5, `docs/CONTEXT_ENGINEERING.md` 1,
  `docs/DEPLOYMENT_RUNBOOK.md` 3, `docs/OBSERVABILITY_EVAL.md` 13). The
  baseline was 43; T027 removed 21 by fixing the three files it edits.
- `uv run pytest -q` with live PostgreSQL -> PASS (436 passed, 4 skipped, 86
  warnings) as the Phase 2 final audit; no test was changed by T027.
- `uv run ruff format --check .`, `uv run ruff check --output-format concise`,
  `uv run pyrefly check` (0 errors), `uv lock --check`, `git diff --check` ->
  PASS.

Security notes:
- The documentation now states every security rule the code actually enforces
  and no rule it does not: opaque server-side credentials, `CurrentPrincipal`
  derivation, set-based role policy without hierarchy, 401/403/404 semantics,
  SQL-level tenant predicates, bootstrap secrets handling, transaction
  lifecycle, and the compatibility-only `AUTH_SECRET`.
- No document claims an approval domain, Task domain, HTTP endpoint, permission
  table, JWT, or organization switch that does not exist.
- The docs state that the persistence and security suites skip without
  `TASKPILOT_TEST_DATABASE_URL` and that a skipped run proves nothing.

Known limitations:
- 22 pre-existing Markdown violations remain in `docs/AGENT_DESIGN.md`,
  `docs/CONTEXT_ENGINEERING.md`, `docs/DEPLOYMENT_RUNBOOK.md`,
  and `docs/OBSERVABILITY_EVAL.md`. They predate Phase 2, are in files T027 does
  not edit, and need a separate formatting-only change.
- T027 is documentation-only, so the deferred product capabilities (approval
  records, Task domain, TaskPilot HTTP endpoints, observability/audit tables)
  remain unimplemented; the documents now say so explicitly.

Learner notes:
- Problem solved: after Phase 2, the repository's own documentation no longer
  contradicts the code, and a reader cannot mistake planned endpoints or the
  approval domain for implemented behavior.
- Read `docs/SECURITY_HITL.md` (Phase 2 identity rules, T023–T026 evidence,
  deferrals) and `docs/DATABASE_DESIGN.md` (ownership, transaction boundary,
  verification) first, then `docs/API_CONVENTIONS.md` for the planned-versus-
  implemented distinction.
- Key concept: a documentation sync is an audit, not a rewrite - each sentence
  must be traceable to code, tests, or an accepted ADR, and unimplemented scope
  must be labeled as such.
- Exercise: pick one claim from `docs/SECURITY_HITL.md`, find the test that
  proves it in `tests/persistence/test_security_matrix_integration.py`, and
  confirm the doc would fail review if the test were deleted.
- Do not worry yet about the five files with leftover Markdown formatting debt.

Suggested next step: commit T027, then start Phase 3 only when authorized, with
T030 Task state-machine design. No Phase 3 work was started here.

### 2026-09-18 — Phase 3 Task domain planning

Status: PLANNING COMPLETE — no Phase 3 implementation started

What changed:
- Audited Phase 2 implementation truth: PostgreSQL TaskPilot schema, async SQLAlchemy/Alembic boundary, CurrentPrincipal and authorization helpers exist; Task/TaskRun/TaskStep, TaskPilot HTTP routes, approvals and Agent runtime domain do not exist.
- Added ADR-005 for Task domain topology, tenant/actor ownership, lifecycle state machines, Task-to-LangGraph boundary, transaction/concurrency rules, API semantics and deferrals.
- Expanded Phase 3 to T030–T039 and created implementation-ready cards with explicit dependencies, forbidden scope, migration gates, PostgreSQL integration and review gates.
- Synchronized ROADMAP, TASK_BACKLOG and task index.

Files changed:
- ROADMAP.md
- TASK_BACKLOG.md
- process/DECISION_LOG.md
- process/PROGRESS_LOG.md
- process/tasks/INDEX.md
- process/tasks/T030.md through process/tasks/T039.md

Commands/tests run:
- Baseline Git checks → PASS: phase-3-task-domain, HEAD 7b9c473, clean before planning.
- Read Phase 3 authoritative context and relevant Phase 2 persistence/auth/runtime code → PASS.
- Runtime pytest → NOT RUN: planning-only task; no runtime changes.
- Focused Markdown and diff checks recorded after edits.

Architecture/security notes:
- Task, TaskRun and TaskStep remain business records, never LangGraph checkpoint/thread/AgentState records.
- All Task-owned access derives organization scope from CurrentPrincipal; caller identity and tenant fields are not authorization truth.
- Approval, planner/executor/verifier, skills/tools/context, observability and UI remain deferred to their roadmap phases.

Known limitations:
- No Phase 3 production schema or API exists until the cards are separately authorized.

Learner notes:
- Problem solved: Phase 3 is now an executable design with explicit lifecycle and tenant boundaries rather than a list of table names.
- Read `process/DECISION_LOG.md` ADR-005, `process/tasks/T030.md`, `src/persistence/models.py`, `src/service/authorization.py`, and `src/service/session.py`.
- Key concept: a business Task can reference future agent execution without becoming a LangGraph thread.
- Exercise: draw the Task → TaskRun → TaskStep ownership chain and mark where CurrentPrincipal.organization_id must appear in SQL.
- Ignore for now: planner prompts, tools, approvals, queues, Redis and UI.

Suggested next task: T030 Strong Review. Do not start T031 until ADR-005 and its transition/API/migration gates are accepted.


## Phase 3 planning blocker fix — 2026-09-18

- Corrected the six planning blockers: Task/TaskRun source of truth and lifecycle, persistence-only cancellation, deferred TaskStep/T033, deferred application-level idempotency, single ADR-005/T030 architecture gate, and the sequential executable DAG.
- Planning files changed: ROADMAP.md, TASK_BACKLOG.md, process/DECISION_LOG.md, process/tasks/INDEX.md, and process/tasks/T030.md–T039.md.
- No production code, tests, dependencies, or migrations changed.
- Validation: `git diff --check`; stale-contract search and diff review remain to be reported with the final review.
- Learner focus: distinguish business Task state from execution-attempt TaskRun state, and distinguish domain concurrency invariants from application idempotency.

### 2026-09-18 — T031: Task schema and migration

Status: IMPLEMENTED — READY FOR STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-3-task-domain`
- Baseline HEAD: `e817df4 docs: freeze Phase 3 task domain architecture`.
- Working tree before implementation: clean.

What changed:
- Added the `Task` ORM model and `TaskStatus` enum with the frozen six-state
  user-visible lifecycle. New tasks default to `DRAFT` and contain no run.
- Added tenant and creator provenance columns as non-null PostgreSQL foreign
  keys to `organizations` and `users`, with `RESTRICT` delete behavior.
- Added non-blank title, optional description, readable lowercase status,
  UTC timestamps, explicit indexes, and PostgreSQL check constraints.
- Added the linear Alembic revision `t031_task`, registered the model in the
  metadata and migration environment, and updated the relevant architecture,
  database, and README status statements.
- No TaskRun, TaskStep, repository, lifecycle service, API, runtime, or
  application idempotency behavior was added.

Files changed:
- `src/persistence/models.py`
- `src/persistence/__init__.py`
- `migrations/env.py`
- `migrations/versions/20260918_01_task.py`
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`
- `docs/ARCHITECTURE.md`
- `README.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence/test_foundation.py -q` -> PASS (26 passed).
- `uv run pytest tests/persistence/test_postgres_integration.py -q` with live
  PostgreSQL -> PASS (19 passed, 27 warnings).
- `uv run pytest -q` with live PostgreSQL -> PASS (439 passed, 4 skipped,
  87 warnings).
- `uv run ruff format --check .` -> PASS (115 files already formatted).
- `uv run ruff check --output-format concise` -> PASS.
- `uv run pyrefly check` -> PASS (0 errors).
- `uv lock --check` -> PASS.
- `uv run alembic upgrade head` followed by `uv run alembic check` against
  PostgreSQL -> PASS; no new upgrade operations detected.
- `git diff --check` -> PASS.

Known limitations:
- The T031 card does not define a public API or lifecycle service; those stay
  with T034–T038. The schema intentionally does not contain TaskRun fields.
- Alembic requires an explicit PostgreSQL URL; application startup still does
  not auto-migrate.

Learner notes:
- Problem solved: TaskPilot now has a durable tenant-owned Task record whose
  initial state and provenance are enforced at the persistence boundary.
- Read `src/persistence/models.py`,
  `migrations/versions/20260918_01_task.py`,
  `tests/persistence/test_foundation.py`,
  `tests/persistence/test_postgres_integration.py`, and
  `docs/DATABASE_DESIGN.md`.
- Key concept: an ORM default and a database server default must describe the
  same invariant, while tenant ownership still belongs in explicit columns and
  foreign keys.
- Exercise: create one Task in a PostgreSQL transaction, query it back, then
  try inserting an invalid status and deleting its organization; observe the
  database reject both operations.
- Ignore for now: TaskRun numbering, lifecycle transitions, HTTP routes,
  planner/executor/verifier behavior, and idempotency.

Suggested next task: T031 Strong Review, then T032 TaskRun schema and migration.

### 2026-09-19 — T032: TaskRun schema and migration

Status: IMPLEMENTED — READY FOR T032 STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-3-task-domain`
- Baseline HEAD: `8f3df41 feat: add TaskPilot task persistence`.
- Working tree before T032 implementation: clean.

What changed:
- Added the `TaskRun` ORM model and `TaskRunStatus` enum for one durable
  execution attempt with `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, and
  `CANCELLED` states.
- Added per-Task positive `run_number` values with a database uniqueness
  constraint, allowing multiple terminal historical runs.
- Added a PostgreSQL partial unique index that permits at most one active
  (`PENDING` or `RUNNING`) run per Task.
- Added the linear Alembic revision `t032_task_run`, registered the model in
  metadata, and synchronized architecture, database, and README documentation.
- Added model-contract and PostgreSQL integration coverage for defaults,
  foreign keys, constraints, ordering, active-run enforcement, downgrade, and
  re-upgrade behavior.
- No TaskStep, repository/service, lifecycle orchestration, API, runtime,
  planner/executor/verifier, application idempotency, or automatic retry
  behavior was added.

Files changed:
- `src/persistence/models.py`
- `src/persistence/__init__.py`
- `migrations/env.py`
- `migrations/versions/20260919_01_task_run.py`
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`
- `docs/ARCHITECTURE.md`
- `README.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence/test_foundation.py -q` -> PASS (28 passed).
- `uv run pytest tests/persistence/test_postgres_integration.py -q` with live
  PostgreSQL -> PASS (21 passed, 32 warnings).
- `uv run pytest --basetemp .pytest-tmp-t032 -q` with live PostgreSQL -> PASS
  (442 passed, 4 skipped, 91 warnings). The default-temp run encountered
  environment permissions in `C:\Users\Lin\AppData\Local\Temp`; the explicit
  repository-local temp directory passed.
- `uv run ruff format --check .` -> PASS (116 files already formatted).
- `uv run ruff check .` -> PASS.
- `uv run pyrefly check` -> PASS (0 errors; 18 suppressed, 5 warnings).
- `uv lock --check` -> PASS.
- `uv run pymarkdown scan README.md docs/ARCHITECTURE.md docs/DATABASE_DESIGN.md` -> PASS.
- `uv run alembic upgrade head` followed by `uv run alembic check` against
  PostgreSQL -> PASS; no new upgrade operations detected.
- `git diff --check` -> PASS.

Known limitations:
- T032 provides the persistence invariant only. T035 owns legal lifecycle
  transitions and synchronization between `Task.status` and `TaskRun.status`.
- Application-level idempotency, TaskStep, repositories/services, APIs,
  runtime interruption, and automatic retries remain outside this task.
- The environment did not provide the Docker CLI; validation used the
  reachable local PostgreSQL server and the repository's existing disposable
  database integration setup.

Learner notes:
- Problem solved: TaskPilot can now persist each execution attempt, retain
  terminal history, number attempts per Task, and reject concurrent active runs
  at the database boundary.
- Read `src/persistence/models.py`,
  `migrations/versions/20260919_01_task_run.py`,
  `tests/persistence/test_foundation.py`,
  `tests/persistence/test_postgres_integration.py`, and
  `docs/DATABASE_DESIGN.md`.
- Key concept: a partial unique index expresses a conditional concurrency
  invariant directly in PostgreSQL; it is different from application
  idempotency and from lifecycle transition logic.
- Exercise: insert one pending run and one terminal run for a Task, then try
  inserting a second pending run and run number zero; observe both database
  constraints reject the invalid writes.
- Ignore for now: TaskStep, HTTP APIs, planner/executor/verifier behavior,
  retries, and application-level idempotency.

Suggested next task: T032 Strong Review, then T034 TaskRun repository/service.

### 2026-09-19 — T034: Tenant-scoped repositories and transaction boundary

Status: IMPLEMENTED — READY FOR T034 STRONG REVIEW (uncommitted)

Baseline:
- Branch: `phase-3-task-domain`
- Baseline HEAD: `1f02975 feat: add TaskPilot task run persistence`.
- Working tree before T034 implementation: clean.

What changed:
- Added `TaskRepository` primitives to add/flush Tasks, load one Task inside
  a trusted organization scope, and list Tasks inside that scope.
- Added `TaskRunRepository` primitives to add/flush runs, load a run through a
  tenant-scoped join to its Task, and list complete historical run records in
  run-number order.
- Kept the caller-owned `AsyncSession` and transaction boundary intact:
  repositories flush but never commit, rollback, close, or replace sessions.
- Added unit SQL-shape evidence and live PostgreSQL behavioral tests proving
  own-tenant visibility, foreign/nonexistent invisibility, TaskRun-through-Task
  scoping, and preserved run history.
- Updated the architecture, database design, README, and progress log to
  describe the T034 boundary.
- No lifecycle transition service, HTTP API, TaskStep, idempotency, runtime
  binding, or new dependency/framework was added.

Files changed:
- `src/persistence/repositories.py`
- `src/persistence/__init__.py`
- `tests/persistence/test_foundation.py`
- `tests/persistence/test_postgres_integration.py`
- `docs/DATABASE_DESIGN.md`
- `docs/ARCHITECTURE.md`
- `README.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/persistence/test_foundation.py -q` -> PASS (29 passed,
  1 existing pytest cache warning).
- `uv run pytest --basetemp .pytest-tmp-t034 tests/persistence/test_foundation.py tests/persistence/test_postgres_integration.py -q` with live PostgreSQL -> PASS (51 passed, 33 warnings).
- `uv run pytest --basetemp .pytest-tmp-t034 -q` with live PostgreSQL -> PASS
  (444 passed, 4 skipped, 92 warnings).
- `uv run ruff format` on changed Python files -> PASS; `uv run ruff format
  --check .` -> PASS (116 files already formatted).
- `uv run ruff check .` -> PASS.
- `uv run pyrefly check` -> PASS (0 errors; 18 suppressed, 5 warnings).
- `uv lock --check` -> PASS.
- `uv run pymarkdown scan README.md docs/ARCHITECTURE.md docs/DATABASE_DESIGN.md` -> PASS.
- `uv run alembic upgrade head` followed by `uv run alembic check` -> PASS;
  no new upgrade operations detected; `t032_task_run (head)` is the single head.
- `git diff --check` -> PASS.

Known limitations:
- T034 supplies composable persistence/query primitives only. T035 owns legal
  Task/TaskRun transitions, active-state synchronization, and lifecycle
  transactions.
- The repository-local PostgreSQL test harness is required for live tenant
  isolation evidence. Docker CLI is not available in this environment, but
  the reachable local PostgreSQL server passed the integration tests.

Learner notes:
- Problem solved: Task and TaskRun queries now enforce tenant visibility in
  SQL, so foreign and nonexistent resources are both not found at the
  repository boundary.
- Read `src/persistence/repositories.py`,
  `tests/persistence/test_foundation.py`,
  `tests/persistence/test_postgres_integration.py`,
  `src/persistence/engine.py`, and `docs/DATABASE_DESIGN.md`.
- Key concept: repository scope is a trusted server-side organization input;
  it is a SQL predicate, not a Python post-query authorization check.
- Exercise: query a foreign TaskRun ID with the local organization scope, then
  query a nonexistent ID; observe that both return `None` through the same
  production repository method.
- Ignore for now: lifecycle state transitions, HTTP status mapping, TaskStep,
  idempotency, and LangGraph runtime binding.

Suggested next task: T034 Strong Review, then T035 Task lifecycle transition service.

### 2026-09-19 — T035: Task lifecycle transition service

Status: IMPLEMENTED — READY FOR T035 STRONG REVIEW (uncommitted)

What changed:
- Added `TaskLifecycleService` with explicit start/retry, begin-running,
  success, failure, and persistence-only cancellation operations.
- Added the smallest repository locking/query primitives needed for tenant-scoped
  lifecycle decisions, serialized run-number allocation, and active-run checks.
- Added focused transition, conflict, cancellation, and fail-closed unit tests.
- Synchronized database and architecture documentation with the service boundary.

Files changed:
- `src/persistence/repositories.py`
- `src/service/task_lifecycle.py`
- `tests/service/test_task_lifecycle.py`
- `docs/DATABASE_DESIGN.md`
- `docs/ARCHITECTURE.md`
- `process/DECISION_LOG.md`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- `uv run pytest tests/service/test_task_lifecycle.py -q` -> PASS (5 passed).
- `uv run pytest tests/persistence/test_foundation.py -q` -> PASS (29 passed).
- Ruff format/check and `uv run pyrefly check` -> PASS.

Known limitations:
- T035 has no HTTP API, TaskStep, runtime interruption, automatic recovery, or
  application-level idempotency. PostgreSQL concurrency evidence remains part
  of the complete validation pass before strong review.

Learner notes:
- Problem solved: Task and its active execution attempt now move together under
  explicit, tenant-scoped lifecycle rules.
- Read `src/service/task_lifecycle.py`, `src/persistence/repositories.py`,
  `tests/service/test_task_lifecycle.py`, and `docs/DATABASE_DESIGN.md`.
- Key concept: lock the aggregate root (Task) before deciding a transition;
  the service owns domain rules and the business transaction while the caller
  owns session close.
- Exercise: add a test for retrying a failed Task and verify run #1 remains
  failed while run #2 is pending.
- Ignore for now: HTTP wiring, runtime execution, TaskStep, and idempotency.

### 2026-09-19 — T036: Task create/list/get API

Status: IMPLEMENTED — READY FOR T036 STRONG REVIEW (uncommitted)

What changed:
- Added protected `POST /api/v1/tasks`, `GET /api/v1/tasks`, and
  `GET /api/v1/tasks/{task_id}` routes using the server-derived
  `CurrentPrincipal`.
- Added typed request/response models and a small Task application service.
- Creation derives organization and creator from the principal, commits through
  the service boundary, starts in `DRAFT`, and creates no TaskRun.
- Reads use the tenant-scoped TaskRepository; foreign and nonexistent Tasks both
  return the fixed 404 response.
- Added real PostgreSQL HTTP/security coverage for authentication, spoofed
  identity fields, tenant-scoped listing, foreign/nonexistent equivalence, and
  persisted creation state.
- Updated API, architecture, README, user-guide, and progress documentation.

Files changed:
- `src/schema/task_api.py`
- `src/service/task_api.py`
- `src/service/task_service.py`
- `src/service/service.py`
- `tests/service/test_task_api_postgres.py`
- `docs/API_CONVENTIONS.md`
- `docs/ARCHITECTURE.md`
- `docs/USER_GUIDE.md`
- `docs/SECURITY_HITL.md`
- `README.md`
- `process/PROGRESS_LOG.md`

Scope remains limited to T036. No TaskRun API, Task mutation/cancellation API,
TaskStep, idempotency, runtime execution, or new dependency was added.
Task API models are imported directly from `schema.task_api`; they are
intentionally not re-exported from `schema` to keep package initialization
acyclic.

Commands/tests run:
- `uv run pytest tests/service/test_task_api_postgres.py -q` -> PASS (1 passed).
- Focused identity/lifecycle/persistence regression -> PASS (98 unit tests and
  72 PostgreSQL integration tests passed; the one combined run was separated so
  the business-database environment variable could not affect settings tests).
- `uv run pytest -q` with `TASKPILOT_TEST_DATABASE_URL` only -> PASS (455 passed,
  4 skipped).
- `uv run alembic upgrade head`, `uv run alembic check`, and import sanity
  checks -> PASS; no circular import reproduced.
- Ruff format/check, `uv run pyrefly check`, `uv lock --check`, focused
  Markdown scans, and `git diff --check` -> PASS.

Known limitations:
- Login remains a service/CLI concern; Task mutation/run APIs, TaskStep,
  idempotency, runtime execution, approvals, and observability remain later
  Phase 3/4 work.

Learner notes:
- Problem solved: authenticated callers can create and read only Tasks in their
  current organization, without trusting client-supplied identity fields.
- Read `src/service/task_api.py`, `src/service/task_service.py`,
  `src/schema/task_api.py`, `tests/service/test_task_api_postgres.py`, and
  `src/service/auth_dependency.py`.
- Key concept: the route authenticates a principal, the service owns the use
  case, and the repository enforces tenant scope in the query.
- Exercise: add a test proving a malformed UUID returns FastAPI's validation
  response without reaching the repository.
- Ignore for now: TaskRun HTTP operations, runtime graphs, and approvals.

### 2026-09-19 — T035 blocker-only fix B1–B3

Status: READY FOR FOCUSED T035 RE-REVIEW (uncommitted)

- Aligned `TaskLifecycleService` with accepted ADR-005: successful lifecycle
  operations commit atomically, failures roll back and propagate, and the
  service never closes the supplied session.
- Added deterministic two-session PostgreSQL start contention using a test-only
  pre-lock barrier; the production row-locking design remains unchanged.
- Added fresh persisted PostgreSQL assertions for both QUEUED/PENDING and
  RUNNING/RUNNING cancellation.
- Updated T035 transaction-boundary documentation and tests.

### 2026-09-19 — T037: Task update/cancel API

Status: IMPLEMENTED — READY FOR T037 STRONG REVIEW (uncommitted)

What changed:
- Added `PATCH /api/v1/tasks/{task_id}` for mutable `title` and `description`
  fields only; server-owned identity and lifecycle fields are ignored or
  rejected by the request model.
- Added `POST /api/v1/tasks/{task_id}/cancel`, delegating persistence-only
  cancellation to the T035 `TaskLifecycleService`.
- Applied the frozen tenant/role policy: admins manage any Task in their active
  organization; members manage only Tasks they created; owners do not inherit
  admin task permissions.
- Mapped foreign/nonexistent resources to 404, same-tenant insufficient access
  to 403, and lifecycle conflicts/inconsistent persisted states to a stable 409.
- Added real PostgreSQL HTTP tests for authentication, ownership/role policy,
  tenant isolation, update persistence, draft/queued/running cancellation,
  repeated cancellation, and terminal conflicts.
- Updated API, architecture, security, README, user-guide, and progress docs.

Files changed:
- `src/schema/task_api.py`
- `src/service/authorization.py`
- `src/service/task_api.py`
- `src/service/task_service.py`
- `tests/service/test_task_api_t037_postgres.py`
- `README.md`
- `docs/API_CONVENTIONS.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_HITL.md`
- `docs/USER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Scope remains limited to T037. No TaskRun API, TaskStep, idempotency,
runtime/LangGraph execution, or lifecycle state machine outside T035 was added.

Known limitations:
- Cancellation remains persistence-only and does not interrupt external work.
- Login, later TaskRun runtime operations, TaskStep, approvals, runtime
  execution, and application-level idempotency remain deferred.

Learner notes:
- Problem solved: authenticated users can update permitted Task metadata and
  cancel Tasks without bypassing tenant or lifecycle rules.
- Read `src/service/task_api.py`, `src/service/task_service.py`,
  `src/service/task_lifecycle.py`, `src/service/authorization.py`, and
  `tests/service/test_task_api_t037_postgres.py`.
- Key concept: authorization first resolves a tenant-scoped resource, then
  applies same-tenant policy; lifecycle mutation remains owned by T035.
- Exercise: add a test proving a member cannot update or cancel another
  member's Task while an admin can.
- Ignore for now: TaskRun HTTP routes, runtime interruption, and idempotency.

### 2026-09-19 — T038: TaskRun start/inspect API

Status: IMPLEMENTED — READY FOR T038 STRONG REVIEW (uncommitted)

What changed:
- Added `POST /api/v1/tasks/{task_id}/runs` for tenant-scoped start/retry.
- Added `GET /api/v1/tasks/{task_id}/runs/{run_id}` for tenant-scoped persisted
  TaskRun inspection.
- Delegated all Task/TaskRun lifecycle state changes, run numbering, row locks,
  transaction commit/rollback, and retry legality to the T035 lifecycle service.
- Allowed start only from `DRAFT` or `FAILED`; illegal and inconsistent states
  map to a stable HTTP 409 response.
- Added a TaskRun response containing only persisted identity, task ID, run
  number, status, and timestamps; no runtime metadata or idempotency contract.
- Added real PostgreSQL HTTP coverage for authentication, tenant isolation,
  foreign/nonexistent equivalence, DRAFT start, FAILED retry history, illegal
  restart states, and TaskRun response shape.
- Updated API, architecture, security, README, user-guide, and progress docs.

Files changed:
- `src/persistence/repositories.py`
- `src/schema/task_run_api.py`
- `src/service/task_api.py`
- `src/service/task_run_service.py`
- `tests/service/test_task_run_api_postgres.py`
- `README.md`
- `docs/API_CONVENTIONS.md`
- `docs/ARCHITECTURE.md`
- `docs/SECURITY_HITL.md`
- `docs/USER_GUIDE.md`
- `process/PROGRESS_LOG.md`

Scope remains limited to T038. TaskStep, runtime execution, planner/executor/
verifier behavior, application-level idempotency, and external side effects
remain deferred.

Known limitations:
- T038 persists lifecycle state only; it does not invoke an LLM or interrupt
  external work.
- Login, TaskStep, approvals, runtime execution, and application-level
  idempotency remain deferred.

Learner notes:
- Problem solved: authenticated callers can start/retry tenant-visible Tasks
  and inspect persisted TaskRun history without bypassing T035 lifecycle rules.
- Read `src/service/task_api.py`, `src/service/task_run_service.py`,
  `src/service/task_lifecycle.py`, `src/persistence/repositories.py`, and
  `tests/service/test_task_run_api_postgres.py`.
- Key concept: API code adapts HTTP to the lifecycle service; it does not own
  state transitions, run numbering, locking, or transaction commits.
- Exercise: add a concurrent HTTP start test proving one winner and one 409
  after the existing domain-level concurrency test.
- Ignore for now: TaskStep, runtime graphs, tool execution, and idempotency.

### 2026-09-19 — Phase 3 Final Audit B1: stale locked Task refresh

Status: FIXED — READY FOR FOCUSED PHASE 3 RE-AUDIT (uncommitted)

What changed:
- Reproduced the audit interleaving against PostgreSQL: one Session preloaded a
  `DRAFT` Task, another Session committed `QUEUED` plus a `PENDING` TaskRun,
  and cancellation in the first Session incorrectly committed `CANCELLED`
  while leaving the run `PENDING`.
- Added `populate_existing=True` to the tenant-scoped Task `SELECT ... FOR
  UPDATE`, so every T035 lifecycle branch uses the current database state from
  the locked row even when that Task identity was already loaded.
- Added a deterministic PostgreSQL regression using independent Sessions and a
  fresh final database read. It asserts one historical cancelled run and no
  `PENDING` or `RUNNING` run after cancellation.

Files changed:
- `src/persistence/repositories.py`
- `tests/service/test_task_lifecycle_postgres.py`
- `process/PROGRESS_LOG.md`

Commands/tests run:
- Pre-fix focused B1 test -> expected FAIL: fresh state was Task `CANCELLED`,
  Run `PENDING`.
- Post-fix focused B1 test -> PASS (1 passed).
- Affected T035/T036/T037/T038 and persistence regression -> PASS (67 passed).
- Full pytest with `TASKPILOT_TEST_DATABASE_URL` -> PASS (460 passed, 4
  skipped, 102 warnings); mandatory Phase 3 PostgreSQL tests executed.
- Ruff tracked-Python format check and lint -> PASS (127 tracked files already formatted,
  all checks passed). Direct repository-root traversal remains affected by the
  pre-existing inaccessible `.pytest-tmp-*` directories.
- Pyrefly -> PASS (0 errors; 18 suppressed, 6 warnings not shown).
- `uv lock --check` -> PASS.
- Alembic heads/history and fresh disposable PostgreSQL upgrade/current/check
  -> PASS; `t032_task_run` is the single head and no schema drift was found.
- Direct `schema`, `schema.task_api`, `schema.task_run_api`, and application
  imports with the repository's `src` layout -> PASS.

Security/transaction review:
- Tenant predicates and T037 admin/member/owner authorization are unchanged.
- Repositories still query/add/flush only; `TaskLifecycleService` remains the
  sole lifecycle commit/rollback owner and the outer dependency owns close.
- No migration, API, dependency, runtime, recovery, idempotency, or distributed
  locking change was introduced.

Known limitations:
- Cancellation remains persistence-only and does not interrupt external work,
  as required by ADR-005. No B1-related blocker remains.

Learner notes:
- Problem solved: a database row lock does not itself refresh an ORM object
  already present in a Session; cancellation now branches on locked database
  truth instead of stale identity-map state.
- Read `src/persistence/repositories.py`, `src/service/task_lifecycle.py`,
  `src/service/task_service.py`, and
  `tests/service/test_task_lifecycle_postgres.py`.
- Key concept: `SELECT ... FOR UPDATE` serializes access, while
  `populate_existing` separately repopulates an existing ORM identity.
- Exercise: temporarily remove `populate_existing`, run the focused B1 test,
  and inspect the final Task and TaskRun statuses; then restore the line.
- Do not worry yet about TaskStep, runtime interruption, application
  idempotency, distributed locks, or automatic recovery.

Suggested next task: focused Phase 3 Final Audit B1 re-audit only.
