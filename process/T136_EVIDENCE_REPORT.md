# Phase 11 T136 executable validation and evidence report

**Task:** T136 — Final executable evaluation/evidence report  
**Run date:** 2026-09-28 (Asia/Shanghai)  
**Repository:** `agent-service-toolkit`  
**Branch:** `phase-11-documentation-demo-final-audit`  
**HEAD:** `023be024c33617b31bff1637ae68723fb620d14c`

This report records commands executed against the current repository. It does not
approve T136 or Phase 11; approval belongs to the independent Strong Review and
the T137 Final Audit respectively.

## 1. Baseline

- Initial tracked working-tree state: clean (`git status --short` produced only
  known permission warnings for `.pytest-tmp-t032/` and `.pytest-tmp-t034/`).
- Docker daemon: available (`docker info` reported server `29.7.2`).
- PostgreSQL: available through the repository Compose service
  (`postgres:16`, healthy).
- Initial Phase 11 status: T129–T135 complete/approved as recorded in the
  current status files; T134 conditional/inactive; T136 next executable; T137
  not started.

## 2. Evidence matrix

| Tier | Exact command / prerequisite | Result and bounded evidence | Classification |
|---|---|---|---|
| Lock | `uv lock --check` | `Resolved 256 packages`; exit 0 | **PASS** |
| Ruff format | `uv run ruff format --check` | Exit 1; existing unformatted files: `scripts/demo_product_flow.py`, `src/memory/postgres.py`. Ruff also emitted Windows cache permission warnings. No files were changed. | **FAIL** |
| Ruff lint | `uv run ruff check --output-format github` | Exit 0; only cache permission warnings | **PASS** |
| Pyrefly | `uv run pyrefly check` | `0 errors` (18 suppressed; 11 warnings not shown) | **PASS** |
| Markdown | `uv run pymarkdown scan README.md docs/` | Exit 1 with existing MD032/MD022/MD012 findings in `docs/AGENT_DESIGN.md`, `docs/DEPLOYMENT_RUNBOOK.md`, `docs/OBSERVABILITY_EVAL.md`, and `docs/SECURITY_HITL.md` | **FAIL** |
| Full regression | `uv run pytest` | Evidence chronology: original T136 run `692 passed, 133 skipped, 18 warnings` in `110.66s`; independent Strong Review rerun recorded `1 failed, 691 passed, 133 skipped, 18 warnings`, failing at `tests/app/test_streamlit_app.py::test_app_simple_non_streaming` with a Streamlit `AppTest` timeout; the Strong Review focused rerun passed. This correction run passed with `692 passed, 133 skipped, 18 warnings` in `144.42s`. Skips are Docker and PostgreSQL environment-gated tests. | **PASS (current general tier; historical intermittent timeout disclosed)** |
| Evaluation | `uv run python scripts/evaluation_smoke.py` | Completed report `taskpilot.eval.report/v1`; 3/3 cases passed; pass rate, approval compliance, evidence completeness, and recovery success all `1.0000`. No external credentials required. | **PASS** |
| Compose config | `docker compose config` | Exit 0; topology resolved as postgres → migrate → agent_service → streamlit_app | **PASS** |
| Compose build | `docker compose build` | All three images built successfully | **PASS** |
| Compose startup | `docker compose up -d --build` | PostgreSQL healthy; migrate exited successfully; API and Streamlit started healthy | **PASS** |
| Health/readiness | `Invoke-WebRequest http://localhost:8080/health/ready`; `Invoke-WebRequest http://localhost:8501/healthz` | Both returned HTTP 200 | **PASS** |
| Compose restart | `docker compose restart agent_service`, then API readiness check | API returned HTTP 200 after restart | **PASS** |
| Repeat migration | `docker compose run --rm migrate` | Exit 0; no migration error | **PASS** |
| Docker integration | `uv run pytest tests/integration -v --run-docker` | 2 failures. `test_service_with_fake_model` uses hard-coded `http://0.0.0.0` and receives Windows proxy/invalid-address failure; `test_service_with_app` has no initial `chat_message` under local `AppTest`. | **BLOCKED BY ENVIRONMENT** |
| PostgreSQL required | Compose PostgreSQL plus `TASKPILOT_TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test`; `uv run pytest tests/persistence tests/runtime/test_task_runtime_postgres.py tests/service/test_approval_api_postgres.py tests/service/test_phase10_batch1_postgres.py tests/service/test_task_api_postgres.py tests/service/test_task_api_t037_postgres.py tests/service/test_task_lifecycle_postgres.py tests/service/test_task_run_api_postgres.py tests/service/test_trace_api_postgres.py -q` | `167 passed, 0 skipped` in `92.42s`; persistence, identity, authorization/tenant, Task/TaskRun, lifecycle, approval, runtime, trace, and Phase 10 suites executed against PostgreSQL | **PASS** |
| Migration | `TASKPILOT_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/taskpilot_test; uv run alembic upgrade head`; then `uv run alembic check` | Upgrade applied `t033_approval` and `t034_observability`; subsequent check reported `No new upgrade operations detected` | **PASS** |
| Deterministic demo | Compose API at `http://localhost:8080`; hidden-prompt bootstrap; two runs of `uv run python scripts/demo_product_flow.py --base-url http://localhost:8080 --email demo-owner@example.com` | Bootstrap succeeded. First run created one task and one pending TaskRun; second run reused the same task and run. Public read-back and logout completed. Tenant-scoped state remained one deterministic Task and one TaskRun; existing other disposable organizations account for separate historical rows. | **PASS** |
| Diff hygiene | `git diff --check` | Exit 0 | **PASS** |

