# TaskPilot Roadmap

> Principle: vertical slices before feature breadth. Each phase must leave the repository runnable.

## Phase 0 — Repository Assessment [STRONG MODEL]

Goal: understand the upstream/base repository and produce an explicit delta plan.

Tasks:
- T000 baseline inventory
- T001 run current tests/build
- T002 map request/agent/data flow
- T003 architecture delta ADR and gap analysis

Exit:
- no major speculative refactor
- baseline commands documented
- code reading order exists
- architecture delta reviewed

## Phase 1 — Foundation & Documentation [ECONOMY/STANDARD]

Goal: stable config, documentation discipline, migrations, logging IDs.

Tasks:
- T010 repository branding and attribution
- T011 environment template audit
- T012 settings validation
- T013 request correlation ID
- T014 structured logging redaction
- T015 migration baseline verification
- T016 developer command documentation

Exit:
- local startup reproducible
- tests reproduce upstream baseline
- progress/decision logs active

## Phase 2 — Identity, Organization, RBAC [STRONG DESIGN + STANDARD IMPLEMENTATION]

Status (2026-09-18): complete. The cards below were renumbered during planning; `process/tasks/` and `TASK_BACKLOG.md` are authoritative and this list reflects them.

Tasks:

- T020 identity, tenancy, authentication and persistence architecture gate (ADR-004, accepted)
- T021 SQLAlchemy/Alembic foundation and Organization schema
- T022 User identity schema
- T022A Membership schema, roles and repositories
- T023 opaque session/token service, Argon2id login and controlled bootstrap
- T024 `CurrentPrincipal` request dependency
- T025 central authorization boundary and tenant policy
- T026 negative authentication, tenant, transaction and migration test matrix
- T027 security/API/database documentation sync

Exit:

- two users in different orgs cannot read each other's resources
- negative auth tests exist

Both exit criteria are met by the T025 authorization boundary and the T026 live-PostgreSQL matrix. No TaskPilot HTTP endpoint, Task domain, or approval domain exists yet; those stay in Phase 3 and later.

## Phase 3 — Task Domain [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T030 Task domain/lifecycle architecture decision (ADR-005; single Planning Strong Review gate)
- T031 Task schema/migration
- T032 TaskRun schema/migration
- T033 TaskStep persistence deferred to Phase 4 runtime design
- T034 tenant-scoped repositories and transaction boundary
- T035 lifecycle transition service
- T036 Task create/list/get API
- T037 Task update/cancel API
- T038 TaskRun start/inspect API
- T039 Phase 3 integration/security tests and documentation

Exit:
- task lifecycle works without LLM
- invalid transitions rejected
- same-tenant CRUD works and cross-tenant resources return 404
- insufficient in-tenant role returns 403
- PostgreSQL migration and rollback/coexistence checks pass

## Phase 4 — Agent Runtime [STRONG MODEL]

Tasks (canonical IDs; ADR-006/T040 is the planning gate):
- T040 Phase 4 runtime architecture / AgentState contract
- T041 Planner schema
- T042 Planner node
- T043 Executor interface
- T044 deterministic execution path
- T045 Verifier schema
- T046 Verifier node
- T047 failure classifier
- T048 bounded retry
- T049 bounded replan
- T050 checkpoint / resume
- T051 Phase 4 Final Audit (read-only)

Recommended order: `T040 → T041 → T042 → T043 → T044 → T045 → T046 → T047 → T048 → T049 → T050 → T051`.
Phase 4 is an internal runtime service, not a new HTTP API. `POST /api/v1/tasks/{task_id}/runs` retains T038 creation/start semantics; the runtime consumes a tenant-validated TaskRun through an internal service entry point. Task and TaskRun enums remain those frozen by ADR-005. TaskStep persistence, HITL, HTTP idempotency, worker queues, and real external effects remain deferred.

Exit:
- one complete task can execute through graph
- a forced retry is visible and recoverable

## Phase 5 — Skills, Tools, Context [STRONG DESIGN + STANDARD/ECONOMY IMPLEMENTATION]

Tasks: see the canonical backlog IDs T060–T074.

Exit:
- planner selects a skill
- tool calls are typed/traced
- context sources can be explained

## Phase 6 — Human-in-the-loop & Safety [STRONG MODEL]

Tasks: see the canonical backlog IDs T080–T088.

Exit:
- L2 action cannot execute before approval
- duplicate approval/action cannot duplicate effect

## Phase 7 — Observability [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T090 trace data model decision
- T091 AgentRun persistence
- T092 ToolCall persistence
- T093 correlation propagation
- T094 latency/status/error metrics
- T095 token usage adapter
- T096 cost estimator
- T097 trace query API
- T098 redaction tests

