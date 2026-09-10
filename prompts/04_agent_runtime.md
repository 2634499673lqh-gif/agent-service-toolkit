# Phase 4 Prompt — Agent Runtime [STRONG MODEL]

## Goal

Implement the smallest reliable Planner → Executor → Verifier → Recovery graph integrated with TaskRun/TaskStep.

## Work order

1. Define typed `AgentState`.
2. Define structured planner output schema.
3. Implement Planner.
4. Implement one deterministic/simple Executor path.
5. Implement structured Verifier.
6. Add failure classification.
7. Add bounded retry.
8. Add bounded replan.
9. Persist/checkpoint state.
10. Map graph state to TaskRun/TaskStep statuses.
11. Add deterministic tests with mocked model/tool outputs.

## Must demonstrate

Case A:
normal success.

Case B:
tool fails once with retryable error, succeeds next attempt.

Case C:
verifier fails due to missing evidence and triggers bounded recovery.

Case D:
terminal failure stops cleanly.

## Safety

Do not include real external side effects yet.
No unbounded recursion/loop.

## Documentation

Update `AGENT_DESIGN`, architecture, reading order, progress log.

Learner explanation must trace one task node by node.
