# Phase 3 Prompt — Task Domain

## Goal

Build Task/TaskRun/TaskStep lifecycle independently of LLM behavior.

## First

Propose explicit state enums and legal transitions. Have tests for transitions before wiring Agent execution.

## Implement

- Task
- TaskRun
- TaskStep
- repositories/services
- create/list/get task APIs
- start run
- read run/steps
- cancel if legal
- transition service
- tenant authorization
- optimistic locking/idempotency only where justified

## Required invariants

- Run belongs to Task.
- Step belongs to Run.
- caller cannot cross tenant.
- terminal states do not silently return to RUNNING.
- duplicate start request does not accidentally create uncontrolled duplicate work if idempotency is expected.
- timestamps consistent.

## Tests

State transition matrix plus API tests.

No LLM required to pass Phase 3.
