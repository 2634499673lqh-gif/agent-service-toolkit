# Database Design Guide

This document records the Phase 2 identity architecture and the T021–T023 schema, the Phase 3 T031/T032/T034 Task domain, T081/T082 Approval persistence and decision services, and the Phase 7 ADR-009 persistence implementation. ADR-004, ADR-005, accepted ADR-008, and accepted/frozen ADR-009 are authoritative for their respective domains.

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

## Task domain foundation (T031)

`tasks` is the first Phase 3 business table. Each row stores a UUID4 `id`,
server-derived `organization_id`, server-derived `created_by_user_id`, a
non-blank `title`, optional `description`, the user-visible `status`, and
UTC `created_at`/`updated_at` timestamps. New rows default to `DRAFT`; a new
Task has no TaskRun. Status is stored as readable lowercase text and is
constrained to `draft`, `queued`, `running`, `succeeded`, `failed`, or
`cancelled`. Organization and creator foreign keys use `RESTRICT`, and the
organization, creator, and status columns are indexed for later tenant-scoped
queries. T034 adds tenant-scoped repository queries, and T035 adds the
service-owned lifecycle transitions. The lifecycle service commits successful
operations and rolls back failures; the caller still owns session close.

## TaskRun persistence foundation (T032)

`task_runs` stores one durable execution attempt for a Task. Each row has a
UUID4 `id`, a non-null `task_id` foreign key to `tasks`, a positive per-Task
`run_number`, a readable lowercase `status`, and UTC `created_at`/
`updated_at` timestamps. New rows default to `PENDING`. `(task_id,
run_number)` is unique, so run numbers are immutable persistence identities
within a Task and terminal history can contain multiple rows. PostgreSQL also
owns a partial unique index over `task_id` for `pending` and `running` rows;
this enforces at most one active run per Task. T035 still owns legal lifecycle
transitions and synchronization between Task and TaskRun states.

## Approval persistence foundation (T081)

`approvals` stores one bounded approval record for a TaskRun plan slot. Its
canonical identity is unique on `(task_run_id, replan_count, step_position)`;
`step_position` is zero-based and corresponds to one-based
`ExecutionResult.step_position`. The row stores the complete bounded JSONB
proposal, the L2-only risk level, requester/optional decider memberships,
decision state and timestamps, and the bounded terminal action outcome. Named
database checks constrain ranges, enums, JSON object types, decision fields,
and outcome shape. The ORM rejects non-object/non-JSON values and proposals or
outcomes whose canonical compact UTF-8 JSON exceeds 8,192 bytes.

Ownership is normalized through `approvals.task_run_id -> task_runs.task_id ->
tasks.organization_id`; Approval has no duplicate Task or tenant column. The
run and membership foreign keys use `ON DELETE RESTRICT`. The
`(task_run_id, status)` index supports run/status lookup. `ApprovalRepository`
keeps the full TaskRun-to-Task join and principal organization predicate in
every read query; `add()` flushes but leaves commit/rollback to its caller.
T082's `ApprovalService` owns create/reuse and decision transactions, locking
Task -> TaskRun -> Approval, and commits or rolls back the supplied session.
Its protected nested read/decision routes return sanitized proposal and
decision fields. Create/reuse is service-only: trusted runtime wiring supplies
the already selected action and validated arguments. T083 implements runtime
pause/resume; T084 implements the bounded action claim and deterministic mock outcome; generic effect infrastructure remains deferred.

## Phase 7 observability persistence (ADR-009; T091/T092 implementation)

T091 and T092 add only the normalized AgentRun and ToolCall evidence described
by ADR-009 and implemented in the T034 migration. AgentRun owns a required
`task_run_id`; ToolCall owns a required `agent_run_id`. Tenant ownership is always resolved through
`ToolCall -> AgentRun -> TaskRun -> Task -> organization_id`; trace tables do
not duplicate `organization_id` or `task_id`. Both foreign-key paths use
`ON DELETE RESTRICT`, repositories flush without committing, and the service
transaction owns commit/rollback.

