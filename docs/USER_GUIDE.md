# User Guide

> Codex must keep this synchronized with implemented behavior.

## Status (2026-09-23)

Everything below describes the target V1 workflow beyond the currently implemented surface. There is no login endpoint, step view, or approval UI; T036/T037 provide protected Task routes, T038 provides tenant-scoped TaskRun routes, and T081/T082 provide Approval persistence with protected nested read/decision routes under `/api/v1/tasks`. T083 implements the internal runtime approval boundary; T084 bounded action handling is implemented; the public runtime workflow remains outside the current scope. Phase 2 delivered opaque revocable sessions, the server-derived `CurrentPrincipal`, and the authorization boundary. See `docs/ARCHITECTURE.md` for the implemented inventory and `docs/DEVELOPER_GUIDE.md` for the available commands.

## What TaskPilot does

TaskPilot accepts a concrete work task, plans steps, uses approved tools/skills, verifies results, and shows the execution trace.

## Typical workflow

1. Log in.
2. Create a task with:
   - title
   - objective
   - acceptance criteria
   - optional files/context
3. Start a run.
4. Watch task steps.
5. If an approval is required, inspect the proposed action.
6. Approve or reject.
7. Review final result and trace.
8. Provide feedback.

## How to write a good task

Bad:
> 帮我看看这些东西。

Better:
> 比较上传的两份文档，列出预算、时间、负责人三项差异。只使用文档中能直接支持的信息；缺失项标记“未找到”。

## Approval safety

Never approve an action you do not understand.
The UI should show:

- action
- target
- sanitized arguments
- risk level
- originating task

## Known V1 limitations

Keep this section factual and update after every major release.

## Phase 9 planning status (2026-09-26)

The Product UI is approved as a Streamlit-first, authenticated TaskPilot view. Planning found no public login/session/logout route and no HTTP run-discovery route, so T118 and T119 are explicit prerequisites. The UI will use server-owned identity and lifecycle state, show only selected-run approvals and sanitized trace evidence, and clearly label that starting a run creates persisted pending/queued state without running the internal runtime. No TaskStep, public execution endpoint, React rewrite, live updates, uploads or admin console is included. The next executable implementation batch is T118–T119.
