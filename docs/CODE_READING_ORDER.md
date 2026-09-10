# Code Reading Order

> Codex must update exact paths after Phase 0 because the upstream repository may evolve.

## 0. Read documents first

1. `README.md`
2. `AGENTS.md`
3. `PROJECT_SPEC.md`
4. `ROADMAP.md`

Understand product boundaries before implementation.

## 1. Application entry and configuration

Read:
- service startup / application factory
- settings/config
- dependency setup

Questions:
- FastAPI app is created where?
- settings come from where?
- DB/model clients are initialized where?

## 2. API schemas

Read:
- request/response Pydantic models
- shared API protocol models

Questions:
- internal domain model vs API schema?
- streaming vs non-streaming response?

## 3. Database and persistence

Read:
- DB session/engine
- ORM models
- migrations
- repositories

Questions:
- transaction boundary?
- tenant filter?
- checkpoint storage?

## 4. Authentication and authorization

Read:
- current user dependency
- auth service
- RBAC policy
- organization/tenant checks

Trace one request from token → current user → authorized resource.

## 5. Task domain

Read:
- Task
- TaskRun
- TaskStep
- state transition service
- Task APIs

Trace:
POST task → DB → start run → update status.

## 6. Agent graph

Read in this order:
1. AgentState
2. graph builder
3. planner node
4. executor node
5. verifier node
6. recovery routing
7. checkpoint/resume

Do not read prompts first. Understand state flow first.

## 7. Skills and Tools

Read:
- Skill manifest
- registry
- tool interface
- 1 simple tool
- 1 external/knowledge tool

Trace:
plan step → skill selection → tool call → result → state.

## 8. Context

Read:
- context builder
- memory retrieval
- knowledge retrieval
- context budget/truncation
- tool result normalization

Question:
What exactly goes into each model call?

## 9. Human approval

Trace:
risky tool proposal
→ policy
→ approval row
→ interrupt
→ API decision
→ resume
→ idempotent tool execution

## 10. Observability

Trace one task through:
request_id → task_run → agent_run → tool_call → trace event.

## 11. Evaluation

Read:
- fixtures
- eval runner
- metrics
- regression output

## 12. UI

Only after backend flow is understood:
- API client
- task detail
- trace timeline
- approval UI

## 13. Tests

Read tests after corresponding production module; use them as executable specifications.

### Personal reading method

For every file, answer four questions:
1. Who calls this?
2. What does it receive?
3. What does it return/change?
4. What breaks if it is wrong?
