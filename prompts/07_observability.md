# Phase 7 Prompt — Observability

## Goal

Make TaskPilot explainable from request to final result.

## Implement

Correlation:
- request_id
- task_id
- task_run_id
- task_step_id
- agent_run_id
- tool_call_id
- approval_id

Capture:
- agent/node name
- model
- skill/tool
- timestamps/latency
- status
- retry
- normalized error class
- token usage when available
- cost estimate through configurable price table/provider adapter when feasible
- verifier outcome
- approval status

## Requirements

- structured logs
- queryable trace for a run
- secret/content redaction policy
- no dependency on one vendor for core business truth
- optional integration with LangSmith/Langfuse/OpenTelemetry backend is fine

## Tests

- trace contains a complete success flow
- trace exposes fail-once retry
- trace exposes approval pause/resume
- redaction test

Add basic UI/API for viewing the timeline only after backend trace works.
