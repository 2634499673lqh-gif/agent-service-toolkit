# Atomic Task Backlog

> This backlog is a planning map. After Phase 0, a strong model must replace placeholder paths with exact repository paths before handing an item to a cheaper model.

Model:
- A = strong architecture/review
- B = standard implementation
- C = low-cost, tightly scoped implementation

## Phase 0

### T000 — Baseline repository inventory [A]
Goal: map existing architecture and exact file paths.
Acceptance: tree, main entrypoints, test commands, reusable features, gaps recorded.
No feature implementation.

### T001 — Baseline execution [B]
Goal: run current test/start commands without changing behavior.
Acceptance: exact commands and pass/fail state recorded.
If failures exist, preserve and document them.

### T002 — Exact code reading map [C after T000]
Goal: update `docs/CODE_READING_ORDER.md` using real paths.
Acceptance: every path exists; each entry explains why to read it.
Do not modify production code.

### T003 — Architecture delta ADR [A]
Goal: decide what upstream modules are kept, adapted, deprecated, or added.
Acceptance: decision log includes tradeoffs; no speculative framework replacement.

## Phase 1

### T010 — Repository branding and attribution [C]
Goal: introduce TaskPilot name/description while preserving upstream license/attribution.
Scope: README/docs only.
Acceptance: no removed license; no false claims about unimplemented features.

### T011 — Environment template audit [C]
Goal: make `.env.example` complete and secret-free.
Acceptance: every required var documented with placeholder; no real credential.

### T012 — Settings validation [B]
Goal: centralize/normalize configuration using existing settings pattern.
Acceptance: invalid required config fails clearly; tests cover validation.
Do not replace config framework unless approved.

### T013 — Request correlation ID [C/B]
Goal: add request_id generation/propagation to API logs.
Acceptance: request logs share request_id; response header if architecture chooses; tests.

### T014 — Structured logging redaction [B]
Goal: structured logs with basic secret-field redaction.
Acceptance: test proves known secret fields are not emitted.

### T015 — Migration baseline verification [B]
Goal: verify current migration mechanism and produce first TaskPilot-safe baseline if needed.
Acceptance: fresh DB can migrate from zero.
Schema design beyond prerequisites is out of scope.

### T016 — Developer command documentation [C]
Goal: fill exact commands in Developer Guide.
Acceptance: commands were actually executed.
Docs only.

## Phase 2 — Identity / tenancy

### T020 — Identity domain decision [A]
Goal: choose minimal V1 User/Organization/Role model.
Output: approved schema, authorization rules, migration plan, test matrix.

### T021 — Organization model/migration [C after T020]
Goal: implement approved Organization schema only.
Acceptance: migration up works; model tests pass.
Do not implement auth.

### T022 — User model/migration [C after T020]
Goal: implement approved User schema and org relation.
Acceptance: constraints and migration tests.
Do not implement login yet.

### T023 — Authentication service [B]
Goal: implement login/identity issuance using approved design.
Acceptance: success/failure tests; password/token secrets not leaked.

### T024 — Current-user dependency [C/B]
Goal: authenticated API dependency resolving current user.
Acceptance: missing/invalid credential tests.

### T025 — Tenant authorization helper [B]
Goal: one reusable server-side authorization policy/helper.
Acceptance: same-tenant allow, cross-tenant deny.

### T026 — Auth API tests [C]
Goal: expand negative cases only.
Scope: tests.
Acceptance: invalid login/token/inactive user/tenant denial covered.

### T027 — Security docs sync [C]
Docs-only update for implemented auth behavior.

## Phase 3 — Task domain

Single planning gate: T030 Planning Strong Review approves ADR-005; ADR-005 then becomes Accepted. Production begins at T031.

Executable chain: `T031 → T032 → T034 → T035 → T036 → T037 → T038 → T039`.

### T030 — Task domain and lifecycle architecture [A]
Planning-only architecture milestone; no second freeze, schema, migration, runtime code, or tests.

### T031 — Task schema and migration [B after T030]
Implement approved Task persistence and migration.

### T032 — TaskRun schema and migration [C after T031]
Implement approved TaskRun persistence and migration, including transactional one-active-run invariant.

### T033 — DEFERRED
TaskStep persistence is deferred to Phase 4 runtime design and is not an executable dependency.

