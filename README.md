# TaskPilot

TaskPilot is a production-oriented task-execution platform for knowledge work. This repository is in its Phase 1 foundation: it retains an upstream LangGraph, FastAPI, and Streamlit runtime while the TaskPilot product is built incrementally and with explicit domain boundaries.

## Current scope

The current checkout provides the retained upstream runtime and the TaskPilot planning and engineering process around it. It does **not** yet provide TaskPilot users, organizations, RBAC, Task/TaskRun/TaskStep records, planner/executor/verifier roles, approvals, or TaskPilot observability tables. See [the architecture baseline](docs/ARCHITECTURE.md) for the implemented-runtime inventory and the phase boundary.

## Getting started

Read [AGENTS.md](AGENTS.md), then follow the verified local commands in [the Developer Guide](docs/DEVELOPER_GUIDE.md). For the ordered Phase 1 work, use [the task backlog](TASK_BACKLOG.md) and the individual task cards under `process/tasks/`.

For deterministic local verification, use `USE_FAKE_MODEL=true`; do not put real credentials in version control.

## Upstream attribution and license

TaskPilot is being developed from [Joshua Carroll's agent-service-toolkit](https://github.com/JoshuaC215/agent-service-toolkit). The upstream project documentation is retained unchanged in [README_UPSTREAM.md](README_UPSTREAM.md), and this repository retains its [MIT License](LICENSE), including the upstream copyright notice.

The retained upstream runtime is a starting point, not a claim that its chat, checkpoint, RAG, interrupt, or tracing examples already implement TaskPilot product domains. Changes for TaskPilot are tracked in the repository's planning and progress documents.
