# Database Design Guide

This document records the Phase 2 architecture decision and the T021–T023 schema that implements it. ADR-004 is authoritative.

## Ownership and bootstrap

TaskPilot business tables are PostgreSQL-only in schema `taskpilot`, managed by SQLAlchemy 2.x models and Alembic revisions owned by TaskPilot. LangGraph checkpoint/store tables remain library-owned and outside Alembic. Both may share one PostgreSQL database instance. Fresh startup is PostgreSQL -> `alembic upgrade head` -> LangGraph `setup()` -> application. Production application startup never auto-migrates. One-revision-at-a-time is only the development/test migration verification procedure; production follows forward migrations, does not use downgrade as automatic/routine recovery, and requires separate review for any destructive downgrade.

T021's `TASKPILOT_DATABASE_URL` is an explicit PostgreSQL-only business URL
(`postgresql+psycopg://`, with `postgresql://` accepted and normalized). It is
independent from `DATABASE_TYPE`, which still selects LangGraph's SQLite,
PostgreSQL, or Mongo backend. Alembic stores its version table as
`taskpilot.alembic_version` and creates `taskpilot` on a fresh database.
The live coexistence tests use one uniquely created disposable database per
scenario; they are not reported as passed unless a test PostgreSQL server is
available.

## Identity schema

- `organizations`: UUID4 `id`, `name`, `is_active`, UTC `created_at`, `updated_at`.

T021 implements only `organizations`; the first migration is
`migrations/versions/20260914_01_organization.py`. Names are non-null,
maximum 255 characters, and must contain at least one PostgreSQL-regex
non-whitespace character (spaces, tabs, and newlines are rejected when alone).
IDs are application-generated UUID4 values. `created_at`/`updated_at` use
timezone-aware UTC values and `is_active` defaults to true. The repository
flushes but never commits; service code owns transaction commit/rollback.

- `users`: UUID4 `id`, trimmed display `email`, non-null globally unique `normalized_email` produced by the shared trim + Unicode casefold helper, Argon2id `password_hash`, `is_active`, UTC `created_at`, `updated_at`. Hashes are never serialized; PostgreSQL `lower()`/collation is not the canonicalization mechanism.
- `memberships`: UUID4 `id`, unique `(user_id, organization_id)`, constrained role enum `owner|admin|member`, `is_active`, UTC `created_at`, `updated_at`, explicit foreign keys.
- `auth_sessions`: UUID4 `id`, indexed SHA-256 token hash, user/membership foreign keys, `expires_at`, `revoked_at`, UTC timestamps. Raw tokens are never stored.

T022 implements `users`; the second migration is
`migrations/versions/20260916_01_user.py` and its revision is `t022_user`
(`down_revision = t021_organization`). `email` keeps the trimmed display
string; `normalized_email` stores the same trimmed string after Unicode
`casefold` and is `NOT NULL` with a global `UNIQUE` constraint. `password_hash`
is only an opaque storage column in T022: hashing, verification, and login
belong to T023, and no password library is added here. The `User` model derives
`normalized_email` through `persistence.identity.canonicalize_email`, so
bootstrap, lookup, and future controlled identity creation share one contract.
`UserRepository` follows the same boundary as `OrganizationRepository`: it
queries, adds, and flushes, but never commits or closes the caller's session.
Deactivation is represented by `is_active`; no public hard delete exists.

T022A implements `memberships`; the third migration is
`migrations/versions/20260916_02_membership.py` and its revision is
`t022a_membership` (`down_revision = t022_user`). A membership is the formal
User-to-Organization link: UUID4 `id`, `user_id`, `organization_id`, a
constrained role from the frozen V1 set `owner|admin|member`, `is_active`, and
timezone-aware `created_at`/`updated_at`. The role is stored as a
`VARCHAR(16)` with a PostgreSQL check constraint instead of a native enum type,
so the database representation stays readable and no enum type has to be
created or dropped with the migration. `(user_id, organization_id)` is
globally `UNIQUE`, so duplicate membership cannot be created even while a
previous row is inactive; uniqueness is never emulated with a
`SELECT`-then-`INSERT` check. Both foreign keys are `ON DELETE RESTRICT`, which
prevents a user or organization removal from silently cascading into
authorization history. `MembershipRepository` follows the same transaction
boundary as the other repositories and exposes only scope-naming lookups
(`get_for_user_in_organization`, `list_for_organization`), never a global
membership listing.