### T034 — Tenant-scoped repositories [B after T032]
Implement server-derived tenant predicates and service transaction boundaries.

### T035 — Lifecycle transition service [B after T034]
Implement Task/TaskRun legal transitions and persistence-only cancellation.

### T036 — Task create/list/get API [C after T035]
Implement typed routes with 401/403/404 and server-derived principal scope; no application-level idempotency.

### T037 — Task update/cancel API [C after T036]
Implement sequential update/cancel behavior and lifecycle conflicts.

### T038 — TaskRun start/inspect API [B after T037]
Implement start/inspect integration, retry rules, run numbering, and concurrency safety; no Idempotency-Key.

### T039 — Phase 3 integration and documentation audit [B after T038]
Complete security/lifecycle/migration evidence and planning synchronization.

## Phase 4 — Agent runtime (planning contract frozen by ADR-006/T040)

### T040 — Phase 4 runtime architecture / AgentState contract [A]
Planning-only architecture gate. Freeze the typed serializable AgentState, graph topology, Task/TaskRun integration, TaskStep deferral, runtime entry, recovery budgets, checkpoint identity/resume trust, persistence ownership, transactions, concurrency, security boundary, and deferred scope. No production code.

### T041 — Planner schema [C after T040]
Implement the minimal typed Plan/PlanStep runtime schema and structural validation. PlanStep is not a persisted TaskStep entity; invalid structured output has a bounded repair/fail rule.

### T042 — Planner node [B after T041]
Implement the deterministic/mockable planner node that emits a validated Plan without granting authorization or tool authority.

### T043 — Executor interface [A/B after T042]
Define the narrow typed executor boundary for one deterministic, side-effect-free step; no generic tool or skill registry.

### T044 — Deterministic execution path [C after T043]
Implement exactly one deterministic normal execution path using dependency injection; no provider, shell, filesystem, network, or external side effect.

### T045 — Verifier schema [C after T044]
Implement the minimal typed verifier result with PASS/FAIL and required criteria/evidence/reason fields only.

### T046 — Verifier node [B after T045]
Implement the verifier node and deterministic PASS/FAIL behavior; evidence is not authorization truth.

### T047 — Failure classifier [B after T046]
Define the small explicit retry/replan/terminal classification contract and sanitize internal failures from user-facing output.

### T048 — Bounded retry [B after T047]
Implement a one-retry budget with fail-once success and always-fail exhaustion scenarios; retry count is checkpoint state, not HTTP idempotency.

### T049 — Bounded replan [A/B after T048]
Implement a one-replan budget with deterministic success and exhaustion; counters cannot reset and no new TaskRun is created.

### T050 — Checkpoint / resume [A after T049]
Bind LangGraph checkpoint identity to the tenant-validated TaskRun, define resume/corruption fail-closed behavior, and preserve independent TaskPilot/LangGraph persistence ownership.

### T051 — Phase 4 Final Audit [A, read-only after T050]
Collect implementation evidence for the accepted ADR/T040 contract, deterministic planner/executor/verifier/recovery behavior, TaskRun lifecycle/security/concurrency, checkpoint/resume, persistence separation, static checks, and deferred-scope compliance. No implementation work.

## Phase 5 — Skills / Tools / Context (complete)

Phase 4/T051 is approved, complete, and merged to `main`. T060–T064 are implemented, Strong Review approved, committed, and pushed. Phase 5 implementation is complete and the Phase 5 Final Audit is approved; Phase 5 is complete. The initial audit returned NOT APPROVED solely because canonical status documentation was stale, and the focused re-review subsequently approved Phase 5. DAG: `T060 → T061`, then `T061 → T062` and `T061 → T063`, then `T062 + T063 → T064`.

### T060 — Capability/context architecture gate [A]
Depends on completed Phase 4/T051. Freeze one minimal Capability contract, sanitized ContextEnvelope, explicit dispatch, deterministic read-only scope, and replay/security boundaries. Architecture gate complete; ADR-007 is accepted and frozen.

### T061 — Capability contract and explicit dispatch [B]
Reuse `ExecutionResult` / `RuntimeFailure` in one typed in-process dispatch adapter. No registry, plugin loading, provider, or public API.

### T062 — First deterministic read-only capability [C/B]
Implement exactly one bounded, deterministic, side-effect-free capability with fixture evidence.

