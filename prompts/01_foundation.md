# Phase 1 Prompt — Foundation & Documentation

Execute Phase 1 only, broken into small commits/tasks if needed.

## Goal

Make the fork reproducible and establish TaskPilot's engineering/documentation discipline without changing core Agent behavior yet.

## Work

- Preserve upstream license and attribution.
- Update project branding/README carefully.
- Verify `.env.example` contains placeholders only.
- Centralize/validate settings using existing conventions.
- Add or normalize request correlation ID and structured logging.
- Establish DB migration baseline if missing.
- Add convenient verified developer commands/scripts.
- Ensure Docker/local startup docs match reality.
- Keep existing agent demos running unless intentionally deprecated and documented.

## Acceptance

- new developer can start from docs
- no secrets
- request_id appears in logs
- tests pass or baseline failures are explicitly preserved
- migrations are reproducible
- `PROGRESS_LOG` updated
- learner notes included

Do not add business auth/task domain yet unless a tiny prerequisite is unavoidable.
