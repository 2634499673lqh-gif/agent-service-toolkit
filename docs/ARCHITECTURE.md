# Architecture — Phase 0 baseline (2026-09-10)

This document keeps the Phase 0/1 assessment below as history. The current implementation status is recorded in "Current TaskPilot implementation status" at the end; earlier sections describe the runtime as it was when they were written.

## What is actually present now

This repository is an upstream `agent-service-toolkit` application: a LangGraph agent demo/service with FastAPI and a Streamlit chat UI. TaskPilot planning documents were added around it, but TaskPilot's product domain has not been implemented. The source of truth for this assessment is the code under `src/`, not the planning documents.

```mermaid
flowchart LR
    UI[Streamlit: src/streamlit_app.py] --> C[AgentClient]
    C --> API[FastAPI: src/service/service.py]
    API --> Registry[src/agents/agents.py registry]
    Registry --> Graph[Compiled LangGraph agent]
    Graph --> Tools[Web search / calculator / Chroma / MCP examples]
    API --> CP[LangGraph checkpointer]
    API --> Store[LangGraph Store]
    CP --> SQLite[(SQLite default)]
    CP --> PG[(PostgreSQL optional)]
    CP --> Mongo[(MongoDB optional)]
    Store --> Mem[InMemory default / PostgreSQL]
    Graph --> LLM[Configured LLM provider]
    API -. optional .-> Trace[LangSmith / Langfuse]
```

### Request flow

1. `src/run_service.py` loads `.env`, then starts Uvicorn with `service:app`.
2. `src/service/service.py` creates `app`; lifespan calls `memory.initialize_database()` and `initialize_store()`, assigns both to every registered graph, and lazy-loads the GitHub MCP graph.
3. `POST /invoke` or `POST /{agent_id}/invoke` validates `schema.UserInput`, selects a graph from `src/agents/agents.py`, and invokes it with LangGraph `thread_id`/caller-supplied-or-random `user_id`. `/stream` uses the same input construction and returns SSE; `/agui/*/run` provides AG-UI SSE.
4. The graph executes. For the default `research-assistant`, the path is input safeguard -> model -> optional `ToolNode` -> model -> end.
5. A checkpointer persists graph state by `thread_id`; `/history` and `/threads` read it. This is conversation persistence, not TaskPilot task/run/step storage.

### Request correlation

The FastAPI app installs one HTTP middleware for the complete request lifecycle. It
generates a fresh UUID4, stores it on `request.state.request_id` for downstream
handlers, and returns the same value in the `X-Request-ID` response header. Client
supplied `X-Request-ID` values are intentionally ignored, so callers cannot spoof a
server correlation value. This is request-scoped transport metadata only; it does
not create TaskPilot trace, run, task, or tool records. Structured logging and
redaction are deferred to T014.

`AUTH_SECRET` is a single optional shared bearer secret. It protects the router when set, but does not establish an authenticated user, organization, role, or tenant. `user_id` is request data and `/threads` documents that it is caller asserted; it must not be mistaken for TaskPilot authorization.

## Reusable upstream capabilities

| Capability | Real implementation | Reuse assessment |
| --- | --- | --- |
| FastAPI transport, JSON/SSE streaming, AG-UI | `src/service/service.py`, `src/service/agui.py`, `src/schema/schema.py` | Keep as transport starting point; add versioned TaskPilot APIs later. |
| Agent registry and lazy graph loading | `src/agents/agents.py`, `src/agents/lazy_agent.py`, `src/agents/github_mcp_agent/` | Keep registry pattern; do not treat demo graphs as TaskPilot roles. |
| LangGraph graph examples | research/RAG/interrupt/supervisor modules | Keep LangGraph; add a bounded TaskPilot graph only in Phase 4. |
| Checkpoint and long-term Store adapters | `src/memory/{__init__,sqlite,postgres,mongodb}.py` | Reuse adapters after deciding TaskPilot persistence boundaries. |
| Existing RAG examples | `src/agents/tools.py`, `rag_assistant.py`, `scripts/create_chroma_db.py`, `knowledge_base_agent.py` | Prototype only: no tenant filtering, provenance contract, or upload lifecycle. |
| Interrupt/resume primitive | `src/agents/interrupt_agent.py`, service `_handle_input` | Reuse technical primitive, not the birthdate demo; TaskPilot needs policy, approvals and exactly-once effects. |
| Tracing/feedback hooks | `src/service/service.py`, `src/service/agui.py` | Optional Langfuse callbacks/LangSmith feedback, not TaskPilot trace/audit truth. |
| Streamlit UI/client | `src/streamlit_app.py`, `src/client/client.py` | Keep for V1 development/demo; evolve after stable task APIs. |
| Tests and containers | `tests/`, `compose.yaml`, Dockerfiles, CI | Preserve; add deterministic TaskPilot tests phase by phase. |

