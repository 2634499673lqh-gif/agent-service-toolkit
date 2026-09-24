# Observability & Evaluation

## Observability questions

For any task, system should answer:

- Who started it?
- Which tenant?
- What task/run/step?
- Which agent/node?
- Which model?
- Which skill?
- Which tool?
- What status?
- How long?
- How many attempts?
- Why did it fail?
- Was there approval?
- What did verifier conclude?
- Token/cost if available?

## Correlation IDs

At minimum:
- request_id
- task_id
- task_run_id
- task_step_id
- agent_run_id
- tool_call_id
- approval_id

## Metrics

Platform:
- request latency
- error rate
- active runs

Agent:
- task success rate
- planner parse failure
- verifier pass rate
- retry rate
- replan rate
- human intervention rate

Tools:
- call count
- latency
- error rate
- retry count

Economics:
- input/output tokens
- estimated cost per run
- cost by model/skill/task type

## Evaluation layers

### Unit
Deterministic functions and policies.

### Workflow
Does the graph transition correctly?

### Agent task eval
Given fixed task + fixtures:
- plan valid?
- correct skill?
- correct tool?
- final answer meets criteria?

### Safety
- approval obeyed?
- injection resisted?
- tenant isolation?

### Regression
Run same versioned eval set after important changes.

## Avoid vanity metrics

“LLM said success” is not success.

Prefer:
- deterministic expected values where possible
- evidence-based verification
- human-labeled golden cases for subjective outputs

## Phase 7 persistence contract and implementation (ADR-009; T091/T092)

Phase 7 keeps Task and TaskRun as the business authority. The durable
observability path is ToolCall -> AgentRun -> TaskRun -> Task ->
organization; AgentRun and ToolCall never duplicate tenant authority.
The T034 migration and tenant-scoped repositories now persist these rows.
TaskStep persistence remains deferred, so the step coordinate is the
zero-based pair (replan_count, step_position), with retry_count identifying a
bounded retry.

The minimum correlation path is request_id (when an HTTP source exists) ->
task_id -> task_run_id -> step coordinates -> agent_run_id -> tool_call_id.
Request IDs come from the existing middleware, task and run IDs come from
server-validated rows, and trace IDs are observational rather than
authorization credentials.

Timing is UTC with nullable integer millisecond durations. Errors use bounded
normalized class/code/message fields. Provider usage is either a bounded
known object or an explicit unavailable reason; unavailable values are never
zero. Cost is a deterministic estimate from an explicit price table, not
billing truth.

The trace query applies the principal organization predicate in SQL, returns a
bounded sanitized timeline in stable order, and preserves failure/retry/replan
rows. T092 owns persisted-payload redaction tests; T097 owns timeline-response
redaction tests; T098 is exclusively the independent read-only Phase 7 Final
Audit.