### T063 — Sanitized ContextEnvelope and builder [B]
Implement minimum typed, provenance-labeled, size-bounded context; exclude authority, secrets, and runtime objects. No persistence.

### T064 — Phase 5 runtime integration implementation [A/B]
Integrate with `TaskRuntimeService` and preserve retry/replan/checkpoint/resume and tenant guarantees. T064 implementation and independent Strong Review are complete; the separate read-only Phase 5 Final Audit is approved.

Phase 5 defers separate Skill/Tool hierarchies, dynamic registries/plugins/MCP, credentials or real side effects, new persistence, public runtime APIs, HITL, memory/knowledge systems, workers, and exactly-once claims.
## Phase 6 — HITL

Phase 6 planning narrows the former nine-item sketch to six reviewable cards;
the cards below are authoritative. Phase 6 Planning is APPROVED, frozen,
committed, and published. T080 is complete; T081 is Strong Review approved,
committed, and pushed. T082 is complete, Strong Review approved, committed, and
pushed to origin. T083 is COMPLETE; Strong Review APPROVED; committed and pushed. T084 is COMPLETE; Strong Review APPROVED; committed and pushed. The initial T085 Final Audit returned NOT APPROVED for a documentation-only status blocker; after the fix, focused Final Audit re-review APPROVED T085. Phase 6 is COMPLETE. Phase 7 Planning is APPROVED; ADR-009 is accepted/frozen; T090–T097 are COMPLETE / APPROVED; T098 Phase 7 Final Audit is APPROVED; Phase 7 is COMPLETE. Phase 8 Planning is APPROVED / frozen; T100–T106 are COMPLETE / APPROVED; T107 Phase 8 Final Audit is APPROVED; Phase 8 is COMPLETE. Phase 9 Planning is APPROVED / frozen; implementation is NOT STARTED.

Numbering verified against pre-planning HEAD `a2cad167eac375bf0680c9bb0f0c6b67e5dd2020`:
its backlog already assigned Phase 6 T080–T088, and its roadmap referenced that
range. The gap after T064 is inherited, not invented here; keep T080 onward.
ADR-008 B1–B3 freeze the normalized ownership, unique run/replan/step approval
identity, and transactional mock outcome used by T081–T084.

### T080 — HITL/risk architecture gate [A, planning only]
Accept ADR-008: one server-side risk classifier, L0/L1 auto-allow, L2 approval,
L3 blocked; approval boundary, state/lifecycle choice, tenant/role policy,
checkpoint/resume, race, replay, and audit contracts are frozen.

T080 is COMPLETE; its focused Strong Review is APPROVED and ADR-008 is
Accepted/frozen. T081 is COMPLETE and Strong Review approved, committed, and
pushed. T082 is COMPLETE with Strong Review APPROVED, committed, and pushed.
T083 is COMPLETE; Strong Review APPROVED; committed and pushed. T084 is COMPLETE; Strong Review APPROVED; committed and pushed.

### T081 — Approval persistence and migration [C after T080]
Implement only the approved tenant-scoped approval record and constraints.
Status: COMPLETE; Strong Review APPROVED; committed and pushed.

### T082 — Approval service and decision APIs [B after T081]
Create/read/approve/reject with immutable proposed action, owner/admin policy,
single terminal decision, stale-run checks, and sanitized audit evidence.
Status: COMPLETE; Strong Review APPROVED; committed and pushed to origin.

### T083 — Runtime approval boundary [A after T082]
Classify before an effect, persist the checkpoint before waiting, and expose the
minimal WAITING_APPROVAL result without changing unrelated lifecycle states.
Status: COMPLETE; FOCUSED STRONG REVIEW APPROVED; committed and pushed.

### T084 — Resume and idempotent approved action [A/B after T083]
Resume the exact run/step after approval, fail closed on rejection/cancellation,
and prove one approved mock effect under duplicate HTTP/worker delivery.
Status: COMPLETE; Strong Review APPROVED; committed and pushed.

### T085 — Phase 6 final audit [A, read-only after T084]
Collect migration, authorization, race, replay, redaction, lifecycle, and
integration evidence; no implementation work.
Status: Initial Final Audit NOT APPROVED for a documentation-only blocker;
blocker fixed; focused Final Audit re-review APPROVED. Phase 6 COMPLETE.

