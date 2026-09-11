# T014 — Structured logging and redaction

## Model Recommendation
Implementation: LOW_COST
Review: STRONG_REVIEW_REQUIRED only where INDEX says so.

## Goal
Complete exactly the task purpose using existing repository behavior.

## Why
This establishes a safe Phase 1 foundation for later TaskPilot work.

## Existing Behavior
FastAPI app: src/service/service.py. Settings: src/core/settings.py (Pydantic Settings). Persistence: src/memory/postgres.py calls LangGraph setup(). Tests are under tests/core and tests/service. No TaskPilot business tables exist.

## Desired Behavior
The named task has an observable, documented result and focused regression coverage, without changing Phase 2+ domains.

## Read First
- AGENTS.md
- TASK_BACKLOG.md
- docs/ARCHITECTURE.md
- docs/DEVELOPER_GUIDE.md
- process/DECISION_LOG.md

## Exact Allowed Files
Only process/tasks/T014.md implementation targets, their focused tests, and stale documentation named below. No source code is authorized by this planning card.

## Do Not Change
No User, Organization, Authentication/RBAC, Task, TaskRun, TaskStep, Agent Runtime, Redis, Kafka, Kubernetes, Next.js, or Phase 7 trace tables. Do not alter dependencies, lockfile, LangGraph persistence, or public API unless explicitly named.

## Implementation Constraints
Reuse existing Pydantic Settings, FastAPI, stdlib logging, and LangGraph adapters. No new dependency. Preserve Windows startup and USE_FAKE_MODEL=true. Never print secrets.

## Acceptance Criteria
- [ ] Exact task purpose is met.
- [ ] No Phase 2+ functionality is introduced.
- [ ] Focused tests and documentation are updated.
- [ ] process/PROGRESS_LOG.md records files, commands, result, limits, learner notes, next task.

## Focused Tests
Use the exact command specified in the task-specific section below.

## Broader Regression
Run full uv run pytest only where task-specific section requires it; otherwise defer to Phase 1 exit.

## Documentation Update
Update only the files listed in the task-specific section and process/PROGRESS_LOG.md.

## Learner Target
Understand the task-specific foundation concept.

## Definition of Done
Implementation, focused tests, required docs, progress log, diff review, and stated limitations are complete.

## Task-specific decision
src/service/service.py;src/core/settings.py;tests/service

## Task-specific focused command
uv run pytest tests/service -q, then uv run pytest

## Task-specific model
Implementation: STANDARD

## Task-specific allowed files
src/service, tests/service, docs/DEVELOPER_GUIDE.md, docs/TROUBLESHOOTING.md, process/PROGRESS_LOG.md

## Task-specific constraint
Build on T013 request ID; stdlib logging only.

