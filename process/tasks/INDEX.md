# Task Card Index

## Phase 1

See the historical Phase 1 cards T010–T016.

## Phase 2 Task Cards — Identity / Organization / RBAC / Tenant Isolation

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T020 | Identity, tenancy, authentication and business persistence architecture gate | STRONG | STRONG_REVIEW_REQUIRED | Phase 1 | No (design evidence) |
| T021 | SQLAlchemy async foundation, Organization schema and Alembic migration | STANDARD | STRONG_REVIEW_REQUIRED | T020 | Focused + shared persistence |
| T022 | User schema, password field and email constraints | STANDARD | STRONG_REVIEW_REQUIRED | T020, T021 | Focused + shared persistence |
| T022A | Membership schema, role enum, active membership and repositories | STANDARD | STRONG_REVIEW_REQUIRED | T021, T022 | Focused + shared persistence |
| T023 | Opaque authentication session/token service, Argon2id login and bootstrap | STANDARD | STRONG_REVIEW_REQUIRED | T021, T022, T022A | Auth-focused; full at gate |
| T024 | CurrentPrincipal FastAPI dependency and request session lifecycle | STANDARD | STRONG_REVIEW_REQUIRED | T023 | Auth/API-focused |
| T025 | Central authorization helper and tenant policy | STANDARD | STRONG_REVIEW_REQUIRED | T024 | Authorization matrix; full at gate |
| T026 | Negative authentication, tenant, transaction and migration tests | STANDARD | STRONG_REVIEW_REQUIRED | T023–T025 | Yes |
| T027 | Security/API/database documentation synchronization | LOW_COST | None | T020–T026 | Focused docs checks |

Recommended order: T020 (Strong Review) -> T021 -> T022 -> T022A -> T023 -> T024 -> T025 -> T026 -> T027. T020 is authoritative; implementation cards may not change identity, token, migration, transaction, tenant, or error semantics without a new accepted ADR.

## Phase 2 completion status (2026-09-18)

T020 was accepted as ADR-004. T021–T026 are implemented, reviewed and committed; T027 synchronizes the documentation with the implementation. No Phase 3 task has started: there is no Task/TaskRun/TaskStep domain, no approval records, and no TaskPilot HTTP endpoint yet.

The Phase 2 definitions of done were verified at the T026 gate: `uv run pytest` (full suite, including the disposable-PostgreSQL persistence and security suites when `TASKPILOT_TEST_DATABASE_URL` is set), `uv run ruff format --check .`, `uv run ruff check --output-format concise`, `uv run pyrefly check`, `uv lock --check`, and `git diff --check`.

## Phase 3 Task Cards — Task domain

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T030 | Task domain/lifecycle architecture and ADR-005 | STRONG | STRONG_REVIEW_REQUIRED | Phase 2 | No runtime |
| T031 | Task schema and migration | STANDARD | STRONG_REVIEW_REQUIRED | T030 | Migration gate |
| T032 | TaskRun schema and migration | LOW_COST | STRONG_REVIEW_REQUIRED | T031 | Migration gate |
| T033 | DEFERRED: TaskStep to Phase 4 runtime design | — | — | — | No Phase 3 implementation |
| T034 | Tenant-scoped repositories/transactions | STANDARD | STRONG_REVIEW_REQUIRED | T032 | Focused + shared |
| T035 | Lifecycle transition service | STANDARD | STRONG_REVIEW_REQUIRED | T034 | Focused + shared |
| T036 | Task create/list/get API | LOW_COST | STRONG_REVIEW_REQUIRED | T035 | Focused |
| T037 | Task update/cancel API | LOW_COST | STRONG_REVIEW_REQUIRED | T036 | Focused |
| T038 | TaskRun start/inspect | STANDARD | STRONG_REVIEW_REQUIRED | T037 | Concurrency gate |
| T039 | Integration tests and documentation | STANDARD | STRONG_REVIEW_REQUIRED | T030–T038 | Full suite |

Dependency graph: `T031 → T032 → T034 → T035 → T036 → T037 → T038 → T039`. T030 is the approved planning gate; T033 is deferred and not an executable dependency. T039 is the Phase 3 final audit gate.

