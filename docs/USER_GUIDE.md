# User Guide

> Codex must keep this synchronized with implemented behavior.

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