Exit:
- one task can be reconstructed from trace
- failure point and retry are visible

## Phase 8 — Evaluation [STRONG DESIGN + ECONOMY IMPLEMENTATION]

Tasks:
- T100 eval schema decision
- T101 deterministic fixture set
- T102 workflow eval runner
- T103 metrics calculation
- T104 machine-readable report
- T105 human-readable report
- T106 CI cheap smoke eval

Exit:
- same eval suite can be rerun
- results are versioned/comparable

## Phase 9 — Product UI [STANDARD/ECONOMY]

Tasks:
- T110 existing UI gap assessment
- T111 task create form
- T112 task list/detail
- T113 run/step timeline
- T114 approval queue/detail
- T115 trace timeline
- T116 error/loading states

First stabilize existing Streamlit UI.
Optional second step: add Next.js/React when APIs are stable.

Views:
- login
- task create
- task list/detail
- step/trace timeline
- approval queue
- knowledge/file upload
- eval/observability summary

Exit:
- demo can be used without Postman

## Phase 10 — Concurrency & Deployment Hardening [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T120 concurrency architecture review
- T121 DB pool/transaction review
- T122 background worker integration (only if approved)
- T123 rate limiting/backpressure
- T124 health/readiness
- T125 Docker Compose hardening
- T126 CI pipeline
- T127 concurrency smoke test

Exit:
- multiple simultaneous demo users do not corrupt state
- startup/health/recovery documented

## Phase 11 — Documentation, Demo, Final Audit [STRONG REVIEW + ECONOMY DOC WORK]

Tasks:
- T130 User Guide audit
- T131 Developer Guide audit
- T132 Code Reading Order audit
- T133 Architecture/security audit
- T134 Critical/high fixes
- T135 Demo script/data
- T136 Final eval/test report

Exit:
- a new learner can clone, start, understand, and demo the project from docs alone

## Historical compact-roadmap mapping (not live task assignments)

The following old roadmap labels are retained only to preserve planning
history; the canonical live IDs are those listed above and in
`TASK_BACKLOG.md`:

| Old roadmap ID | Canonical ID(s) | Preserved intent |
|---|---|---|
| T004 | T003 | Gap analysis and architecture decision folded into the architecture ADR |
| T050 | T060 | Skill manifest decision |
| T051 | T062 | Research skill |
| T052 | T063 | Document-analysis skill |
| T053 | T064 | Tabular-analysis skill |
| T054 | T065 | Typed tool interface |
| T055 | T071 | Context builder |
| T056 | T073 | User memory |
| T057 | T074 | Organization knowledge retrieval |
| T058 | T070, T072 | Split into ContextEnvelope schema and context budget trimming |
| T060 | T080 | Risk policy |
| T061 | T081, T082, T083, T084 | Split into approval persistence, service, list/detail, and decision APIs |
| T062 | T085 | LangGraph interrupt integration |
| T063 | T086 | Resume after approval |
| T064 | T087 | Approved side-effect idempotency |
| T065 | T088 | HITL audit events |
| T070 | T090 | Trace data model decision |
| T071 | T093 | Correlation propagation |
| T072 | T094 | Latency/status/error metrics |
| T073 | T095 | Token usage adapter |
| T074 | T097 | Trace query API |
| T075 | T098 | Redaction tests |
| T080 | T100 | Evaluation schema decision |
| T081 | T101 | Deterministic fixture set |
| T082 | T102 | Workflow evaluator |
| T083 | T103 | Metrics calculation |
| T084 | T104 | Machine-readable regression report |
| T085 | T106 | CI smoke evaluation |
| T100 | T120 | Concurrency architecture review |
| T101 | T122 | Background worker decision/integration |
| T102 | T123 | Rate limiting/backpressure |
| T103 | T124 | Health/readiness |
| T104 | T125 | Docker Compose hardening |
| T105 | T126 | CI pipeline |
| T106 | T121, T123 | Split into DB pool/transaction review and security/backpressure hardening |
| T107 | T127 | Concurrency smoke test |
| T110 | T130 | User Guide audit |
| T111 | T131 | Developer Guide audit |
| T112 | T132 | Architecture/code-reading documentation |
| T113 | T132 | Code Reading Order audit |
| T114 | T131 | Troubleshooting/developer runbook documentation |
| T115 | T135 | Demo script/data |
| T116 | T133 | Security audit |
| T117 | T133 | Architecture audit |
| T118 | T136 | Final test/evaluation report |
