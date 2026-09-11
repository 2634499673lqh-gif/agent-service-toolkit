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
