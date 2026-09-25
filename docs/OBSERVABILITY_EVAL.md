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

T093 wires this path through the bounded runtime graph. The graph emits one
server-selected `executor` AgentRun observation for each capability boundary,
using the zero-based `(replan_count, step_position, retry_count)` coordinates;
the capability dispatch emits its ToolCall child with `call_index = 0`. The
service persists both rows only after the graph result is known, through the
existing tenant-scoped repositories. HTTP request IDs come from the middleware
context; background execution stores `NULL`. No `task_step_id` is synthesized,
and neither the checkpoint nor model output can supply a trace authority.

### T094/T095 normalization boundary

T094 validates aware timestamps, canonicalizes them to UTC, and derives a
nullable integer `duration_ms` from the two endpoints only. Durations are
bounded to 24 hours; an open or missing endpoint remains `NULL`. AgentRun and
ToolCall statuses are observational values, so TaskRun lifecycle remains the
authoritative `pending/running/succeeded/failed/cancelled` state machine. Error
class, code, and sanitized message are bounded; an unclassifiable error uses
`UNKNOWN`, while no error uses `NULL`. A retry is a new AgentRun row with the
same replan and incremented retry coordinate. A replan increments the replan
coordinate and starts at step position zero; prior evidence is retained.

T095 normalizes provider usage to either a known object containing bounded
integer token counts or `{"status":"unavailable","reason":...}` with one of
`not_returned`, `unsupported`, or `malformed`. A missing total is derived only
from valid input and output counts. Missing, malformed, negative, or oversized
values never become numeric zero. Provider metadata is restricted to
allowlisted provider/model/response identifiers and 2,048 compact UTF-8 bytes.
The normalized usage and metadata flow through the existing runtime collector
into AgentRun/ToolCall rows; `NULL` remains reserved for an observation where
no provider usage applied.

### T096 cost estimate boundary

T096 uses an explicit in-process `PricingTable` keyed by `(provider, model,
version)`. Each `PricingEntry` validates bounded non-negative `Decimal` prices
per 1,000 tokens and a bounded currency. The pure `estimate_cost` function
accepts known normalized usage, computes input plus output cost in Decimal
arithmetic, rounds with `ROUND_HALF_UP` to six fractional places, and returns a
fixed-scale decimal string with its currency. A known provider/model with a
missing version returns `missing_price`; an unsupported provider/model returns
`unsupported_model`. Unavailable or partial usage returns `usage_unavailable`,
and a result above `10^18` currency units returns `overflow`. Unknown results
contain a reason and never fabricate numeric zero. These values are
informational observability estimates, not billing authority; there is no
remote pricing lookup, refresh loop, ledger, or billing behavior.

The trace query applies the principal organization predicate in SQL, returns a
bounded sanitized timeline in stable order, and preserves failure/retry/replan
rows. The public route is `GET /api/v1/tasks/{task_id}/runs/{run_id}/trace`
with a default limit of 100 and a maximum of 500. Its projection contains
correlation IDs, server-selected names, observational status, UTC timing,
normalized errors, usage, an informational estimate, and bounded redacted
provider metadata; arguments, results, checkpoints, prompts, context,
credentials, authorization objects, and raw provider responses are excluded.
T092 owns persisted-payload redaction tests; T097 owns timeline-response,
tenant, ordering, limit, and reconstruction tests; T098 is exclusively the
independent read-only Phase 7 Final Audit.