T086–T088 are retired as standalone cards: their required behavior is covered
by T083/T084 and the audit. Generic policy engines, workflow engines, worker
queues, distributed locks, credentials, real external effects, and L3 execution
remain deferred.

## Phase 7 — Observability [COMPLETE; FINAL AUDIT APPROVED]

Phase 7 planning is defined by ADR-009 and task cards T090–T098. DAG: `T090 → (T091, T092) → T093 → (T094, T095) → T096 → T097 → T098`. T090–T097 are COMPLETE / APPROVED; T098 Phase 7 Final Audit is APPROVED; Phase 7 is COMPLETE. The audit covered redaction evidence owned by T092/T097 and did not add tests or fixes. No additional audit ID is introduced.

### T090 — Trace data model decision [A]
Goal: decide DB events vs external trace backend responsibilities.

### T091 — AgentRun persistence [C/B]
Implementation and live PostgreSQL evidence complete; Strong Review APPROVED.
Approved schema only; no correlation wiring.

### T092 — ToolCall persistence [C/B]
Implementation and live PostgreSQL evidence complete; Strong Review APPROVED.
Approved schema only, including persisted-payload redaction tests.

### T093 — Correlation propagation [B]
Goal: request→task→run→step→agent→tool IDs connected.

### T094 — Latency/status/error metrics [C/B]
Goal: record normalized timing/error fields. Implementation and Strong Review
are approved.

### T095 — Token usage adapter [C/B]
Goal: normalize provider usage when returned; handle unavailable values honestly.
Implementation and Strong Review are approved.

### T096 — Cost estimator [C]
Goal: configurable price table/calculation, not hardcoded business truth.
Tests deterministic. Implementation and Strong Review are approved.

### T097 — Trace query API [C/B]
Goal: retrieve ordered sanitized execution timeline, including response-redaction
tests. T097 implementation and required PostgreSQL evidence are complete;
Strong Review APPROVED.

### T098 — Phase 7 Final Audit [A, read-only after T097]
Fresh-eyes, independent, non-mutating audit of ADR-009, T091–T097 evidence,
tenant SQL, ordering, recovery reconstruction, and redaction evidence owned by
T092/T097. No code/test fixes, commit, push, or T099.

## Phase 8 — Evaluation [COMPLETE / FINAL AUDIT APPROVED]

T100 is the Phase 8 Evaluation architecture/planning gate. Planning Strong Review is APPROVED; ADR-010 is Accepted/frozen, T100 is COMPLETE / APPROVED, and implementation begins at T101.

### T100 — Evaluation architecture/planning gate [A]
Freeze ADR-010, exact metric and report contracts, five-case suite, comparison semantics, bounds, redaction, deferred scope, and DAG. Planning-only; see `process/tasks/T100.md`.

### T101 — Deterministic fixture set [C]
Implement the repository-backed `deterministic.fixture_baseline` plus retry, replan, L2 approval, and L3 blocked cases. The prior tabular/sum wording was pre-planning intent; reuse existing capability behavior; see `process/tasks/T101.md`.

### T102 — Workflow Evaluation runner [B]
Repeatable local runner with stable ordering, bounded typed results, and explicit completed/partial/error semantics; see `process/tasks/T102.md`.

### T103 — Deterministic metrics [C]
Implement the four fixed metric contracts with Decimal rounding and explicit not-applicable zero denominators; see `process/tasks/T103.md`.

### T104 — Machine-readable report [C]
Emit the exact bounded `taskpilot.eval.report/v1` canonical JSON contract; see `process/tasks/T104.md`.

### T105 — Human-readable report [C]
Derive deterministic Markdown/console output from T104 only; see `process/tasks/T105.md`.

### T106 — CI cheap smoke [B]
Run the baseline/retry/L2 subset with bounded non-zero failure semantics; see `process/tasks/T106.md`.

### T107 — Phase 8 Final Audit [A, read-only]
Independent fresh-eyes audit of T100–T106; see `process/tasks/T107.md`.

Batches: Planning gate **T100**; Implementation Batch 1 **T101–T103**; Implementation Batch 2 **T104–T106**; Phase Final Audit **T107**.

