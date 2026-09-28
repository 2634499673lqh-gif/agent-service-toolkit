# Code Reading Order

Use this sequence to learn the current repository. Read the cited tests beside each boundary; this is a navigation guide, not a second architecture specification.

1. **Repository rules and entry points** — `AGENTS.md`, `README.md`, `PROJECT_SPEC.md`, `pyproject.toml`, `.env.example`, `src/run_service.py`, and `src/streamlit_app.py`.
2. **Configuration and persistence choices** — `src/core/settings.py`, `src/persistence/engine.py`, `src/memory/__init__.py`, `src/memory/sqlite.py`, `src/memory/postgres.py`.
3. **Identity and session boundary** — `src/persistence/models.py`, `src/persistence/repositories.py`, `src/persistence/identity.py`, `src/persistence/passwords.py`, `src/persistence/tokens.py`, `src/service/session.py`, `src/service/auth_dependency.py`, and `src/service/authorization.py`.
4. **Schema and migrations** — `migrations/env.py` and `migrations/versions/20260914_01_organization.py` through `20260924_01_observability.py`; then `src/schema/auth_api.py`, `src/schema/task_api.py`, `src/schema/task_run_api.py`, `src/schema/approval_api.py`, and `src/schema/trace_api.py`.
5. **Task and TaskRun lifecycle** — `src/service/task_service.py`, `src/service/task_lifecycle.py`, `src/service/task_run_service.py`, `src/service/task_api.py`, and `src/service/service.py` route registration. The database owns lifecycle truth; the Product UI does not execute a run.
6. **Planner, Executor, and Verifier runtime** — `src/runtime/state.py`, `src/runtime/graph.py`, `src/runtime/planner.py`, `src/runtime/executor.py`, `src/runtime/verifier.py`, `src/runtime/retry.py`, `src/runtime/replan.py`, and `src/runtime/failure.py`.
7. **Capability and context boundaries** — `src/runtime/capability.py`, `src/runtime/capabilities.py`, `src/runtime/context.py`, and `src/runtime/risk.py`.
8. **Human approval boundary** — `src/service/approval_service.py`, `src/service/approval_api.py`, and the approval boundary in `src/runtime/graph.py` and `src/runtime/capability.py`.
9. **Observability and trace projection** — `src/runtime/observability.py`, `src/service/trace_service.py`, `src/service/logging.py`, and `src/schema/trace_api.py`.
10. **Evaluation** — `src/evaluation/fixtures.py`, `src/evaluation/runner.py`, `src/evaluation/metrics.py`, `src/evaluation/report.py`, and `scripts/evaluation_smoke.py`.
11. **Product client and UI** — `src/client/taskpilot.py`, `src/taskpilot_ui.py`, `src/streamlit_app.py`, and `tests/app/test_taskpilot_ui.py` plus `tests/app/test_taskpilot_runs_approvals_trace.py`.
12. **Deployment, readiness, and concurrency hardening** — `compose.yaml`, `docker/Dockerfile.service`, `docker/Dockerfile.app`, `src/service/service.py` health routes, and `src/service/runtime_capacity.py`; then `tests/test_phase10_batch1.py`, `tests/service/test_task_api_postgres.py`, and `.github/workflows/test.yml`.
13. **Evidence and regression** — read the focused tests in `tests/service/`, `tests/persistence/`, `tests/runtime/`, `tests/app/`, and `tests/client/`, then the CI workflow. PostgreSQL and Docker tests are explicitly environment-gated.

While reading, ask who calls each module, what it receives, what it returns or persists, and which trust boundary applies. Keep these concepts separate: legacy LangGraph conversation checkpoints, authenticated TaskPilot identity, persisted Task/TaskRun state, approval records, runtime evidence, and Product UI snapshots.

Deferred or deliberately absent systems include a public runtime execution API, TaskStep persistence/view, background worker, Redis/distributed lock, and generic exactly-once guarantee. Do not infer them from the runtime modules or from the legacy chat UI.
