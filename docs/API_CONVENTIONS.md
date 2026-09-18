# API Conventions

TaskPilot currently exposes **no** HTTP endpoint of its own. The retained upstream routes (`/invoke`, `/stream`, `/history`, `/threads`, `/feedback`, `/info`, `/health`, `/agui/*`) are unchanged, and `/api/v1` exists only as the planned prefix below. Login, principal resolution, and authorization are implemented as service and FastAPI-dependency layers, not as routes.

Planned prefix: `/api/v1`.

## Identity (planned, not implemented)

- POST `/auth/login`
- GET `/me`

## Tasks (planned, not implemented)

- POST `/tasks`
- GET `/tasks`
- GET `/tasks/{task_id}`
- POST `/tasks/{task_id}/runs`
- GET `/tasks/{task_id}/runs/{run_id}`
- POST `/tasks/{task_id}/runs/{run_id}/cancel`

## Approvals (planned, not implemented)

- GET `/approvals`
- GET `/approvals/{approval_id}`
- POST `/approvals/{approval_id}/approve`
- POST `/approvals/{approval_id}/reject`

## Trace (planned, not implemented)

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
- cross-tenant or nonexistent resources answer `404`, not `403`, to hide existence; this is now the frozen decision documented below

## Phase 2 identity rules (implemented; applies to future TaskPilot routes)

TaskPilot `/api/v1` endpoints require the new opaque session credential and a server-derived `CurrentPrincipal`; the legacy `AUTH_SECRET` bearer is not accepted as identity. `CurrentPrincipal` contains user, membership, organization, role, and session IDs loaded from the database. Request body/query/header identity fields are ignored for authorization.

The credential is `Authorization: Bearer <opaque-token>` and the dependency is `service.auth_dependency.require_principal` (T024). Every request re-reads session, user, membership, and organization state, so deactivation, revocation, expiry, and role changes apply immediately; the role and organization always come from the current membership row. Every authentication failure returns the same `401` envelope with `WWW-Authenticate: Bearer` and no principal, so callers cannot distinguish unknown, revoked, expired, or inactive credentials.

Authorization uses the single boundary in `service.authorization` (T025): `require_authenticated`, `require_active_membership`, `require_role(allowed_roles)`, and `require_resource_tenant(resource_organization_id)`. Status codes are fixed: missing/invalid authentication is `401`; a valid in-tenant principal lacking the required role is `403` with `{"detail": "Forbidden"}`; a resource outside the principal's organization is `404` with `{"detail": "Not Found"}`, identical to a resource that does not exist. Tenant existence is resolved inside the principal's scope before any role check, so a `403`/`404` difference cannot reveal another tenant's resources. Route handlers pass the server-derived `CurrentPrincipal.organization_id` as the scope; request body, query, and header identity values are never authorization truth.

Use 401 for missing/malformed/invalid/expired/revoked/inactive credentials, 403 for an authenticated principal lacking an in-tenant role, and 404 for cross-tenant or nonexistent resources. Login failures are generic.

The legacy upstream `AUTH_SECRET` bearer guard still applies only to the existing upstream router. It is not a TaskPilot principal, and a TaskPilot protected dependency rejects it with the same generic 401 as any unknown token.

Bootstrap is not an HTTP endpoint. The CLI/setup path is administrative and uses hidden interactive password confirmation. Any inactive organization, user, or membership, or revoked/expired/missing session returns 401 before principal construction.
