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
## ADR-002 — Future TaskPilot migrations alongside LangGraph PostgreSQL persistence

Date: 2026-09-11
Status: proposed for Phase 1 review

Context: The repository has no SQLAlchemy, Alembic, ORM, application migration directory, or TaskPilot business tables. `src/memory/postgres.py` calls LangGraph `AsyncPostgresSaver.setup()` and `AsyncPostgresStore.setup()`, which own their persistence tables. PostgreSQL is already verified for checkpoint and Store use.

Decision proposal: Keep LangGraph setup ownership separate from future TaskPilot application migrations. Do not add Alembic/ORM or business tables in Phase 1. Before Phase 2 models, a strong design review must choose an application migration tool, a dedicated application schema or clearly namespaced tables, revision ownership, startup ordering, and fresh-database verification. LangGraph tables must never be altered by application revisions.

Options considered: (1) introduce Alembic/ORM now, adding unused framework and schema coupling; (2) defer until the first approved TaskPilot domain schema; (3) manually manage business DDL. Option 2 is recommended because it preserves tested upstream persistence and avoids premature abstractions; option 3 is rejected for repeatability.

Consequences: Phase 1 has no migration command or business schema. Phase 2 must prove fresh database setup and coexistence before shipping models. Existing LangGraph `setup()` remains the library-managed prerequisite.

Revisit when: Phase 2 identity schema is approved or LangGraph setup conflicts with the selected application namespace.
