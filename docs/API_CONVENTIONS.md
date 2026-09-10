# API Conventions

Suggested prefix: `/api/v1`.

## Identity
- POST `/auth/login`
- GET `/me`

## Tasks
- POST `/tasks`
- GET `/tasks`
- GET `/tasks/{task_id}`
- POST `/tasks/{task_id}/runs`
- GET `/tasks/{task_id}/runs/{run_id}`
- POST `/tasks/{task_id}/runs/{run_id}/cancel`

## Approvals
- GET `/approvals`
- GET `/approvals/{approval_id}`
- POST `/approvals/{approval_id}/approve`
- POST `/approvals/{approval_id}/reject`

## Trace
- GET `/tasks/{task_id}/runs/{run_id}/trace`

## Knowledge/files
Exact endpoints should follow the existing codebase conventions after Phase 0.

## Rules

- Pydantic request/response models
- consistent error envelope
- explicit pagination
- explicit authorization dependency
- idempotency key for retryable create/action endpoints where needed
- no direct DB calls in route handlers
- 404 may be preferable to 403 for cross-tenant resource existence hiding; decide and document consistently
