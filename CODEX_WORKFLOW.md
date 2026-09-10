# Codex Working Method

## 1. Use planning for large changes

For architecture, ask Codex to inspect and plan first.
Do not mix “decide architecture” and “write 2,000 lines” in one opaque prompt.

## 2. Prompt like a GitHub issue

Every prompt should contain:
- goal
- context pointers
- scope
- constraints
- acceptance criteria
- tests
- docs

## 3. Keep durable repository context

Use `AGENTS.md` and versioned docs rather than repeating rules in chat.

## 4. Verification loop

Every implementation task:

Inspect
→ Plan
→ Edit
→ Focused test
→ Fix
→ Broader check
→ Docs
→ Progress log
→ Review diff

## 5. Work queue

Maintain a backlog of small tasks.
Do not ask a low-cost model to decide what the next 20 tasks should be.

## 6. Review discipline

For routine work:
- low-cost model implements
- automated tests validate

For security/state/architecture:
- stronger model reviews the diff and invariants

## 7. When stuck

Ask Codex to:
1. reproduce
2. isolate failing layer
3. explain root-cause hypothesis
4. add/identify failing test
5. apply minimal fix

Avoid “try random changes until it works”.
