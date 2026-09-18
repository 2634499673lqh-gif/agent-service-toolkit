# Decision Log

Use only for non-trivial decisions that future contributors must understand.

## ADR-001 — Preserve upstream runtime while building TaskPilot additively

Date: 2026-09-10

Status: accepted

Context:
Phase 0 found an upstream FastAPI + LangGraph + Streamlit toolkit with optional SQLite/PostgreSQL/Mongo LangGraph persistence, but no TaskPilot identity or task domain. Replacing the runtime now would discard tested transport and graph examples while obscuring required product work.

Options:
1. Replace FastAPI/LangGraph/Streamlit and rebuild a new platform.
2. Preserve the upstream runtime and add TaskPilot domain/API/runtime concerns in small phases.
3. Treat the upstream chat/checkpoint models as the TaskPilot domain.

Decision:
Choose option 2. Keep FastAPI, LangGraph, existing checkpointer adapters, Streamlit for development/demo, and upstream attribution/reference files. Introduce TaskPilot business persistence and the dedicated planner/executor/verifier workflow only in the roadmap phases that specify them.

Why:
It minimizes speculative rewrites and follows the repository constraints. Option 3 would make unauthenticated caller-supplied `user_id` and conversation checkpoints act as business truth, which fails TaskPilot tenancy, lifecycle, audit and recovery requirements.

Consequences:
Positive:
- Existing service, streaming, persistence and test patterns remain available.
- Learners can distinguish upstream examples from TaskPilot product capabilities.

Negative:
- Upstream demo graphs will temporarily coexist with the future TaskPilot design.
- Phase 1 must verify the migration boundary between application tables and LangGraph-managed persistence.

Revisit when:
Measured retained-runtime limitations prevent TaskPilot semantics, or Phase 1 persistence verification shows the additive boundary is unsafe.

## ADR-000 Template

Date:
Status: proposed / accepted / superseded

Context:
What problem/constraint forced a decision?

Options:
1.
2.
3.

Decision:
What did we choose?

Why:
Why is this best for V1?

Consequences:
Positive:
Negative:

Revisit when:
What evidence would justify changing it?
## ADR-002 — Separate ownership for TaskPilot migrations and LangGraph persistence

Date: 2026-09-11
Status: accepted for Phase 1; the Phase 2 migration framework decision this record deferred was made by ADR-004 (SQLAlchemy 2.x async ORM plus Alembic owning only the `taskpilot` schema)

Context: The repository has no SQLAlchemy, Alembic, ORM, application migration directory, or TaskPilot business tables. SQLite is the lightweight local-development checkpoint path. `src/memory/postgres.py` calls LangGraph `AsyncPostgresSaver.setup()` and `AsyncPostgresStore.setup()`, which create and upgrade their LangGraph-owned persistence tables. PostgreSQL is the current LangGraph checkpoint/Store persistence option, not a TaskPilot business schema.

Decision: Keep LangGraph setup ownership separate from future TaskPilot application migrations. Do not add Alembic/ORM or business tables in Phase 1. LangGraph owns its internal persistence tables; TaskPilot will own future business tables. Neither owner may alter or migrate the other's tables. Before Phase 2 models, a strong design review must choose an application migration tool, schema/namespace, revision ownership, startup ordering, independent production migrations, downgrade policy, test-database strategy, and fresh-database coexistence verification.

Options considered: (1) introduce Alembic/ORM now, adding unused framework and schema coupling; (2) defer until the first approved TaskPilot domain schema; (3) manually manage business DDL. Option 2 is recommended because it preserves tested upstream persistence and avoids premature abstractions; option 3 is rejected for repeatability.

Consequences: Phase 1 has no migration command, ORM, migration directory, or business schema. Phase 2 must prove fresh database setup and coexistence before shipping models. Existing LangGraph `setup()` remains the library-managed prerequisite.

Revisit when: Phase 2 identity schema is approved or LangGraph setup conflicts with the selected application namespace.

