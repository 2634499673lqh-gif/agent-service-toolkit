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

### T030 — Task state machine design [A]
Goal: approve Task/Run/Step states, transitions, ownership and idempotency rules.
Output: transition table + tests-to-write.

### T031 — Task ORM + migration [C after T030]
Implement exact approved schema.
Acceptance: migration + model constraints.

### T032 — TaskRun ORM + migration [C]
Implement approved TaskRun relation/state fields.

### T033 — TaskStep ORM + migration [C]
Implement approved TaskStep relation/order/state fields.

### T034 — Task repository [C/B]
Goal: tenant-scoped create/get/list operations.
Acceptance: repository tests include tenant filtering.

### T035 — Task transition service [B]
Goal: legal transition enforcement.
Acceptance: full transition matrix tests; illegal transitions rejected.

### T036 — POST /tasks [C]
Goal: create task endpoint using service layer.
Acceptance: auth, validation, tenant ownership tests.

### T037 — GET /tasks [C]
Goal: paginated/defined listing of permitted tasks.
Acceptance: no cross-tenant rows.

### T038 — GET /tasks/{id} [C]
Goal: return allowed task only.
Acceptance: same-tenant and hidden cross-tenant behavior.

### T039 — Start TaskRun API [B]
Goal: create/start run according to approved idempotency policy.
Acceptance: duplicate/request semantics tested.

## Phase 4 — Agent runtime

### T040 — AgentState design [A]
Goal: exact typed state contract and persistence boundaries.
Output: state fields + what must NOT be stored.

### T041 — Planner schema [C after T040]
Goal: Pydantic structured Plan/PlanStep output only.
Acceptance: schema tests and invalid output tests.

### T042 — Planner node [B]
Goal: model call producing valid structured plan.
Acceptance: mocked unit tests; parse/repair behavior explicit.

### T043 — Executor interface [A/B]
Goal: define one-step execution contract, no side effects yet.
Acceptance: deterministic executor test.

### T044 — Simple deterministic tool path [C]
Goal: execute one mock/calculation tool end to end.
Acceptance: result stored in state/step.

### T045 — Verifier schema [C]
Goal: structured verdict/criteria/evidence contract.
Tests only for schema/validation.

### T046 — Verifier node [B]
Goal: verify output against acceptance criteria.
Acceptance: mocked PASS/FAIL cases.

### T047 — Failure classifier [B]
Goal: normalized error categories.
Acceptance: table-driven tests.

### T048 — Retry budget [B]
Goal: bounded retry for retryable tool failure.
Acceptance: fail-once succeeds; always-fail terminates.

### T049 — Replan route [A/B]
Goal: bounded replan for plan/context failures.
Acceptance: no infinite loops.

### T050 — Checkpoint/resume [A]
Goal: persist/resume graph state safely.
Acceptance: process interruption/resumption test.

## Phase 5 — Skill / tool / context

### T060 — Skill manifest decision [A]
Goal: finalize minimal Skill metadata and loading mechanism.

### T061 — Skill registry [C/B]
Goal: load/list/get versioned skills.
Acceptance: duplicate/missing skill tests.

### T062 — Research skill manifest [C]
Docs/config + tests only, based on approved registry.

### T063 — Document-analysis skill manifest [C]
Same scope.

### T064 — Tabular-analysis skill manifest [C]
Same scope.

### T065 — Tool interface [B]
Goal: typed input/output + risk/error metadata.

### T066 — Safe tabular tool [B]
Goal: deterministic CSV/XLSX summary/metric operations, not arbitrary untrusted code execution.
Acceptance: known fixtures produce exact outputs.

### T067 — Document retrieval tool [B]
Goal: tenant-scoped document/KB retrieval.
Acceptance: cross-tenant test mandatory.

### T068 — Research/search adapter [B]
Goal: provider-abstracted, mockable research tool.
Acceptance: live network not required in unit tests.

### T069 — Mock external action tool [C/B]
Goal: in-memory/test DB side-effect counter for later approval proof.
Acceptance: idempotency hook exists; no real external system.

### T070 — ContextEnvelope schema [C after design]
Goal: typed context container with provenance/budget metadata.

### T071 — Context builder [A/B]
Goal: select task/step/knowledge/memory/tool context explicitly.
Acceptance: provenance test.

### T072 — Context budget trimming [C/B]
Goal: deterministic priority-based trimming.
Acceptance: safety/objective never dropped.

### T073 — User memory persistence/retrieval [B]
Goal: minimal scoped memory, not a generalized memory platform.

### T074 — Organization knowledge tenant filtering [B]
Mandatory cross-tenant retrieval tests.

## Phase 6 — HITL

### T080 — Risk policy design [A]
Goal: exact mapping L0-L3, server-side enforcement point.

### T081 — Approval ORM/migration [C after T080]
Exact approved fields/constraints only.

### T082 — Approval service [B]
Create/read/decide with authorization and immutable proposed action.

### T083 — Approval list/detail APIs [C]
Read-only endpoints, tenant/policy scoped.

### T084 — Approve/reject APIs [B]
Acceptance: unauthorized and duplicate decision tests.

### T085 — LangGraph interrupt integration [A]
Goal: persist checkpoint and enter WAITING_APPROVAL before effect.

### T086 — Resume after approval [A]
Goal: resume correct run/step without duplicate work.

### T087 — Side-effect idempotency [A/B]
Goal: approved mock action executes exactly once.
Mandatory duplicate HTTP/worker retry tests.

### T088 — HITL audit events [C/B]
Goal: decision and execution trace/audit records.

## Phase 7 — Observability

### T090 — Trace data model decision [A]
Goal: decide DB events vs external trace backend responsibilities.

### T091 — AgentRun persistence [C/B]
Approved schema only.

### T092 — ToolCall persistence [C/B]
Approved schema only.

### T093 — Correlation propagation [B]
Goal: request→task→run→step→agent→tool IDs connected.

### T094 — Latency/status/error metrics [C/B]
Goal: record normalized timing/error fields.

### T095 — Token usage adapter [C/B]
Goal: normalize provider usage when returned; handle unavailable values honestly.

### T096 — Cost estimator [C]
Goal: configurable price table/calculation, not hardcoded business truth.
Tests deterministic.

### T097 — Trace query API [C/B]
Goal: retrieve ordered sanitized execution timeline.

### T098 — Redaction tests [C]
Tests-only hardening.

## Phase 8 — Evaluation

### T100 — Eval schema decision [A]
Define case/run/result versioning and metrics.

### T101 — Deterministic fixture set [C]
Create fixed tabular/recovery/approval fixtures.

### T102 — Workflow eval runner [B]
Goal: repeatable CLI/test runner.

### T103 — Metrics calculation [C]
Goal: pure deterministic metric functions + tests.

### T104 — Machine-readable report [C]
JSON artifact schema + tests.

### T105 — Human-readable report [C]
Markdown/console summary from machine results.

### T106 — CI cheap smoke eval [B]
No expensive live model dependency in default CI.

## Phase 9 — UI

### T110 — Existing UI gap assessment [B]
No code rewrite; decide minimal needed screens.

### T111 — Task create form [C]
Single UI component/flow.

### T112 — Task list/detail [C]
Use existing API client; no state duplication.

### T113 — Run/step timeline [C/B]
Render backend state.

### T114 — Approval queue/detail [C/B]
Render proposed action/risk and invoke decision APIs.

### T115 — Trace timeline [C]
Render sanitized trace.

### T116 — Error/loading states [C]
UI-only hardening.

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
