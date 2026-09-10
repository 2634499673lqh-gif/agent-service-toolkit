# Codex Model / Budget Strategy

## 1. Core rule

Spend intelligence on decisions, not repetition.

Use a stronger model to decide:
- boundaries
- invariants
- state machines
- security
- recovery
- concurrency
- migrations
- acceptance criteria

Then use a cheaper model to implement tasks whose decisions are already written down.

## 2. Three tiers

### Tier A — Architecture / high reasoning

Use for:
- Phase 0
- schema redesign
- Agent graph
- failure recovery
- checkpoint/resume
- tenant security
- side-effect safety
- concurrency
- hard bugs
- final review

Expected output should often be a plan/spec first, not immediate code.

### Tier B — Standard implementation

Use for:
- service methods
- APIs
- migrations after schema is decided
- tool adapters
- ordinary auth implementation
- trace endpoints
- UI flows
- integration tests

### Tier C — Low-cost execution

Use for:
- one CRUD endpoint
- one Pydantic model
- one migration from an already-approved schema
- unit tests for a fixed contract
- docs sync
- fixtures
- type hints
- straightforward validation
- simple UI component

## 3. Low-cost task size

Ideal low-cost task:
- touches 1 domain
- usually <= 3–6 files
- no framework replacement
- no new architectural pattern
- exact acceptance tests are known
- can be reverted independently

Bad prompt:
> 完成整个用户权限系统。

Good prompt:
> Implement `GET /api/v1/tasks/{task_id}` authorization using the existing TaskRepository and `CurrentUser` dependency. Return 404 for resources outside the caller's tenant. Add tests for same-tenant access and cross-tenant denial. Do not change token issuance or DB schema.

## 4. Context reuse

Put persistent rules in:
- `AGENTS.md`
- `PROJECT_SPEC.md`
- `docs/*`

Put current work state in:
- `process/PROGRESS_LOG.md`
- the current `TASK-xxx.md`

Do not repaste the whole project description in every prompt.

## 5. Two-pass pattern

For risky work:

Pass 1, strong model:
- inspect
- decide
- create task spec
- define acceptance tests

Pass 2, cheaper model:
- implement exactly that spec
- run tests
- update docs

Optional Pass 3, strong reviewer:
- review diff only
- focus on invariants/security
- do not rewrite unless necessary

## 6. Cheapest tasks to automate first

Documentation drift checks, test fixture additions, ordinary unit tests, type cleanup, endpoint examples, changelog entries, simple UI rendering, generated API examples.

## 7. Never delegate blindly to the cheapest model

Keep strong review for:
- auth bypass risks
- migrations deleting data
- transactions
- idempotency
- approval bypass
- arbitrary code execution
- prompt/tool injection boundaries
- cross-tenant retrieval
- external side effects