## Phase 4 Task Cards — Agent runtime

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? | Current Status |
|---|---|---|---|---|---|---|
| T040 | Phase 4 runtime architecture / AgentState contract | STRONG, planning only | APPROVED / COMPLETE | T039, ADR-005 | No runtime | APPROVED / COMPLETE / COMMITTED |
| T041 | Planner schema | LOW_COST | STRONG_REVIEW_REQUIRED | T040 | Focused schema | APPROVED / COMPLETE / COMMITTED |
| T042 | Planner node | STANDARD | STRONG_REVIEW_REQUIRED | T041 | Focused deterministic | APPROVED / COMPLETE / COMMITTED |
| T043 | Executor interface | STRONG/STANDARD | STRONG_REVIEW_REQUIRED | T042 | Focused contract | APPROVED / COMPLETE / COMMITTED |
| T044 | Deterministic execution path | LOW_COST | STRONG_REVIEW_REQUIRED | T043 | Focused deterministic | APPROVED / COMPLETE / COMMITTED |
| T045 | Verifier schema | LOW_COST | STRONG_REVIEW_REQUIRED | T044 | Focused schema | APPROVED / COMPLETE / COMMITTED |
| T046 | Verifier node | STANDARD | STRONG_REVIEW_REQUIRED | T045 | Focused PASS/FAIL | APPROVED / COMPLETE / COMMITTED |
| T047 | Failure classifier | STANDARD | STRONG_REVIEW_REQUIRED | T046 | Table-driven | APPROVED / COMPLETE / COMMITTED |
| T048 | Bounded retry | STANDARD | STRONG_REVIEW_REQUIRED | T047 | Recovery scenarios | APPROVED / COMPLETE / COMMITTED |
| T049 | Bounded replan | STRONG/STANDARD | STRONG_REVIEW_REQUIRED | T048 | Recovery scenarios | APPROVED / COMPLETE / COMMITTED |
| T050 | Checkpoint / resume | STRONG | STRONG_REVIEW_REQUIRED | T049 | PostgreSQL/LangGraph integration | APPROVED / COMPLETE / COMMITTED |
| T051 | Phase 4 Final Audit (read-only) | STRONG | STRONG_REVIEW_REQUIRED | T040–T050 | Full suite at gate | APPROVED / COMPLETE / COMMITTED |

Execution order is the linear DAG `T040 → T041 → T042 → T043 → T044 → T045 → T046 → T047 → T048 → T049 → T050 → T051`. T040/ADR-006 is approved and complete; T041–T050 are completed, task-level approved, and committed on this branch. Phase 4 adds no TaskStep persistence, new HTTP endpoint, worker, approval, HTTP idempotency, external side effect, or Task/TaskRun state.

Current status: T040–T051 are completed, approved, and committed. Phase 4 is
complete and merged to `main`. T060–T064 are implemented, Strong Review
approved, committed, and pushed. Phase 5 implementation is complete. The
Phase 5 Final Audit is approved, and Phase 5 is complete. Its initial audit
returned NOT APPROVED solely because canonical status documentation was stale;
the focused re-review subsequently approved the phase.

Later-phase numbering follows the granular `TASK_BACKLOG.md` authority. The former compact roadmap labels are preserved as intent mappings there; no later work is deleted.

For Phases 5–11, `TASK_BACKLOG.md` is the canonical task-card numbering
source; ROADMAP.md now lists the same canonical IDs and titles. Historical
compact roadmap IDs appear only in the explicitly labeled mapping table and
are not live task assignments.


## Phase 5 Task Cards — Skills / Tools / Context (complete)

| Task | Purpose | Model | Review | Depends | Current status |
|---|---|---|---|---|---|
| T060 | Capability/context architecture gate and ADR-007 | STRONG planning | Planning Strong Review | Phase 4 complete | APPROVED / COMPLETE / COMMITTED / PUSHED |
| T061 | Capability contract and explicit dispatch | STANDARD | Strong Review | T060 | APPROVED / COMPLETE / COMMITTED / PUSHED |
| T062 | One deterministic read-only capability | LOW_COST/STANDARD | Strong Review | T061 | APPROVED / COMPLETE / COMMITTED / PUSHED |
| T063 | Sanitized ContextEnvelope and builder | STANDARD | Strong Review | T060, T061 | APPROVED / COMPLETE / COMMITTED / PUSHED |
| T064 | Runtime integration implementation | STRONG/STANDARD | Strong Review | T061–T063 | APPROVED / COMPLETE / COMMITTED / PUSHED |

