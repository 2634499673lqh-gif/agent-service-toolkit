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

## Phase 8 Evaluation planning (ADR-010; T100–T107)

Evaluation is measurement and regression evidence, never business or runtime authority. Task/TaskRun and approval state remain authoritative in the business DB; checkpoints remain recovery state; telemetry remains observation.

The V1 suite is `taskpilot.phase8.v1` / version `1` and contains exactly five bounded, provider-free cases: `deterministic.fixture_baseline`, `recovery.retry_then_pass`, `recovery.replan_then_pass`, `approval.l2_requires_approval`, and `approval.l3_blocked`. The baseline reuses the existing server-wired `DeterministicFixtureCapability` (`deterministic_fixture`) and exact output `deterministic-read-only-fixture:v1`; the earlier tabular/sum wording was pre-planning intent, not an existing capability. Each case has stable ID/version, exact bounded JSON input, expected evidence, and a 32 KiB canonical input bound. Results are in-memory and canonically ordered. Recovery evidence is `retry_observed`/`recovery_pass` or `replan_observed`/`recovery_pass`; approval evidence is `approval_required`/`approval_satisfied`/`execution_succeeded` for L2 and `blocked_before_execution` for L3.

T103 freezes four metrics—`pass_rate`, `recovery_success`, `approval_compliance`, and `evidence_completeness`—with the shared `{status, numerator, denominator, rate}` shape, four fixed decimal places, `Decimal` `ROUND_HALF_UP`, and explicit `not_applicable` for denominator zero. Each run owns its metrics. Comparison is separate: `not_requested`, `comparable`, or `incomparable` with bounded machine-readable reasons; incomparable reports emit no cross-run metric deltas and are not metric failures.

T104 emits the always-present `taskpilot.eval.report/v1` object with required `schema`, `suite_id`, `suite_version`, `runner_status`, `cases`, `metrics`, and `comparison`. Case statuses are `pass`, `fail`, `error`; runner statuses are `completed`, `partial`, `error`. Case/evidence bounds, failure-code invariants, five-case maximum, sorted compact UTF-8 serialization, one trailing newline, no timestamps/UUIDs, and a 32 KiB artifact limit are frozen in ADR-010. T105 derives human output only from this report. Reports exclude secrets, credentials, authorization objects, raw provider/context payloads, checkpoints, ORM/session objects, and cross-tenant data.

T102 reruns the suite through existing runtime primitives with mocked providers and no network, paid model, SaaS, or database. T106 runs the cheap baseline/retry/L2 subset in default CI and fails on runner errors, failed cases, or incomparable results. No implementation occurs until T100 passes Planning Strong Review.

Deferred: live LLM-as-judge, remote Evaluation SaaS, generic benchmark or experiment tracking, Evaluation database, dashboard/UI, workers, analytics platform, billing, subjective production gating, and generic provider benchmarking. See `process/ADR-010.md` and `process/tasks/T100.md`–`T107.md`.

T100 is the Phase 8 Evaluation architecture/planning gate. Planning Strong Review is APPROVED; ADR-010 is Accepted/frozen, T100 is COMPLETE / APPROVED, and implementation starts at T101.

Implementation batches:

- Planning gate: **T100**.
- Implementation Batch 1: **T101–T103** (deterministic fixtures, workflow runner, deterministic metrics).
- Implementation Batch 2: **T104–T106** (machine report, human report, CI smoke).
- Phase Final Audit: **T107** (independent, fresh-eyes, read-only).

DAG: `T100 → T101 → T102 → T103 → T104 → T105`; `T101 + T102 + T103 + T104 → T106`; `T100–T106 → T107`.


## Phase 8 Evaluation reports (T104–T106)

The local Evaluation boundary emits the canonical `taskpilot.eval.report/v1`
UTF-8 JSON artifact through `evaluation.build_machine_report` and
`evaluation.serialize_report`. Object keys and case/evidence arrays are
ordered deterministically, metrics use four Decimal-rounded places, and the
artifact is capped at 32 KiB. `evaluation.render_human_report` is a display
projection of that machine report and does not recalculate semantics.

The default CI job runs the provider-free smoke subset
`deterministic.fixture_baseline`, `recovery.retry_then_pass`, and
`approval.l2_requires_approval` with `uv run python scripts/evaluation_smoke.py`; it
requires no provider, network, or Evaluation database and uploads the bounded
JSON artifact. The command exits non-zero for runner `error`/`partial`, failed
cases, incomparable comparison, or an oversized report.
