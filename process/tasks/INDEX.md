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