AgentRun uniqueness is
`(task_run_id, replan_count, step_position, retry_count, agent_name)`; its
repository validates the TaskRun parent in the trusted tenant scope before
flushing. ToolCall uniqueness is `(agent_run_id, call_index)`; its repository
validates the AgentRun -> TaskRun -> Task parent before flushing. Step position
is zero-based because TaskStep persistence remains deferred. All timing is UTC;
duration is a nullable bounded integer in milliseconds; errors, provider
metadata, arguments, and results are bounded and sanitized at the model
boundary. T092 owns
persisted-payload redaction tests. T097 owns the SQL-tenant-scoped ordered
timeline and response-redaction tests. T098 is a separate read-only Final
Audit and does not add tests or fixes.

## Tenant-scoped repository boundary (T034)

`TaskRepository` requires a trusted organization scope for Task lookups and
listings. `TaskRunRepository` joins `task_runs` to `tasks` and applies the
organization predicate to the Task row; TaskRun does not duplicate
`organization_id`. Foreign or nonexistent resources therefore return the same
`None`/empty result at the repository visibility boundary. Repository `add`
operations flush into the service-owned business transaction but never commit,
rollback, close, or replace the caller's `AsyncSession`.

## Lifecycle service (T035)

`TaskLifecycleService` locks the visible Task row before each decision, allocates
the next run number under that lock, and synchronizes the required active
TaskRun transition. It supports explicit start/retry, begin, success, failure,
and persistence-only cancellation. It never claims to interrupt external
runtime work and never closes the caller's session. Successful lifecycle
operations commit atomically and failed operations roll back through the
service boundary.

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

Future knowledge, memory, and audit tables must carry explicit organization ownership and follow the same FK, index, timestamp, and tenant-filter rules. Task, TaskRun, and Approval ownership is already represented by the normalized TaskRun-to-Task path described above. The existing LangGraph SQLite/PostgreSQL adapters remain conversation/checkpoint infrastructure, not TaskPilot business persistence.

Phase 4 planning (proposed ADR-006/T040) preserves this boundary: no
TaskStep table or migration is introduced; LangGraph checkpoint/store tables
remain independently initialized and owned. A checkpoint thread identifier
derived from a tenant-validated TaskRun is correlation data only, never a
tenant or authorization selector. Runtime services use discrete TaskPilot
transactions through T035 and do not hold a PostgreSQL transaction open across
graph/model execution.

## Administrative bootstrap

The first Organization + owner User + owner Membership is created only by `scripts/bootstrap_owner.py`, which delegates to `service.bootstrap_cli` and `service.bootstrap`. The password is read exclusively from two hidden interactive `getpass` prompts - there is no flag, positional, or environment-variable form - and `--password` plus positional plaintext are rejected before any database work. Exact complete active state is an idempotent no-op; partial/conflicting/inactive state fails closed without repair, overwrite, password reset, or role elevation. Organization, User, password hash, and Membership share one business transaction: commit once or roll back all. No global consumed flag is used, and no HTTP registration endpoint exists.

## Verification (T021–T026)

The revision chain is linear and owned entirely by TaskPilot: `t021_organization` -> `t022_user` -> `t022a_membership` -> `t023_auth_session` -> `t031_task` -> `t032_task_run` -> `t033_approval`, with the version table in `taskpilot.alembic_version`. `tests/persistence/test_postgres_integration.py` creates one uniquely named disposable database per scenario and proves fresh-DB creation, LangGraph coexistence in both setup orders, revision metadata and constraints, per-revision downgrade/re-upgrade, tenant scoping, and transaction rollback. `tests/persistence/test_foundation.py` asserts model/repository contracts and that no module under `src/` imports the Alembic toolchain, so application startup can neither migrate nor downgrade. `tests/persistence/test_security_matrix_integration.py` (T026) proves the identity and tenant rules against the same schema. All database-backed checks require `TASKPILOT_TEST_DATABASE_URL`; without it they skip and prove nothing.
