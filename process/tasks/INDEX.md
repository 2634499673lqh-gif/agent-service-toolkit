# Task Card Index

## Phase 1

See the historical Phase 1 cards T010–T016.

## Phase 2 Task Cards — Identity / Organization / RBAC / Tenant Isolation

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T020 | Identity, tenancy, authentication and business persistence architecture gate | STRONG | STRONG_REVIEW_REQUIRED | Phase 1 | No (design evidence) |
| T021 | SQLAlchemy async foundation, Organization schema and Alembic migration | STANDARD | STRONG_REVIEW_REQUIRED | T020 | Focused + shared persistence |
| T022 | User schema, password field and email constraints | STANDARD | STRONG_REVIEW_REQUIRED | T020, T021 | Focused + shared persistence |
| T022A | Membership schema, role enum, active membership and repositories | STANDARD | STRONG_REVIEW_REQUIRED | T021, T022 | Focused + shared persistence |
| T023 | Opaque authentication session/token service, Argon2id login and bootstrap | STANDARD | STRONG_REVIEW_REQUIRED | T021, T022, T022A | Auth-focused; full at gate |
| T024 | CurrentPrincipal FastAPI dependency and request session lifecycle | STANDARD | STRONG_REVIEW_REQUIRED | T023 | Auth/API-focused |
| T025 | Central authorization helper and tenant policy | STANDARD | STRONG_REVIEW_REQUIRED | T024 | Authorization matrix; full at gate |
| T026 | Negative authentication, tenant, transaction and migration tests | STANDARD | STRONG_REVIEW_REQUIRED | T023–T025 | Yes |
| T027 | Security/API/database documentation synchronization | LOW_COST | None | T020–T026 | Focused docs checks |

Recommended order: T020 (Strong Review) -> T021 -> T022 -> T022A -> T023 -> T024 -> T025 -> T026 -> T027. T020 is authoritative; implementation cards may not change identity, token, migration, transaction, tenant, or error semantics without a new accepted ADR.

## Phase 2 completion status (2026-09-18)

T020 was accepted as ADR-004. T021–T026 are implemented, reviewed and committed; T027 synchronizes the documentation with the implementation. No Phase 3 task has started: there is no Task/TaskRun/TaskStep domain, no approval records, and no TaskPilot HTTP endpoint yet.

The Phase 2 definitions of done were verified at the T026 gate: `uv run pytest` (full suite, including the disposable-PostgreSQL persistence and security suites when `TASKPILOT_TEST_DATABASE_URL` is set), `uv run ruff format --check .`, `uv run ruff check --output-format concise`, `uv run pyrefly check`, `uv lock --check`, and `git diff --check`.

## Phase 3 Task Cards — Task domain

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T030 | Task domain/lifecycle architecture and ADR-005 | STRONG | STRONG_REVIEW_REQUIRED | Phase 2 | No runtime |
| T031 | Task schema and migration | STANDARD | STRONG_REVIEW_REQUIRED | T030 | Migration gate |
| T032 | TaskRun schema and migration | LOW_COST | STRONG_REVIEW_REQUIRED | T031 | Migration gate |
| T033 | DEFERRED: TaskStep to Phase 4 runtime design | — | — | — | No Phase 3 implementation |
| T034 | Tenant-scoped repositories/transactions | STANDARD | STRONG_REVIEW_REQUIRED | T032 | Focused + shared |
| T035 | Lifecycle transition service | STANDARD | STRONG_REVIEW_REQUIRED | T034 | Focused + shared |
| T036 | Task create/list/get API | LOW_COST | STRONG_REVIEW_REQUIRED | T035 | Focused |
| T037 | Task update/cancel API | LOW_COST | STRONG_REVIEW_REQUIRED | T036 | Focused |
| T038 | TaskRun start/inspect | STANDARD | STRONG_REVIEW_REQUIRED | T037 | Concurrency gate |
| T039 | Integration tests and documentation | STANDARD | STRONG_REVIEW_REQUIRED | T030–T038 | Full suite |

Dependency graph: `T031 → T032 → T034 → T035 → T036 → T037 → T038 → T039`. T030 is the approved planning gate; T033 is deferred and not an executable dependency. T039 is the Phase 3 final audit gate.
