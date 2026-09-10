# Troubleshooting

Codex should append real issues encountered during development.

## Service does not start

Check:
- `.env`
- Docker
- port conflicts
- DB health
- dependency sync
- migration state

## LLM call fails

Check:
- API key exists
- model/provider configured
- network allowed
- rate limit
- provider status
- request payload size

Do not print API keys while debugging.

## DB migration fails

Do not delete the database immediately.
Check:
- current revision
- migration history
- whether local data can be recreated
- incompatible schema changes

## Agent loops

Check:
- retry budget
- replan budget
- terminal condition
- invalid verifier routing
- tool error classification

All loops must be bounded.

## Approval stuck

Check:
- approval status
- checkpoint exists
- run/task status alignment
- authorization
- resume event consumed exactly once
