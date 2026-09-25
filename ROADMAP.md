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

## Phase 5 — Skills, Tools, Context [COMPLETE]

Phase 4/T051 is approved, complete, and merged to `main`. T060–T064 are implemented, Strong Review approved, committed, and pushed. Phase 5 implementation is complete and the Phase 5 Final Audit is approved; Phase 5 is complete. The initial audit returned NOT APPROVED solely because canonical status documentation was stale, and the focused re-review subsequently approved Phase 5. Canonical tasks: `T060 → T061`, then `T061 → T062` and `T061 → T063`, then `T062 + T063 → T064`.

Exit: one deterministic read-only capability is selected and dispatched through the existing Executor; capability context is typed, bounded, provenance-labeled, sanitized, and explainable; retry/replan/checkpoint/resume and tenant boundaries remain unchanged.

No real external effects, credentials, dynamic registry/plugin platform, new persistence, public runtime API, HITL, memory/knowledge subsystem, or exactly-once guarantee is included.
## Phase 6 — Human-in-the-loop & Safety [COMPLETE; FINAL AUDIT APPROVED]

Canonical DAG: `T080 → T081 → T082 → T083 → T084 → T085`.
T080 accepted and froze ADR-008; T081–T084 are the smallest implementation
slice; T085 is the independent Phase 6 Final Audit. T086–T088 are retired as
standalone cards because their behavior is covered by T083/T084.

Phase 6 Planning is APPROVED, frozen, committed, and published. T080 Strong
Review is APPROVED; ADR-008 is Accepted and its contract is frozen.

T081 is COMPLETE and Strong Review approved, committed, and pushed. T082 is
COMPLETE with Strong Review APPROVED, committed, and pushed to origin. T083 is COMPLETE; Strong Review APPROVED; committed and pushed. T084 is COMPLETE; Strong Review APPROVED; committed and pushed. The initial T085 Final Audit returned NOT APPROVED for a documentation-only status blocker; after the fix, focused Final Audit re-review APPROVED T085. Phase 6 is COMPLETE. Phase 7 Planning is APPROVED; ADR-009 is accepted/frozen; T090–T097 are COMPLETE / APPROVED; T098 Phase 7 Final Audit is APPROVED; Phase 7 is COMPLETE. Phase 8 Planning is APPROVED / frozen; implementation is NOT STARTED; T101 is next.

Pre-planning HEAD `a2cad16` already reserved Phase 6 T080–T088 in backlog and
roadmap; the T064-to-T080 gap requires no renumbering. ADR-008 B1–B3 are the
persistence, request-identity, and atomic mock-outcome contract.

Exit:
- L2 action cannot execute before approval and L3 is blocked;
- owner/admin decisions are tenant-scoped, immutable, and auditable;
- checkpoint/resume and cancellation races fail closed;
- duplicate approval or approved mock delivery cannot duplicate the effect;
- no generic policy/workflow engine, credentials, worker platform, or real
  external side effect is introduced.

## Phase 7 — Observability [COMPLETE; FINAL AUDIT APPROVED]

Planning authority: ADR-009. DAG: `T090 → (T091, T092) → T093 → (T094, T095) → T096 → T097 → T098`; T098 is the read-only Phase 7 Final Audit.

T090–T097 implementation and review work is COMPLETE / APPROVED. T098 Phase 7
Final Audit is APPROVED. Phase 7 is COMPLETE. Phase 8 Planning is APPROVED / frozen; implementation is NOT STARTED; T101 is next.

Tasks:
- T090 trace data model decision
- T091 AgentRun persistence
- T092 ToolCall persistence
- T093 correlation propagation
- T094 latency/status/error metrics
- T095 token usage adapter
- T096 cost estimator
- T097 trace query API
- T098 Phase 7 Final Audit (fresh-eyes, independent, read-only)

Exit:
- one task can be reconstructed from trace
- failure point, retry, and replan are visible; T092 owns persisted-payload
  redaction tests and T097 owns timeline-response redaction tests

## Phase 8 — Evaluation [PLANNING GATE AWAITING STRONG REVIEW; IMPLEMENTATION NOT STARTED]

T100 is the Phase 8 Evaluation architecture/planning gate. Planning Strong Review is APPROVED; ADR-010 is Accepted/frozen, T100 is COMPLETE / APPROVED, and implementation begins at T101.

Tasks:
- T100 Evaluation architecture/planning gate
- T101 deterministic existing-capability baseline and remaining fixtures
- T102 workflow Evaluation runner
- T103 deterministic metrics
- T104 machine-readable report
- T105 human-readable report
- T106 CI cheap smoke Evaluation
- T107 Phase 8 Final Audit (fresh-eyes, independent, read-only)

Exit:
- the same five-case Evaluation suite can be rerun;
- each run has deterministic metrics and explicit cross-run comparability;
- report artifacts are bounded, versioned, and redacted.

Batches:
- Planning gate: **T100**.
- Implementation Batch 1: **T101–T103** — deterministic fixtures, workflow runner, deterministic metrics.
- Implementation Batch 2: **T104–T106** — machine report, human report, CI smoke.
- Phase Final Audit: **T107** — independent, fresh-eyes, read-only.

DAG: `T100 → T101 → T102 → T103 → T104 → T105`; `T101 + T102 + T103 + T104 → T106`; `T100–T106 → T107`. No tabular/sum capability is planned; the baseline reuses the existing `DeterministicFixtureCapability`. No Evaluation database, UI, live LLM-as-judge, remote SaaS, or automatic production gating is in Phase 8.

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

This table records the older mapping, including the former Phase 6 T080–T088
split. It does not override the current T080–T085 DAG or assign live work to
retired T086–T088.

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