## ADR-003 — Phase 2 identity planning gate (decision pending implementation)

Date: 2026-09-13

Status: closed; the T020 gate was completed under Strong Review and its accepted decisions are recorded in ADR-004

Context: The repository currently has an optional shared `AUTH_SECRET`, caller-asserted conversation `user_id` in `UserInput`/`/threads`, AG-UI forwarded configurable values, and no TaskPilot business persistence. PostgreSQL is currently owned by LangGraph checkpointer/Store setup; there is no ORM or migration framework. Treating these upstream values as business identity would permit cross-tenant access.

Decision: Create T020 as a mandatory architecture gate before any identity implementation. It must choose User/Organization/Membership topology, minimal role policy, identifier/lifecycle/email/bootstrap rules, authentication mechanism, password handling, migration/session/transaction architecture, and cross-tenant error semantics. T021–T027 may implement only those accepted decisions. The current `AUTH_SECRET` remains compatibility-only until a separately approved change.

Security invariants: authorization is credential → server-resolved user → server-resolved membership/org/role → resource tenant check. Client-supplied user/org IDs, role claims, and AG-UI configurable identity are never authoritative. LangGraph internal tables remain outside TaskPilot migrations.

Known drift: `ROADMAP.md` lists Phase 2 as T020–T025, while `TASK_BACKLOG.md` expands it to T020–T027. The backlog is authoritative; no roadmap rewrite is made in this planning task. Resolved by T027, which aligned the roadmap Phase 2 task list with the real cards.

Revisit when: T020 review supplies evidence that a different topology, migration owner, or credential strategy is required.

## ADR-004 — TaskPilot Phase 2 identity, tenancy, authentication and business persistence

Date: 2026-09-14
Status: accepted; the T020 gate was closed and Phase 2 was implemented, reviewed and verified through T026, with documentation synchronized by T027

### Context

The upstream service has an optional shared `AUTH_SECRET` bearer check, caller-supplied conversation `user_id`, AG-UI forwarded configuration, and LangGraph-managed checkpoint/store persistence. It has no TaskPilot business schema, ORM, migration history, or authenticated principal. These upstream values cannot establish tenant or authorization truth. V1 needs identity, organization isolation, approval authorization, task ownership, organization knowledge, and user memory while remaining small enough for a single PostgreSQL deployment and deterministic tests.

### Decisions

