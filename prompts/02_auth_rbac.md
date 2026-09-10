# Phase 2 Prompt — Identity, Organization, RBAC

Use a strong model first to approve the data/policy design. Implementation may then be split for cheaper models.

## Goal

Implement minimal secure identity and tenant isolation.

## Required design

Define:
- User
- Organization
- role/policy model
- authentication mechanism
- authorization dependency
- cross-tenant resource behavior

Prefer the simplest model that satisfies V1.

## Required implementation

- migrations
- models/repositories/services
- password/token handling or existing auth integration
- login/current-user endpoint
- tenant-aware authorization helpers
- tests

## Mandatory tests

- valid login
- invalid login
- inactive/invalid identity
- same-tenant access
- cross-tenant denial
- unauthorized approval/resource lookup path where applicable
- secrets not exposed in response

## Constraints

- server-side authorization
- no plaintext passwords
- no JWT secret in repo
- do not build enterprise SSO
- do not generalize RBAC beyond V1 need

Update database/security/API docs and progress log.
