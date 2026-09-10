# Phase 10 Prompt — Concurrency & Deployment Hardening

## Goal

Make V1 reproducible for multiple simultaneous users.

## First measure

Before adding Redis/worker, identify which operations are:
- short request/response
- long-running
- resumable
- external side-effecting

## Then decide

If background jobs are necessary, introduce the minimum queue/worker architecture and record an ADR.

## Implement/check

- DB connection pooling
- async correctness
- idempotency
- long-running job isolation
- rate limits/backpressure
- health/readiness
- graceful shutdown
- Docker Compose
- migrations on deploy strategy
- CI
- concurrency smoke test
- safe CORS/config
- secret handling

## Do not

- add Kubernetes
- split into microservices
- add Kafka unless there is measured necessity

Update deployment runbook and troubleshooting.