1. **Topology.** Use `users`, `organizations`, and a many-to-many `memberships` table. A user may belong to multiple organizations but has at most one active membership in a credential/session. A session is bound to one membership; switching organization issues a new session. The request principal therefore has one active organization. Membership work is a separate `T022A` card.
2. **Roles.** Use a constrained enum on membership: `owner`, `admin`, `member`. No role, permission, or policy tables in V1. Server policy is explicit and centralized. `owner` manages organization membership and bootstrap; `admin` manages organization-scoped tasks, knowledge, and approvals; `member` may create/read/update their permitted tasks and read/act on approvals only where policy grants it. All roles may use permitted task execution. L2 approval decisions require `owner` or `admin` and an active membership in the task organization; L3 is blocked in V1.
3. **Trust invariant.** `credential -> authenticated principal -> User -> active Membership -> Organization/Role -> resource organization_id -> authorization decision`. Request `user_id`, `organization_id`, role, `agent_config.user_id`, `/threads` query identity, and AG-UI configurable identity are never authorization truth. Forged values are ignored for scope selection; if they conflict with the authenticated principal, the request is rejected as a malformed/forbidden request without disclosing resource existence.
4. **Cross-tenant lookups.** Resource lookup outside the active organization consistently returns HTTP 404 with the normal error envelope. This hides existence and makes repository/API behavior uniform. A valid principal lacking a role for an in-tenant operation returns 403.
5. **Business persistence.** Add SQLAlchemy 2.x async ORM using the existing psycopg 3 driver (`postgresql+psycopg`), an async sessionmaker, and repository/service boundaries. FastAPI owns request session lifecycle; services own transaction scope; repositories never commit. Rollback occurs on service exception and session close. Agent Runtime obtains repositories through an explicit runtime context, never through `AgentState`.
6. **Migrations.** Add Alembic with an async-compatible env and `taskpilot` migration directory. TaskPilot revisions own only TaskPilot tables in PostgreSQL schema `taskpilot`; LangGraph revisions/setup own only their internal tables (normally `public`) and are never inspected, stamped, or altered by Alembic. Production deploys run `alembic upgrade head` as a release/job step before application startup; application startup never auto-migrates. “One revision at a time” is only the development/test migration verification procedure. Production uses forward migrations; downgrade is not an automatic or routine recovery mechanism, and any destructive production downgrade requires separate review.
7. **Database strategy.** TaskPilot business schema is PostgreSQL-only. SQLite remains supported only for upstream LangGraph local checkpoints. CI uses disposable PostgreSQL for migration/integration tests; model/unit tests may use transaction-isolated PostgreSQL fixtures. This preserves tenant constraints and migration parity instead of creating a second business-database dialect. LangGraph and TaskPilot share one PostgreSQL database instance but use separate schema ownership. Fresh bootstrap is: PostgreSQL -> TaskPilot `alembic upgrade head` -> LangGraph saver/store `setup()` -> application startup.
8. **Identifiers/lifecycle.** Organization, user, membership, and auth-session IDs are UUID4 generated in the application and stored as PostgreSQL `uuid`; they are exposed as strings in APIs. UUID4 is available on Python 3.12+, easy to generate in tests, and avoids sequential tenant enumeration. All timestamps are timezone-aware UTC (`timestamptz`), with `created_at` and `updated_at`; updates are service-managed. Users and memberships have `is_active`; organizations are active by default. V1 has no public delete endpoint: deactivate instead. Hard deletion is an administrative future decision. Foreign keys use `RESTRICT`/`NO ACTION` for audit/security records; no silent cascade across users, memberships, sessions, approvals, or audit records.
9. **Email/bootstrap.** Login uses one canonical application email function: trim Unicode whitespace, then Unicode `casefold`. Store the trimmed display email in `users.email` and the canonical value in non-null `users.normalized_email`, with a database global UNIQUE constraint on `normalized_email` (never PostgreSQL `lower()`/collation). Bootstrap, login lookup, and future controlled identity creation must call this same helper. Public self-registration is disabled. The first organization, owner User, and owner Membership are created only by a controlled CLI/setup command, not HTTP. The command prompts twice using hidden `getpass`-equivalent input; no plaintext password argument or positional password is accepted, and password/password hash never enter shell history or logs. Exact completed state is an idempotent no-op: no duplicate rows and no password reset. Partial or conflicting state fails closed with non-zero/clear error and never repairs, overwrites, changes password, or elevates role. The three rows and hash commit in one TaskPilot transaction, or all roll back. No global bootstrap-consumed flag is used; administrative execution authority is the control. Email rename/change is out of Phase 2.
10. **Authentication.** Use opaque server-side sessions/access tokens, not JWT. Generate at least 32 cryptographically random bytes, encode base64url, return the raw token only at login, store only a SHA-256 token hash with an indexed lookup, bind the session to user and membership, expire after 24 hours, and support explicit revocation plus cleanup of expired rows. Every request re-reads active user and membership state, so deactivation takes effect immediately. This is simpler to revoke and test than JWT and avoids trusting stale role claims while remaining browser-compatible.
11. **Passwords.** Password login uses Argon2id through the approved `pwdlib[argon2]` dependency added by T023 (no dependency is added in T020). Verification uses the library constant-time API; plaintext exists only in request memory, hashes never enter responses/logs, and generic login failure covers unknown email, wrong password, inactive user, and inactive membership. Rehash-on-login is deferred unless the library reports a needed upgrade.
12. **Legacy `AUTH_SECRET`.** Preserve current upstream behavior unchanged for existing demo endpoints while it remains configured. It is compatibility-only, does not create a User/Organization/Role principal, and is never accepted by `/api/v1` TaskPilot endpoints. Document deprecation and remove only in a later compatibility milestone after upstream clients migrate. Tests retain the existing bearer compatibility cases and separately prove TaskPilot endpoints reject it as an identity credential.
13. **Principal contract.** `CurrentPrincipal` contains `user_id`, `membership_id`, `organization_id`, `role`, and `session_id`. Every request resolution re-queries the server database and requires: session exists, not revoked, not expired, user active, membership active, and organization active. Organization and role come from the current verified Membership/Organization rows, never session-cache state or claims. Any failed condition yields the same 401 invalid/inactive credential and no principal or protected-resource access is constructed.
14. **Authorization helper contract.** T025 exposes policy operations for `require_authenticated`, `require_active_membership`, `require_role(roles)`, and `require_resource_tenant(resource_organization_id)`. Repository methods require the principal organization scope and include explicit `organization_id` predicates. Approval reads/decisions use the same helper and ownership checks; duplicate decisions are rejected idempotently.
15. **HTTP errors.** Missing, malformed, invalid, expired, revoked, inactive-user, or inactive-membership credentials return 401 with one generic authentication error. In-tenant insufficient role returns 403. Cross-tenant or nonexistent resource lookup returns 404. Login never distinguishes unknown email, password mismatch, or inactive identity. No error includes token, password, hash, or existence details.

