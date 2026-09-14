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
Status: accepted for Phase 1; Phase 2 migration framework decision pending

Context: The repository has no SQLAlchemy, Alembic, ORM, application migration directory, or TaskPilot business tables. SQLite is the lightweight local-development checkpoint path. `src/memory/postgres.py` calls LangGraph `AsyncPostgresSaver.setup()` and `AsyncPostgresStore.setup()`, which create and upgrade their LangGraph-owned persistence tables. PostgreSQL is the current LangGraph checkpoint/Store persistence option, not a TaskPilot business schema.

Decision: Keep LangGraph setup ownership separate from future TaskPilot application migrations. Do not add Alembic/ORM or business tables in Phase 1. LangGraph owns its internal persistence tables; TaskPilot will own future business tables. Neither owner may alter or migrate the other's tables. Before Phase 2 models, a strong design review must choose an application migration tool, schema/namespace, revision ownership, startup ordering, independent production migrations, downgrade policy, test-database strategy, and fresh-database coexistence verification.

Options considered: (1) introduce Alembic/ORM now, adding unused framework and schema coupling; (2) defer until the first approved TaskPilot domain schema; (3) manually manage business DDL. Option 2 is recommended because it preserves tested upstream persistence and avoids premature abstractions; option 3 is rejected for repeatability.

Consequences: Phase 1 has no migration command, ORM, migration directory, or business schema. Phase 2 must prove fresh database setup and coexistence before shipping models. Existing LangGraph `setup()` remains the library-managed prerequisite.

Revisit when: Phase 2 identity schema is approved or LangGraph setup conflicts with the selected application namespace.

## ADR-003 — Phase 2 identity planning gate (decision pending implementation)

Date: 2026-09-13

Status: proposed; T020 strong-model review required

Context: The repository currently has an optional shared `AUTH_SECRET`, caller-asserted conversation `user_id` in `UserInput`/`/threads`, AG-UI forwarded configurable values, and no TaskPilot business persistence. PostgreSQL is currently owned by LangGraph checkpointer/Store setup; there is no ORM or migration framework. Treating these upstream values as business identity would permit cross-tenant access.

Decision: Create T020 as a mandatory architecture gate before any identity implementation. It must choose User/Organization/Membership topology, minimal role policy, identifier/lifecycle/email/bootstrap rules, authentication mechanism, password handling, migration/session/transaction architecture, and cross-tenant error semantics. T021–T027 may implement only those accepted decisions. The current `AUTH_SECRET` remains compatibility-only until a separately approved change.

Security invariants: authorization is credential → server-resolved user → server-resolved membership/org/role → resource tenant check. Client-supplied user/org IDs, role claims, and AG-UI configurable identity are never authoritative. LangGraph internal tables remain outside TaskPilot migrations.

Known drift: `ROADMAP.md` lists Phase 2 as T020–T025, while `TASK_BACKLOG.md` expands it to T020–T027. The backlog is authoritative; no roadmap rewrite is made in this planning task.

Revisit when: T020 review supplies evidence that a different topology, migration owner, or credential strategy is required.

## ADR-004 — TaskPilot Phase 2 identity, tenancy, authentication and business persistence

Date: 2026-09-14
Status: accepted pending Strong Review (T020 architecture gate; review fixes applied 2026-09-14)

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
