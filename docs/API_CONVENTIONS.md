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


## Phase 2 identity rules (decided; planned API behavior)

TaskPilot `/api/v1` endpoints require the new opaque session credential and a server-derived `CurrentPrincipal`; the legacy `AUTH_SECRET` bearer is not accepted as identity. `CurrentPrincipal` contains user, membership, organization, role, and session IDs loaded from the database. Request body/query/header identity fields are ignored for authorization.

Use 401 for missing/malformed/invalid/expired/revoked/inactive credentials, 403 for an authenticated principal lacking an in-tenant role, and 404 for cross-tenant or nonexistent resources. Login failures are generic.

Bootstrap is not an HTTP endpoint. The CLI/setup path is administrative and uses hidden interactive password confirmation. Any inactive organization, user, or membership, or revoked/expired/missing session returns 401 before principal construction.
