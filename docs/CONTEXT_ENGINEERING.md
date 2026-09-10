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

## Suggested ContextBuilder output

```text
ContextEnvelope
- policy_context
- task_context
- current_step_context
- memory_items[]
- knowledge_items[]
- tool_observations[]
- execution_summary
- provenance[]
- budget_report
```

## Context budget

Codex should implement a simple documented policy before any sophisticated optimization:

Priority:
1. safety/policy
2. objective + acceptance criteria
3. current step
4. directly relevant tool results
5. top-k knowledge
6. relevant memory
7. summarized old messages

If over budget, trim from the bottom, never from safety/goal.

## Evaluation questions

- Did retrieval leak another tenant?
- Did a malicious document override tool policy?
- Can we explain why each retrieved item was included?
- Does a smaller context perform equally well?