### T023 membership selection addendum — frozen 2026-09-16

Gap confirmed: the topology binds each session to one Membership but did not specify how login selects it. The current `AuthService._resolve_membership` rejects any membership list whose length is not one, including valid multi-organization users and one eligible plus one inactive membership. This addendum resolves only selection; other ADR-004 decisions remain unchanged.

Choose option A: a service-level selection-required result after successful password authentication, followed by a fresh login with an explicit organization selector. No chooser token, temporary credential, new endpoint, or UI is required.

Freeze the async service signature as `AuthService.login(email: str, password: str, *, organization_id: UUID | None = None) -> AuthenticatedSession | OrganizationSelectionRequired`. Existing two-argument calls remain valid. Keep `AuthenticatedSession` unchanged for successful issuance. Define an immutable `OrganizationSelectionRequired` result with `code = "ORGANIZATION_SELECTION_REQUIRED"` and `organization_ids: tuple[UUID, ...]`, containing only eligible organization IDs, deduplicated and sorted by canonical UUID string. IDs alone suffice to resubmit this service request; omit names, roles, membership IDs, user details, counts of excluded rows, and credentials. This result grants no authority, creates no AuthSession, and does not generate a token. A future HTTP mapping is outside this decision.

Selection procedure:

1. Canonicalize email using the existing helper, verify the password, derive User from the server repository, and require User active. Unknown email, wrong password, malformed credentials, and inactive User continue to raise `LoginError` with exactly `Invalid credentials`; they return no organization metadata. Do not query memberships or branch on the selector before this authentication succeeds.
2. Query memberships scoped to that User. An eligible row must satisfy `membership.user_id == user.id`, active Membership, and an existing active Organization whose ID matches `membership.organization_id`. Check ownership even when consuming repository results; never count unfiltered memberships. Missing, inactive, or foreign rows are ineligible.
3. With `organization_id` supplied, select only an eligible row matching that UUID. Invalid/malformed selector, unknown organization, other user's membership, inactive membership/organization, or no match raises the same generic `LoginError("Invalid credentials")` externally, without a selection list or explanation. Never fall back to another membership. Ambiguous duplicate matches fail closed; existing uniqueness constraints remain authoritative.
4. Without a selector: zero eligible rows gives generic login failure; one issues a session for that row; more than one returns `OrganizationSelectionRequired`. Never pick first, owner-first, or another implicit default. Thus active Membership A plus inactive Membership B automatically selects A, provided A's Organization is active; an active membership in an inactive Organization also does not count.
5. On resubmission, require email/password again and re-query current eligibility before issuance. A previous result/list is not proof of membership or a credential. Bind AuthSession.user_id and AuthSession.membership_id only from verified server rows; derive organization from the selected Membership. No caller-supplied membership ID or role is accepted by this service signature; caller role cannot influence selection. Existing T024 fresh active-state checks remain required.