## Persistence reality

- SQLite checkpoint: lightweight local-development checkpoint; the long-term store is process-local `InMemoryStore` and is not durable across restarts.
- PostgreSQL LangGraph persistence: `AsyncPostgresSaver` and `AsyncPostgresStore` create and upgrade library-managed checkpoint/Store schemas via `setup()`.
- MongoDB: optional checkpointer only (`docker/compose.mongo.yaml`); no Mongo Store.
- At the Phase 1 baseline there were no SQLAlchemy models, Alembic migrations, application business tables, or `Task`, `TaskRun`, `TaskStep`, user, organization, role, approval, trace-event or evaluation records. Phase 2 (T021–T023) added the identity tables `organizations`, `users`, `memberships`, and `auth_sessions`; T031 and T032 now add the persistence-only `tasks` and `task_runs` tables. `src/schema/task_data.py` remains Streamlit background-task display data, not the TaskPilot domain.

## Current feature inventory and TaskPilot V1 gap

| Requirement | Existing support / reusable code | Missing work and risk | Phase |
| --- | --- | --- | --- |
| Identity/RBAC/tenant isolation | Phase 2 implemented: `taskpilot` schema, opaque sessions, server-derived `CurrentPrincipal`, centralized authorization, tenant-scoped lookups, security matrix | T036/T037 Task APIs, T038 TaskRun APIs, T081/T082 Approval persistence and protected decision APIs, and T083 internal approval boundary | 2 (done) / 3 / 6 |
| Task/run/step lifecycle | T031/T032 persistence foundations, T034 tenant-scoped repositories, T035 lifecycle service, T037 cancellation API, T038 start/inspect integration, and the T040–T050 internal runtime | TaskStep schema, public runtime API, idempotency | 3/4 |
| Planner/executor/verifier/recovery | T040–T050 typed AgentState, Planner → Executor → Verifier runtime, bounded retry/replan, and verification | External tools/providers, broader context and recovery capabilities | 4/5 |
| Checkpoint/resume | T050 LangGraph checkpoint/resume bound to a tenant-validated durable TaskRun | Worker/queue orchestration, HITL resume, and external-effect guarantees | 4/6 |
| Skills/tools/context | Web/calculator, Chroma, Bedrock examples | Versioned contracts, tenant-safe retrieval, file lifecycle, budgets/provenance | 5 |
| Human approval | T081 Approval persistence, T082 protected reads/decisions, T083 server-side classification and approval pause/resume; accepted ADR-008 | T084 action claim and bounded mock effect | 6 |
| Observability/audit | Logging, run UUID, optional Langfuse/LangSmith | Correlated TaskPilot IDs, sanitized events, metrics and audit truth | 7 |
| Evaluation | Unit/integration/smoke tests, fake model | Versioned deterministic task evals and safety/workflow metrics | 8 |
| Product UI | Streamlit chat, threads, voice/feedback | Login, tasks/runs/steps/traces/approvals/files; retain Streamlit first | 9 |

## Architecture delta and Phase 1 boundary

The smallest safe path is additive: preserve the upstream service, LangGraph, checkpointer adapters, Streamlit app and upstream reference files. Phase 1 should only make local development reproducible and establish conventions: audit `.env.example`, document verified commands, add request correlation middleware, add secret-safe structured logging in T014, and decide/verify a migration baseline for future TaskPilot tables. Do not add users, Task/TaskRun/TaskStep, Planner/Executor/Verifier, Redis/Kafka/Kubernetes, or a new frontend in Phase 1.

### Known unknowns

1. Verify in Phase 1 that LangGraph's Postgres tables can share the instance with future TaskPilot migration-managed tables.
2. The dependency-locked test/Docker baseline was not runnable on this host because `uv`, Docker and project dependencies are absent; reproduce after `uv sync --frozen` in a writable clone.
3. Decide in Phase 2 whether `AUTH_SECRET` remains a development-only compatibility mechanism; it is inadequate for end-user authorization.

## Phase 2 identity architecture (T021–T026 implemented)

ADR-004 freezes a PostgreSQL-only TaskPilot business schema in the `taskpilot` namespace, separate from LangGraph-owned tables. Identity is User + Organization + Membership, with one active membership bound to each opaque session. Authorization derives only from the server-resolved principal; upstream `AUTH_SECRET` and caller-supplied conversation IDs remain compatibility-only.

