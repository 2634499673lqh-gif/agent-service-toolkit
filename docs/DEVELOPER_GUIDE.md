# Developer Guide

## Local development

Codex must update exact commands after Phase 0 based on the current repository.

Expected pattern:

1. copy `.env.example` to `.env`
2. add required LLM key(s)
3. start PostgreSQL and services with Docker Compose
4. run migrations
5. run FastAPI
6. run UI
7. run tests

Never place real secrets in documentation.

## Before coding

Read:
- `AGENTS.md`
- current task prompt
- relevant design doc
- relevant tests

## Common commands

Codex should fill these with exact verified commands:

```bash
# install/sync
<TODO verified command>

# test
<TODO verified command>

# lint
<TODO verified command>

# typecheck
<TODO verified command>

# run API
<TODO verified command>

# run UI
<TODO verified command>

# migrations
<TODO verified command>
```

## Change workflow

1. create/read one task spec
2. inspect code
3. plan
4. implement
5. run focused tests
6. run broader checks
7. update docs
8. update progress log
9. review diff
10. commit when appropriate

## Debugging order

1. reproduce
2. capture exact error
3. identify layer: API/domain/DB/agent/tool/external
4. inspect trace IDs
5. write/adjust failing test
6. fix smallest root cause
7. rerun test
8. document non-obvious decision