`organization_id` is a requested context selector, never authorization truth. The earlier trust invariant's ban on caller-selected authorization scope still applies to resource requests and upstream caller-asserted identity; it does not prohibit this password-authenticated, server-verified login selector. Only the resulting session identifies the verified active membership.

Organization switching repeats login with credentials and the desired organization selector and issues a new session bound to that verified Membership. Never mutate the old session's membership. Issuance alone does not revoke existing sessions; they retain their original scope until explicit revocation or expiry. No switching endpoint or CurrentPrincipal implementation is introduced here.

Security rationale: only a password-authenticated active User sees their own eligible organization IDs. Invalid selectors disclose neither another tenant's existence nor its members. Generic credential failure is distinct from authenticated selection-required, which is an intermediate result without resource access. Reauthentication and fresh server queries avoid chooser-credential lifecycle and stale selection authority while preserving multi-organization login.

### Alternatives and rejected options

- One organization per user was simpler but blocks enterprise extension, organization knowledge sharing, and an explicit active-organization session without a migration redesign; rejected.
- Normalized role/permission tables or a policy engine would add joins and policy drift without a V1 need; rejected until measured requirements demand custom permissions.
- JWT would reduce a database lookup but complicate revocation, stale role handling, and key rotation; rejected for this small server-rendered/browser-compatible V1.
- SQLite business persistence would ease local setup but create dialect and constraint/migration parity gaps; rejected.
- Separate databases would increase operations and make a single local stack harder; rejected.
- Application auto-migration would make rollback and multi-instance startup unsafe; rejected.

### Exact implementation ownership

- T021: SQLAlchemy/Alembic foundation, organization model, async session factory and migration verification.
- T022: user model/password-hash field and email constraints.
- T022A: membership model, role enum, active-membership constraints and repositories.
- T023: opaque session/token issuance, Argon2id login, revocation and bootstrap command.
- T024: `CurrentPrincipal` FastAPI dependency and session lifecycle integration.
- T025: centralized authorization helper and tenant-filter policy.
- T026: negative/security/transaction/migration test matrix.
- T027: documentation synchronization after implementation.

### Consequences and deferred decisions

This keeps V1 auditable: one active organization per request, server-derived roles, explicit PostgreSQL ownership, and revocable credentials. It adds a membership lookup and PostgreSQL requirement for business data. Enterprise SSO, refresh-token families, email verification, email rename, custom permissions, organization switching UI, hard deletion, row-level security, key rotation, and L3 execution remain deferred. Revisit only if enterprise identity requirements, measured scale, or a PostgreSQL operational constraint provide evidence that this topology or token model is insufficient.

### Phase 2 implementation status — closed 2026-09-18

Implemented as decided above: `organizations`, `users`, `memberships`, and `auth_sessions` in the `taskpilot` schema through four linear Alembic revisions; Argon2id passwords; opaque 24-hour sessions with SHA-256 at-rest digests, revocation, and expiry; the controlled `scripts/bootstrap_owner.py` CLI; multi-organization login selection as an intermediate result; the server-derived `CurrentPrincipal`; the single authorization boundary with fixed 401/403/404 semantics; tenant-scoped repository lookups; and the T026 live-PostgreSQL security matrix. All four verified revisions are owned by TaskPilot, and no `src/` module imports Alembic, so application startup neither migrates nor downgrades.

