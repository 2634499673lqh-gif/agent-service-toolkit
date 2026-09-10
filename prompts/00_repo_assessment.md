# Phase 0 Prompt — Repository Assessment [STRONG MODEL]

## Goal

Do not build major features yet.

Inspect the current repository as a senior engineer and determine how to turn it into TaskPilot with minimal unnecessary rewrites.

## Required work

1. Read `AGENTS.md`, `MASTER_PROMPT.md`, `PROJECT_SPEC.md`, `ROADMAP.md`.
2. Inspect repository tree and existing documentation.
3. Identify:
   - app entry
   - FastAPI service
   - existing agents
   - LangGraph state/checkpointer/store
   - DB setup
   - auth if any
   - RAG
   - HITL
   - tracing
   - tests
   - Docker
   - frontend
4. Run the documented baseline install/test/start checks that are feasible in this environment.
5. Do NOT hide failing baseline tests. Record them.
6. Compare current architecture with TaskPilot V1 requirements.
7. Produce/update:
   - `docs/ARCHITECTURE.md`
   - `docs/CODE_READING_ORDER.md` with exact real paths
   - `docs/DEVELOPER_GUIDE.md` with verified commands
   - `process/DECISION_LOG.md` only for decisions actually needed
   - `process/PROGRESS_LOG.md`
8. Create a gap table with:
   - requirement
   - existing support
   - reusable code
   - missing work
   - risk
   - proposed phase
9. Recommend whether to keep existing Streamlit for V1.
10. Recommend the smallest Phase 1 changes.

## Do not

- implement auth
- redesign DB wholesale
- replace LangGraph
- add Next.js
- introduce Redis/Kafka/K8s
- create many Agents

## Acceptance criteria

- Baseline commands and results are documented.
- Existing reusable modules are named by path.
- Major unknowns are listed.
- Architecture delta is explicit.
- Code reading order uses exact paths.
- No unrelated feature work was performed.

Stop after reporting Phase 0.