DAG: `T100 → T101 → T102 → T103 → T104 → T105`; `T101 + T102 + T103 + T104 → T106`; `T100–T106 → T107`.

## Phase 9 — UI (superseded planning sketch)

The original T110–T116 sketch is retained as history only. The implementation-ready Phase 9 contract is the ADR-011 section appended below, including T118/T119 prerequisites and T117 Final Audit.

## Phase 10 — deployment/concurrency

### T120 — Concurrency architecture review [A]
Measure/identify need for background worker.

### T121 — DB pool/transaction review [A/B]
Fix only evidenced issues.

### T122 — Background worker integration [B, only if approved]
One queue path, not a new distributed platform.

### T123 — Rate limiting/backpressure [B]
Protect API/LLM execution.

### T124 — Health/readiness [C]
Small endpoints/checks.

### T125 — Docker Compose hardening [B]
Reproducible services/volumes/env.

### T126 — CI pipeline [B/C]
Install, lint/type/test/smoke.

### T127 — Concurrency smoke test [B]
Simulate multiple users/runs; verify no cross-state corruption.

## Phase 11 — finalization

### T130 — User Guide audit [C]
Docs-only, verify UI behavior.

### T131 — Developer Guide audit [C]
Docs-only, execute commands.

### T132 — Code Reading Order audit [C]
All paths valid.

### T133 — Architecture/security audit [A]
Severity-ranked findings only first.

### T134 — Critical/high fixes [A/B]
Each finding becomes a separate task.

### T135 — Demo script/data [C]
No production code unless needed for demo fixtures.

### T136 — Final eval/test report [B/C]
Run actual commands and record outputs.

\r\n
## Phase 9 — Product UI (planning APPROVED / frozen, ADR-011 accepted/frozen)

Phase 9 planning is APPROVED / frozen. T110 is COMPLETE / APPROVED and was the single planning gate. The current repository has protected Task/TaskRun/Approval/trace APIs and a legacy chat Streamlit UI, but no TaskPilot login HTTP route, no run-discovery HTTP route, and no Product client. ADR-011 freezes Streamlit-first Product UI, T118 authentication/session/logout HTTP, T119 tenant-scoped run discovery, and truthful persisted-task/evidence inspection scope. Phase 9 does not execute the internal runtime from the UI.

| Task | Purpose | Model | Review | Depends | Status |
|---|---|---|---|---|---|
| T110 | Product UI architecture/planning gate / ADR-011 | STRONG planning | Planning Strong Review | T107 | COMPLETE / APPROVED |
| T118 | AuthService-backed login/session/logout HTTP API | STANDARD/STRONG | Strong Review | T110 | NOT STARTED |
| T119 | Tenant-scoped TaskRun list HTTP API | LOW_COST | Strong Review | T110 | NOT STARTED |
| T111 | Product login/session client and task create form | STANDARD | Strong Review | T118 | NOT STARTED |
| T112 | Task list/detail navigation | LOW_COST/STANDARD | Strong Review | T111 | NOT STARTED |
| T113 | TaskRun start/discovery/status view | STANDARD | Strong Review | T112, T119 | NOT STARTED |
| T114 | Selected-run approval list/detail/decision | STANDARD | Strong Review | T113 | NOT STARTED |
| T115 | Sanitized deterministic trace timeline | LOW_COST | Strong Review | T113 | NOT STARTED |
| T116 | Product error/loading and session-isolation integration | STANDARD | Strong Review | T114, T115 | NOT STARTED |
| T117 | Phase 9 Final Audit | STRONG read-only | Phase Final Audit | T110–T116, T118–T119 | NOT STARTED |

DAG: `T107 → T110 → (T118, T119)`; `T118 → T111 → T112`; `T112 + T119 → T113`; `T113 → (T114, T115)`; `T114 + T115 → T116`; `T110–T119 (excluding unused gaps) → T117`.

Batches: planning T110; backend T118–T119; client/task T111–T112; run/approval/trace/error T113–T116; independent Final Audit T117. React/Next.js, TaskStep persistence, public runtime execution, websocket/live updates, uploads, eval dashboard, admin/org management, worker UI, deployment, update/cancel UI, registration/SSO/refresh tokens and external effects remain deferred. See `process/ADR-011.md` and cards T110–T119.

Next executable implementation batch: T118–T119.
