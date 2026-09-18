# Code Reading Order — Phase 0 baseline

Read production code in this order. These paths exist in the assessed repository on 2026-09-10. The early files describe the upstream chat service, not yet TaskPilot's future task domain.

1. `AGENTS.md` — TaskPilot constraints; it explains why plans must not be confused with implemented behavior.
2. `prompts/00_repo_assessment.md` — the Phase 0 acceptance scope.
3. `README.md` then `README_UPSTREAM.md` — TaskPilot package guidance followed by retained upstream operating/reference documentation.
4. `PROJECT_SPEC.md` and `ROADMAP.md` — learn the target and phase boundaries before judging demo graphs.
5. `pyproject.toml` and `.env.example` — dependencies, Python range, configuration and persistence choices.
6. `src/run_service.py` — executable FastAPI launch point and Windows event-loop choice.
7. `src/service/service.py` — app lifespan, shared-secret guard, chat/SSE routes, graph invocation, checkpoint history and feedback.
8. `src/schema/schema.py` — actual API boundary models. `UserInput.user_id` is not authenticated identity.
9. `src/agents/agents.py` — every registered graph and the default `research-assistant`; this maps URL `agent_id` to a graph.
10. `src/agents/research_assistant.py` — best first concrete graph: guard -> model -> optional `ToolNode` loop -> end. Learn state, nodes, edges and tools.
11. `src/agents/tools.py` — calculator and Chroma retrieval; illustrates why the latter needs tenancy/provenance work.
12. `src/memory/__init__.py`, `src/memory/sqlite.py`, then `src/memory/postgres.py` — backend selection and what persists.
13. `src/agents/interrupt_agent.py` and `src/service/agui.py` — technical interrupt/resume and AG-UI after normal requests; neither is an approval flow.
14. `src/agents/rag_assistant.py` and `docs/RAG_Assistant.md` — Chroma RAG prototype, not a tenant-scoped knowledge service.
15. `src/agents/knowledge_base_agent.py`, `src/agents/langgraph_supervisor_agent.py`, and `src/agents/langgraph_supervisor_hierarchy_agent.py` — optional Bedrock retrieval/multi-agent examples. Do not adopt them as V1 design yet.
16. `src/client/client.py` then `src/streamlit_app.py` — existing UI calls, browser-generated user ID, streams and thread history.
17. `compose.yaml`, `docker/Dockerfile.service`, `docker/Dockerfile.app`, and `.github/workflows/test.yml` — local container topology and CI commands.
18. Tests alongside production: `tests/service/test_service.py`, `tests/service/test_auth.py`, `tests/service/test_service_real_graphs.py`, `tests/service/test_threads_sqlite.py`, `tests/agents/test_agent_loading.py`, and `tests/smoke/test_persistence.py`.
19. Phase 2 identity, in dependency order: `src/persistence/` (`models.py`, `repositories.py`, `engine.py`, `identity.py`, `passwords.py`, `tokens.py`) and `migrations/` for the `taskpilot` schema, then `src/service/session.py`, `src/service/auth_dependency.py`, and `src/service/authorization.py` for opaque sessions, the server-derived `CurrentPrincipal`, and the single authorization boundary. Evidence lives in `tests/service/test_auth_session.py`, `tests/service/test_current_principal.py`, `tests/service/test_authorization.py`, `tests/service/test_bootstrap.py`, and the disposable-PostgreSQL suites under `tests/persistence/`.

For every file ask: who calls it, what does it receive, what does it return or persist, and what authorization/trust assumption does it make? Distinguish a LangGraph conversation checkpoint from TaskPilot's `Task`/`TaskRun`/`TaskStep` domain, which does not exist yet. Phase 2 identity does exist: caller-supplied `user_id`, `organization_id`, role, `/threads` query values, and AG-UI forwarded identity are never authorization truth, and only a server-resolved `CurrentPrincipal` selects a tenant.