`MembershipRepository.list_eligible_for_user` answers the login-selection
question inside PostgreSQL: it joins `memberships` to `organizations` and
requires the user scope, an active membership, and an existing active
organization. Login therefore never loads every membership and filters tenants
in Python. The service consumes at most one row, an ambiguous duplicate match
fails closed, and the `(user_id, organization_id)` unique constraint remains the
authority for that pair.

T023 implements `auth_sessions`; the fourth migration is
`migrations/versions/20260916_03_auth_session.py` and its revision is
`t023_auth_session` (`down_revision = t022a_membership`). A session is UUID4
`id`, `user_id`, `membership_id`, `token_hash`, `expires_at`, nullable
`revoked_at`, and UTC `created_at`/`updated_at`. `token_hash` stores only the
lowercase hex SHA-256 digest of the raw token, is `NOT NULL`, and is globally
`UNIQUE`; the raw token is returned exactly once at issuance and is never a
column, a parameter name, or a log field. Both foreign keys are
`ON DELETE RESTRICT`, so revocation (`revoked_at`) rather than deletion is the
V1 session lifecycle. Expiry is evaluated against aware UTC, and expired rows
can be removed by an explicit cleanup call; no maintenance job runs one
automatically yet. `AuthSessionRepository` follows the same boundary as the
other repositories and only ever looks a session up by digest.

Organization and user IDs are UUID4 generated by the application and exposed as strings. All business timestamps are timezone-aware UTC. V1 deactivates users, organizations, and memberships; it does not expose hard delete. Foreign keys use RESTRICT/NO ACTION for security and audit records. No silent cascade deletes approvals, sessions, or audit records.

## Transaction boundary

FastAPI dependencies acquire and close an async SQLAlchemy session. A service controls commit/rollback; repositories perform reads and writes without committing. Every query includes explicit tenant scope where applicable. Runtime contexts pass repositories to future Agent Runtime code; `AsyncSession` never enters `AgentState`.

## Other domains

Future task, run, approval, knowledge, memory, and audit tables must carry explicit organization ownership and follow the same FK, index, timestamp, and tenant-filter rules. The existing LangGraph SQLite/PostgreSQL adapters remain conversation/checkpoint infrastructure, not TaskPilot business persistence.

## Administrative bootstrap

The first Organization + owner User + owner Membership is created only by `scripts/bootstrap_owner.py`, which delegates to `service.bootstrap_cli` and `service.bootstrap`. The password is read exclusively from two hidden interactive `getpass` prompts - there is no flag, positional, or environment-variable form - and `--password` plus positional plaintext are rejected before any database work. Exact complete active state is an idempotent no-op; partial/conflicting/inactive state fails closed without repair, overwrite, password reset, or role elevation. Organization, User, password hash, and Membership share one business transaction: commit once or roll back all. No global consumed flag is used, and no HTTP registration endpoint exists.

## Verification (T021–T026)

The revision chain is linear and owned entirely by TaskPilot: `t021_organization` -> `t022_user` -> `t022a_membership` -> `t023_auth_session`, with the version table in `taskpilot.alembic_version`. `tests/persistence/test_postgres_integration.py` creates one uniquely named disposable database per scenario and proves fresh-DB creation, LangGraph coexistence in both setup orders, revision metadata and constraints, per-revision downgrade/re-upgrade, transaction rollback, independent sessions, and expired-session cleanup. `tests/persistence/test_foundation.py` asserts that no module under `src/` imports the Alembic toolchain, so application startup can neither migrate nor downgrade. `tests/persistence/test_security_matrix_integration.py` (T026) proves the identity and tenant rules against the same schema. All of these require `TASKPILOT_TEST_DATABASE_URL`; without it they skip and prove nothing.
