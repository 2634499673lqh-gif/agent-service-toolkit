# Prompt — Generate the Next Cheap-Model Task

Use this with a stronger Codex model at the beginning of a risky/new phase.

Read:
- `AGENTS.md`
- `PROJECT_SPEC.md`
- `ROADMAP.md`
- `TASK_BACKLOG.md`
- current `process/PROGRESS_LOG.md`
- relevant architecture docs
- actual current code

Goal:
Select the next unfinished backlog item that is safe to delegate to a lower-cost model.

Do NOT implement it.

Instead create a concrete task file under `process/tasks/TASK-<id>.md` using `prompts/LOW_COST_TASK_TEMPLATE.md`.

The generated task MUST replace generic placeholders with:
- exact real file paths
- exact interfaces/classes/functions
- exact API contract if relevant
- exact tests to add/run
- exact behavior that must not change

Keep the implementation scope narrow. If the item still requires an architectural decision, do not delegate it; create a strong-model design task instead.

At the end, tell me only:
1. which task was generated,
2. why it is safe for a cheaper model,
3. what command I should run/send next.
