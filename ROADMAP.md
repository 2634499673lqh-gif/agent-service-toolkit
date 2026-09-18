# TaskPilot Roadmap

> Principle: vertical slices before feature breadth. Each phase must leave the repository runnable.

## Phase 0 — Repository Assessment [STRONG MODEL]

Goal: understand the upstream/base repository and produce an explicit delta plan.

Tasks:
- T000 baseline inventory
- T001 run current tests/build
- T002 map request/agent/data flow
- T003 identify reusable modules
- T004 write gap analysis and architecture decision

Exit:
- no major speculative refactor
- baseline commands documented
- code reading order exists
- architecture delta reviewed

## Phase 1 — Foundation & Documentation [ECONOMY/STANDARD]

Goal: stable config, documentation discipline, migrations, logging IDs.

Tasks:
- T010 TaskPilot naming/README while preserving upstream attribution/license
- T011 settings/env validation
- T012 request_id structured logging
- T013 DB migration baseline
- T014 developer commands/scripts

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
- T030 Task/TaskRun/TaskStep states
- T031 persistence/repository
- T032 create/list/get APIs
- T033 transition service
- T034 idempotency/concurrency controls
- T035 task tests/docs

Exit:
- task lifecycle works without LLM
- invalid transitions rejected

## Phase 4 — Agent Runtime [STRONG MODEL]

Tasks:
- T040 AgentState contract
- T041 Planner structured output
- T042 Executor node
- T043 Verifier contract
- T044 failure classifier/retry/replan
- T045 checkpoint/resume
- T046 deterministic runtime tests

Exit:
- one complete task can execute through graph
- a forced retry is visible and recoverable

## Phase 5 — Skills, Tools, Context [STRONG DESIGN + STANDARD/ECONOMY IMPLEMENTATION]

Tasks:
- T050 Skill manifest + registry
- T051 research skill
- T052 document-analysis skill
- T053 tabular-analysis skill
- T054 typed tool registry
- T055 context builder
- T056 user memory
- T057 org knowledge retrieval
- T058 context budget/tests

Exit:
- planner selects a skill
- tool calls are typed/traced
- context sources can be explained

## Phase 6 — Human-in-the-loop & Safety [STRONG MODEL]

Tasks:
- T060 risk policy
- T061 approval persistence/API
- T062 graph interrupt/pause
- T063 approve/reject/resume
- T064 exactly-once protection for approved side effects
- T065 audit/security tests

Exit:
- L2 action cannot execute before approval
- duplicate approval/action cannot duplicate effect

## Phase 7 — Observability [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T070 trace/event schema
- T071 structured correlation
- T072 agent/tool/model metrics
- T073 token/cost adapter
- T074 trace query API
- T075 basic trace UI

Exit:
- one task can be reconstructed from trace
- failure point and retry are visible

## Phase 8 — Evaluation [STRONG DESIGN + ECONOMY IMPLEMENTATION]

Tasks:
- T080 eval schema/dataset
- T081 deterministic fixtures
- T082 evaluator runner
- T083 workflow/accuracy/recovery metrics
- T084 regression report
- T085 CI smoke gate

Exit:
- same eval suite can be rerun
- results are versioned/comparable

## Phase 9 — Product UI [STANDARD/ECONOMY]

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
- T100 long-running work queue decision
- T101 Redis/worker only if needed
- T102 rate limiting/backpressure
- T103 health/readiness
- T104 Docker Compose production-like local stack
- T105 CI
- T106 security config/CORS/secrets
- T107 load/concurrency smoke test

Exit:
- multiple simultaneous demo users do not corrupt state
- startup/health/recovery documented

## Phase 11 — Documentation, Demo, Final Audit [STRONG REVIEW + ECONOMY DOC WORK]

Tasks:
- T110 user guide
- T111 developer guide
- T112 architecture diagrams
- T113 code reading order final
- T114 troubleshooting/runbook
- T115 demo data/scenarios
- T116 security audit
- T117 architecture audit
- T118 final test/eval report

Exit:
- a new learner can clone, start, understand, and demo the project from docs alone