Bootstrap order is PostgreSQL, TaskPilot Alembic migrations, LangGraph saver/store `setup()`, then application startup. Production startup does not run migrations. SQLAlchemy 2.x async sessions are request-scoped, services own transactions, repositories do not commit, and ORM sessions never enter LangGraph `AgentState`. See ADR-004 and T021–T027 for implementation boundaries.

Implemented and reviewed:

- T021 provides `src/persistence/` with TaskPilot-owned SQLAlchemy metadata, an async psycopg engine/session factory, and the Organization repository. T034 adds tenant-scoped Task/TaskRun repositories; TaskRun queries derive tenant ownership through an explicit SQL join to Task. Alembic uses `migrations/env.py` and `taskpilot` schema version metadata; its `include_name` pre-reflection filter plus defensive `include_object` filter exclude public/LangGraph tables from autogenerate.
- T022/T022A/T023 add `users`, `memberships`, and `auth_sessions` through four linear revisions (`t021_organization`, `t022_user`, `t022a_membership`, `t023_auth_session`), with Argon2id password hashes, a canonical `normalized_email`, the frozen `owner|admin|member` role set, and SHA-256 session-token digests.
- T023 login issues opaque 24-hour sessions bound to one user and one membership, with generic credential failure and a controlled CLI bootstrap. T024 resolves the request-scoped `CurrentPrincipal` and re-reads all state per request. T025 provides the single authorization boundary with fixed 401/403/404 semantics. T026 supplies the authoritative negative security matrix.

Business persistence is enabled only by an explicit PostgreSQL `TASKPILOT_DATABASE_URL`; the existing `DATABASE_TYPE` remains the upstream LangGraph backend selector. Run `alembic upgrade head` as a release step, never from application startup.

Not implemented: TaskPilot login/`/me` endpoints, TaskStep records, permission or role tables, JWT/refresh tokens, organization-switch endpoints, and TaskPilot observability tables. T031/T032/T034 provide Task and TaskRun persistence, T035 provides the explicit lifecycle service, T036/T037 provide tenant-safe Task routes, T038 provides tenant-scoped TaskRun routes, and T081/T082 provide Approval persistence and protected nested read/decision routes. `ApprovalService.create_or_reuse` is internal to trusted runtime wiring. T040–T050 provide the committed internal bounded runtime described below. Phase 4 adds no TaskStep persistence, public runtime HTTP endpoint, worker, HTTP idempotency, or real external side effect. `tests/persistence` and the TaskPilot security suites need a disposable PostgreSQL test database and skip without one.

## Current TaskPilot implementation status — Phase 6 in progress

The committed Phase 4 implementation covers T040–T050 and T051 Final Audit is approved. Phase 4 is complete and merged to `main`. T060–T064 are implemented, Strong Review approved, committed, and pushed; Phase 5 implementation is complete and the Phase 5 Final Audit is approved. The initial audit returned NOT APPROVED solely because canonical status documentation was stale; the focused re-review subsequently approved Phase 5, which is complete.

Phase 6 Planning is approved, frozen, committed, and published. T080 is
complete and approved; T081 Approval persistence is complete with Strong Review
approval, committed, and pushed. T082 Approval service and decision APIs are
complete, Strong Review approved, committed, and pushed to origin. T083 runtime
approval boundary is complete with focused Strong Re-review approved; its
implementation remains uncommitted. T084 action claim/effect work has not
started and awaits independent Strong Review.

The implemented runtime is an internal, deterministic LangGraph topology:

- Planner → Executor → Verifier over typed, checkpoint-serializable `AgentState`.
- One bounded retry and one bounded replan, with terminal behavior on budget
  exhaustion or unrecoverable failure.
- Checkpoint/resume for the same tenant-validated `TaskRun`, using a
  correlation-only `taskpilot-run:<task_run_id>` checkpoint thread.
- Task/TaskRun lifecycle integration through T035; runtime code does not directly
  mutate lifecycle status.
- Tenant-scoped business validation before runtime/checkpoint use. TaskPilot
  business persistence and LangGraph checkpoint ownership remain separate.

The following boundaries remain deferred: persistent TaskStep, a public runtime
HTTP API, worker/queue execution, action claim/effect handling, real external
tools or providers, and any production
deployment claim. Checkpoint or graph progress never grants authorization,
and Phase 4 makes no exactly-once execution guarantee.


### Phase 5 implementation boundary

The implemented Phase 5 package (T060–T064, following approved T051) adds one bounded Capability contract and an explicit in-process dispatch dependency to the existing Executor. The first capability remains deterministic, read-only, and side-effect-free. A typed, provenance-labeled ContextEnvelope is sanitized and size-bounded; authority, secrets, repositories, ORM objects, provider clients, and checkpoint objects remain outside AgentState and checkpoints. No new persistence or public runtime API has been introduced.
