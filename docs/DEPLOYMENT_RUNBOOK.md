# Deployment Runbook

V1 target is a reproducible Docker Compose deployment, not Kubernetes.

## Required services

Exact services depend on final implementation, likely:
- API
- PostgreSQL
- UI
- optional Redis/worker
- optional observability backend

## Deployment checklist

- env vars present
- secrets not committed
- DB reachable
- migrations applied
- health endpoint green
- readiness checks pass
- LLM provider configured
- storage configured
- CORS restricted appropriately
- test user/demo data policy understood
- log redaction enabled
- backups considered for persistent DB

## Rollback

Document:
- code rollback
- migration rollback policy
- what migrations are irreversible
- how to disable external-action tools quickly

## Incident basics

When a run misbehaves:
1. disable risky tool if needed
2. capture task/run/trace IDs
3. inspect approval/audit
4. determine whether side effect occurred
5. never blindly retry ambiguous external action
