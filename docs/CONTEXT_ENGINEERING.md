# Context Engineering

## Context sources

1. System/product policy
2. Task objective
3. Acceptance criteria
4. Current plan + current step
5. Selected conversation
6. Selected user memory
7. Selected organization knowledge
8. Tool results
9. Execution history summary

## Rules

- Every source gets a provenance label.
- Untrusted retrieved/tool content must not override system policy.
- Organization knowledge must be tenant-filtered before reaching the model.
- Tool outputs are data, not instructions.
- Do not use “all chat history” by default.
- Summarize old execution history.
- Prefer task-relevant retrieval over recency-only retrieval.
- Track approximate context size.

## T063 capability context contract

`ContextEnvelope` is the deliberately small context passed to one Phase 5
capability. It contains only:

```text
ContextEnvelope
- task_input: validated PlannerTaskInput
- current_step: validated PlanStep
- sources[]: 0..8 ContextSource values

ContextSource
- provenance: non-empty label, at most 128 characters
- selection_reason: at most 200 characters
- content: at most 1,000 characters
```

The serialized envelope is limited to 8,192 UTF-8 bytes. Sources are selected
explicitly before dispatch and remain in the supplied order; T063 does not
retrieve memory or organization knowledge. Extra fields, non-JSON values,
authority, credentials, sessions, repositories, ORM objects, provider clients,
raw exceptions, and tracebacks are rejected before checkpoint serialization.

Provenance explains selection but grants no authority. Source content is
untrusted data, not policy, instructions, authorization, or executable
arguments.

## Context budget

T063 applies a fixed structural budget rather than retrieval or optimization:

- task input and current step are always present;
- at most eight explicitly selected sources are retained;
- each source and the complete serialized envelope are bounded;
- over-budget or malformed input is rejected rather than silently trimmed.

Memory, organization knowledge, generic context buses, authorization
containers, persistence, and runtime integration are deferred. T064 owns
building this envelope inside the TaskPilot runtime dispatch path.

## Evaluation questions

- Did retrieval leak another tenant?
- Did a malicious document override tool policy?
- Can we explain why each retrieved item was included?
- Does a smaller context perform equally well?