Explicitly still absent after Phase 2: TaskPilot HTTP endpoints (`/api/v1`, login, `/me`), Task/TaskRun/TaskStep records, planner/executor/verifier behavior, approval records and duplicate-decision idempotency, permission/role tables, a policy engine, JWT or refresh tokens, organization-switch endpoints or UI, and TaskPilot observability/audit tables. These are unimplemented scope, not defects in the accepted design. Decision 12 remains in force: `AUTH_SECRET` is compatibility-only for the retained upstream routes, and a TaskPilot protected dependency rejects it with the same generic 401 as any unknown token.

## ADR-005 — Phase 3 Task domain boundary and lifecycle

Date: 2026-09-18

Status: proposed; architecture gate is the focused Planning Strong Review. Once approved, ADR-005 is Accepted and Phase 3 implementation starts at T031.

### Decisions

1. **Source of truth.** Persist `Task` and `TaskRun`. `Task.status` is the current user-visible overall execution state; `TaskRun.status` is the lifecycle of one concrete execution attempt. Historical terminal runs remain immutable history except for their legal transition. `TaskStep` is deferred to Phase 4 runtime design and is not a Phase 3 entity.
2. **States.** Task: `DRAFT`, `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`. TaskRun: `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, `CANCELLED`.
3. **Lifecycle.** Creating a Task yields `DRAFT` with no run. Starting is allowed only from `DRAFT` or `FAILED`, creates `PENDING` and changes Task to `QUEUED` in one service transaction. `PENDING→RUNNING`, `RUNNING→SUCCEEDED`, and `RUNNING→FAILED` update the owning Task (`QUEUED→RUNNING`, `RUNNING→SUCCEEDED`, `RUNNING→FAILED`) in the same transaction. No automatic retry. `QUEUED`, `RUNNING`, `SUCCEEDED`, and `CANCELLED` reject start; `FAILED` permits retry.
4. **Active-run invariant.** An active run is `PENDING` or `RUNNING`; each Task has at most one active run, while multiple terminal historical runs are allowed. `QUEUED` requires exactly one `PENDING` run and `RUNNING` exactly one `RUNNING` run. The service/persistence implementation must enforce this transactionally; the invariant does not prescribe infrastructure.
5. **Cancellation.** Cancellation is persistence-only and never claims to interrupt external execution. `DRAFT→CANCELLED` has no run. `QUEUED`/`RUNNING` cancel the active run and Task together in one transaction. `SUCCEEDED`/`FAILED` reject with conflict. Repeated cancellation returns the already-cancelled resource without a new state change. `CANCELLED` cannot restart.
6. **Ownership/security.** Task stores server-derived organization and creator provenance. `CurrentPrincipal` supplies authorization truth; caller identity fields never do. Tenant predicates remain in repository queries. API semantics are 401 invalid auth, 403 same-tenant insufficient authorization, and 404 foreign/non-visible resources.
7. **Transactions and runtime boundary.** Services own commit/rollback; repositories query/add/flush only. Phase 3 stores no planner, executor, verifier, LangGraph, AgentState, or external-runtime interruption fields. PostgreSQL constraints/transactions enforce domain invariants.
8. **Application idempotency deferred.** Phase 3 has no concrete external retry boundary, so Task create and TaskRun start have no `Idempotency-Key`, fingerprint, replay, conflict, or idempotency storage contract. Concurrency safety, one active run, run numbering, and legal transitions remain mandatory domain/database invariants. Application-level idempotency is designed when a concrete retry-producing boundary exists.
9. **Architecture ownership.** T030 is the non-production planning milestone completed by the approved Planning Strong Review. It does not perform a second architecture freeze. After that single gate, ADR-005 is Accepted and T031 begins production implementation.

### Migration and verification

The executable order is Task → TaskRun, then tenant repositories, lifecycle service, APIs, and final audit. T033 is `DEFERRED`: TaskStep persistence belongs to Phase 4 runtime design and is not an executable Phase 3 dependency. Production startup never auto-migrates; release/job migration remains the owner. SQLite is not a TaskPilot business backend.
