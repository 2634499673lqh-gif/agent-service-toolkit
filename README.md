# TaskPilot

TaskPilot is a production-oriented task-execution platform for knowledge work. This repository is at the end of Phase 2 (identity, organizations, memberships, authorization): it retains an upstream LangGraph, FastAPI, and Streamlit runtime while the TaskPilot product is built incrementally and with explicit domain boundaries.

## Current scope

The current checkout provides the retained upstream runtime plus Phase 2 TaskPilot identity and tenancy, and the T031 Task persistence foundation: a PostgreSQL-only `taskpilot` schema (`organizations`, `users`, `memberships`, `auth_sessions`, `tasks`), Argon2id passwords, opaque revocable sessions, a server-derived request principal, a centralized authorization boundary with fixed 401/403/404 semantics, tenant-scoped lookups, and a negative security matrix. These are service, dependency, and persistence layers: **no TaskPilot HTTP endpoint exists yet**, and TaskRun/TaskStep records, planner/executor/verifier behavior, approvals, and TaskPilot observability tables are still absent. See [the architecture baseline](docs/ARCHITECTURE.md) for the implemented-runtime inventory and the phase boundary.

## Getting started

Read [AGENTS.md](AGENTS.md), then follow the verified local commands in [the Developer Guide](docs/DEVELOPER_GUIDE.md). For the ordered Phase 2 work, use [the task backlog](TASK_BACKLOG.md) and the individual task cards under `process/tasks/`.

For deterministic local verification, use `USE_FAKE_MODEL=true`; do not put real credentials in version control.

## Upstream attribution and license

TaskPilot is being developed from [Joshua Carroll's agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit). The upstream project documentation is retained unchanged in [README_UPSTREAM.md](README_UPSTREAM.md), and this repository retains its [MIT License](LICENSE), including the upstream copyright notice.

The retained upstream runtime is a starting point, not a claim that its chat, checkpoint, RAG, interrupt, or tracing examples already implement TaskPilot product domains. Changes for TaskPilot are tracked in the repository's planning and progress documents.
