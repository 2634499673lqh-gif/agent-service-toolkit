# Phase 1 Task Card Index

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T010 | Branding/attribution | LOW_COST | None | None | No |
| T011 | Safe env template | LOW_COST | None | T010 context | No |
| T012 | Settings validation | STANDARD | STRONG_REVIEW_REQUIRED | T011 | Yes |
| T013 | Request correlation middleware | LOW_COST | STRONG_REVIEW_REQUIRED | T012 | Yes |
| T014 | Structured logging/redaction | STANDARD | STRONG_REVIEW_REQUIRED | T013 | Yes |
| T015 | Migration ownership ADR | STRONG_REVIEW_REQUIRED | STRONG_REVIEW_REQUIRED | None | Phase exit |
| T016 | Verified developer commands | LOW_COST | None | T010-T015 docs | Phase exit |

## Phase 2 Task Cards — Identity / Organization / RBAC / Tenant Isolation

| Task | Purpose | Implementation Model | Review Model | Depends On | Full Pytest? |
|---|---|---|---|---|---|
| T020 | Identity domain and migration architecture decision | STRONG | STRONG_REVIEW_REQUIRED | Phase 1 | No (design evidence) |
| T021 | Organization schema, repository and migration | STANDARD | STRONG_REVIEW_REQUIRED | T020 | Focused + shared persistence |
| T022 | User, membership and role schema, repository and migration | STANDARD | STRONG_REVIEW_REQUIRED | T020, T021 | Focused + shared persistence |
| T023 | Authentication credential and login service | STANDARD | STRONG_REVIEW_REQUIRED | T021, T022 | Auth-focused; full at gate |
| T024 | Current authenticated user dependency | STANDARD | STRONG_REVIEW_REQUIRED | T023 | Auth/API-focused |
| T025 | Tenant authorization policy/helper | STANDARD | STRONG_REVIEW_REQUIRED | T024 | Authorization matrix; full at gate |
| T026 | Negative authentication and tenant-isolation tests | LOW_COST | STRONG_REVIEW_REQUIRED | T023–T025 | Yes |
| T027 | Security/API/database documentation synchronization | LOW_COST | None | T020–T026 | Focused docs checks |

Recommended order: T020 → T021 → T022 → T023 → T024 → T025 → T026 → T027. T020 is a planning gate: no implementation card may choose a different identity, ID, migration, transaction, or authorization model without revising T020 and obtaining strong review. The roadmap still contains the older Phase 2 labels T020–T025; `TASK_BACKLOG.md` is authoritative for the expanded T020–T027 sequence.

Recommended order: T010 → T011 → T012 → T013 → T014 → T015 → T016. T015 is documentation/architecture verification only; it must not add Alembic, ORM, or business tables. The first safe low-cost task is T010.

Full-regression gates: run full `uv run pytest` after the migration foundation (T021/T022), after authentication (T023), after tenant authorization (T025), and as the Phase 2 Final Audit before T027 closes. Focused tests are sufficient for intermediate cards.