DAG: `T060 → T061`, then `T061 → T062` and `T061 → T063`, then `T062 + T063 → T064`. T060–T064 are complete and Strong Review approved. The initial Phase 5 Final Audit returned NOT APPROVED solely because canonical status documentation was stale; the focused Final Audit re-review subsequently approved Phase 5.

## Phase 6 Task Cards — Human-in-the-loop & Safety

Phase 6 Planning is APPROVED, frozen, committed, and published. T080 is
COMPLETE with Strong Review APPROVED; ADR-008 is Accepted and frozen. T081 is
COMPLETE with Strong Review APPROVED, committed, and pushed. T082 is COMPLETE
with Strong Review APPROVED, committed, and pushed to origin. T083 is COMPLETE
with focused Strong Review APPROVED; committed and pushed.
T084 is COMPLETE; Strong Review APPROVED; committed and pushed.

| Task | Purpose | Model | Review | Depends On | Status |
|---|---|---|---|---|---|
| T080 | HITL/risk architecture gate and ADR-008 | STRONG planning | Planning Strong Review | Phase 5 complete | COMPLETE / STRONG REVIEW APPROVED |
| T081 | Approval persistence and migration | LOW_COST | Strong Review | T080 | COMPLETE / STRONG REVIEW APPROVED / COMMITTED / PUSHED |
| T082 | Approval service and decision APIs | STANDARD | Strong Review | T081 | COMPLETE / STRONG REVIEW APPROVED / COMMITTED / PUSHED |
| T083 | Runtime approval boundary | STRONG | Strong Review | T082 | COMPLETE / STRONG REVIEW APPROVED / COMMITTED / PUSHED |
| T084 | Resume and idempotent approved action | STRONG/STANDARD | Strong Review | T083 | COMPLETE / STRONG REVIEW APPROVED / COMMITTED / PUSHED |
| T085 | Phase 6 Final Audit | STRONG read-only | Phase Final Audit | T080–T084 | FOCUSED FINAL AUDIT RE-REVIEW APPROVED; PHASE 6 COMPLETE |

DAG: `T080 → T081 → T082 → T083 → T084 → T085`. T086–T088 are retired as standalone cards; their required behavior is included in T083/T084. ADR-008 is Accepted and frozen after T080 Strong Review approval. T080–T084 are complete and approved, committed, and pushed. The initial T085 Final Audit returned NOT APPROVED for a documentation-only status blocker; after the fix, focused Final Audit re-review APPROVED T085. Phase 6 is COMPLETE; Phase 7 Planning is APPROVED; ADR-009 is accepted/frozen; T090–T097 are COMPLETE / APPROVED; T098 Phase 7 Final Audit is APPROVED; Phase 7 is COMPLETE. Phase 8 Planning is APPROVED / frozen; T100–T106 are COMPLETE / APPROVED; T107 Phase 8 Final Audit is APPROVED; Phase 8 is COMPLETE. Phase 9 Planning is APPROVED / frozen; T110–T116 and T118–T119 are COMPLETE / APPROVED; T117 Phase 9 Final Audit is APPROVED; Phase 9 is COMPLETE.

Numbering evidence: pre-planning HEAD `a2cad16` TASK_BACKLOG.md and ROADMAP.md
already assign Phase 6 T080–T088. Keep T080 despite Phase 5 ending at T064.
T081–T084 follow ADR-008 B1–B3 for schema, canonical identity and mock claim.

## Phase 7 Task Cards — Observability [COMPLETE; FINAL AUDIT APPROVED]