## 3. General and PostgreSQL distinction

The current general suite is green but intentionally skips environment-gated database and
Docker tests when their prerequisites are absent. The PostgreSQL-required tier was
run separately with a disposable PostgreSQL service and had **zero relevant
skips** (`167 passed, 0 skipped`). Those results are the database evidence for
identity, tenant isolation, lifecycle, approval races, runtime/checkpoint,
observability, and Phase 10 concurrency behavior.

The full-regression history retains the independent Strong Review failure rather
than rewriting it as a pass. The affected test passed in the Strong Review
focused rerun, passed again in this correction window, and the complete suite
passed in this correction window. Current evidence supports an intermittent
Windows Streamlit `AppTest` timeout observed once during independent review; it
does not demonstrate a product assertion regression.

## 4. Security-sensitive evidence

The PostgreSQL suites above exercise unauthenticated and role-denied access,
foreign-tenant visibility, session behavior, approval owner/admin versus member
rules, immutable first-decision behavior, tenant-safe trace projection, and
sanitized error/logging boundaries. The full general suite also covers the
deterministic evaluation and service-level security regressions. No credentials,
tokens, or raw payloads were written to this report.

## 5. Non-PASS items and T134 assessment

- Ruff format and repository-wide Markdown lint retain pre-existing failures.
  They are historical lint debt and were not rewritten as part of T136.
- The opt-in Docker integration test is not reproducible in this Windows host
  with its Linux-oriented `0.0.0.0` assumption and standalone Streamlit
  `AppTest` behavior. Compose startup, health, restart, and migration evidence
  itself passed.
- These findings do not establish a new critical/high product or security
  blocker and do not activate T134. The independent Strong Review must decide
  whether the static debt or Docker test-harness limitation affects the Phase 11
  exit condition.

### T134 assessment

NO T134 ACTIVATION REQUIRED

## 6. Scope and status check

- No product feature, runtime worker, queue, Redis, lock, new auth architecture,
  or validation framework was added.
- T137 was not started.
- T136 is implementation/evidence complete and ready for independent Strong
  Review; it is not independently approved.

## 7. Strong Review handoff

The independent reviewer should reproduce or inspect:

1. `uv lock --check`, Ruff, Pyrefly, Markdown, full pytest, evaluation smoke,
   and `git diff --check` classifications above.
2. The PostgreSQL command with `TASKPILOT_TEST_DATABASE_URL` and its
   `167 passed, 0 skipped` result.
3. Alembic upgrade/check evidence against the disposable database.
4. Compose config/build/startup, `docker compose ps`, both health endpoints,
   API restart, and repeat migration.
5. The documented T135 bootstrap and two-run deterministic demo, verifying one
   task and one run within the selected organization and successful logout.
6. The causes and scope of the two opt-in Docker integration failures and the
   pre-existing static-lint findings.

### Exit status

T136 EVIDENCE COMPLETE / READY FOR STRONG REVIEW
