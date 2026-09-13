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

Recommended order: T010 → T011 → T012 → T013 → T014 → T015 → T016. T015 is documentation/architecture verification only; it must not add Alembic, ORM, or business tables. The first safe low-cost task is T010.