| Task | Purpose | Model | Review | Depends On |
|---|---|---|---|---|
| T090 | Trace data model decision / ADR-009 | STRONG planning | Planning Strong Review APPROVED | Phase 6 |
| T091 | AgentRun persistence (implementation and PostgreSQL evidence complete) | LOW_COST/STANDARD | Strong Review APPROVED | T090 |
| T092 | ToolCall persistence (implementation and PostgreSQL evidence complete) | LOW_COST/STANDARD | Strong Review APPROVED | T090 |
| T093 | Correlation propagation | STANDARD | Strong Review APPROVED | T091, T092 |
| T094 | Latency/status/error normalization | LOW_COST/STANDARD | Strong Review APPROVED | T093 |
| T095 | Token usage adapter | LOW_COST/STANDARD | Strong Review APPROVED | T093 |
| T096 | Deterministic cost estimator | LOW_COST | Strong Review APPROVED | T095 |
| T097 | Tenant-safe trace query API | LOW_COST/STANDARD | Strong Review APPROVED | T091–T096 |
| T098 | Phase 7 Final Audit | STRONG read-only | Phase Final Audit APPROVED | T097 |

DAG: `T090 → (T091, T092) → T093 → (T094, T095) → T096 → T097 → T098`. T092 owns persisted-payload redaction tests, T097 owns timeline-response redaction tests, and T098 audits their evidence; no T099 is created. Phase 7 is COMPLETE; Phase 8 Planning is APPROVED / frozen; T100–T106 are COMPLETE / APPROVED; T107 Phase 8 Final Audit is APPROVED; Phase 8 is COMPLETE. Phase 9 Planning is APPROVED / frozen; T110–T116 and T118–T119 are COMPLETE / APPROVED; T117 Phase 9 Final Audit is APPROVED; Phase 9 is COMPLETE.

## Phase 8 Task Cards — Evaluation [COMPLETE / FINAL AUDIT APPROVED]

T100 remains the architecture/planning gate. Planning Strong Review is APPROVED; ADR-010 is Accepted/frozen, T100 is COMPLETE / APPROVED, and implementation begins at T101.

| Task | Purpose | Model | Review | Depends On | Status |
|---|---|---|---|---|---|
| T100 | Evaluation architecture/planning gate / ADR-010 | STRONG planning | Planning Strong Review APPROVED | T098 | COMPLETE / APPROVED / FROZEN |
| T101 | Deterministic existing-capability baseline and fixture set | LOW_COST/STANDARD | Strong Review | T100 | COMPLETE / APPROVED |
| T102 | Workflow Evaluation runner | STANDARD | Strong Review | T100, T101 | COMPLETE / APPROVED |
| T103 | Deterministic metrics | LOW_COST | Strong Review | T102 | COMPLETE / APPROVED |
| T104 | Machine-readable report | LOW_COST | Strong Review | T102, T103 | COMPLETE / APPROVED |
| T105 | Human-readable report | LOW_COST | Strong Review | T104 | COMPLETE / APPROVED |
| T106 | CI cheap smoke Evaluation | STANDARD | Strong Review | T101–T104 | COMPLETE / APPROVED |
| T107 | Phase 8 Final Audit | STRONG read-only | Phase Final Audit APPROVED | T100–T106 | COMPLETE / APPROVED |

Implementation batches: Planning gate **T100**; Batch 1 **T101–T103**; Batch 2 **T104–T106**; Phase Final Audit **T107**.

DAG: `T100 → T101 → T102 → T103 → T104 → T105`; `T101 + T102 + T103 + T104 → T106`; `T100–T106 → T107`. T107 is the next conflict-free audit ID after completed T098; no T099 is introduced. The baseline is `deterministic.fixture_baseline` using existing `DeterministicFixtureCapability`; no tabular/sum capability is planned.

\r\n
## Phase 9 Task Cards — Product UI (ADR-011 accepted/frozen)

See `process/ADR-011.md` and cards `T110.md`–`T119.md`. T110 is COMPLETE / APPROVED as the planning gate; T118/T119 are COMPLETE / APPROVED backend prerequisites; T111/T112 are COMPLETE / APPROVED. T113–T116 are COMPLETE / APPROVED; Phase 9 Batch 3 is COMPLETE / STRONG REVIEW APPROVED. T117 is the conflict-free Phase 9 Final Audit. Phase 9 Planning is APPROVED / frozen. Next executable work: T117 Final Audit.
