# Database Design Guide

Codex should convert this conceptual design into the repository's actual ORM and migrations.

## Identity

organizations
- id
- name
- created_at

users
- id
- organization_id
- email
- password_hash / external_identity
- is_active
- created_at

roles
permissions
user_roles / role_permissions

For a small V1, role enum + explicit policy may be simpler than fully normalized RBAC. Decide in Phase 2 and record the decision.

## Task

tasks
- id
- organization_id
- created_by
- title
- objective
- acceptance_criteria
- status
- created_at
- updated_at

task_runs
- id
- task_id
- run_number
- status
- started_at
- finished_at
- current_step_id
- retry_count
- failure_code

task_steps
- id
- task_run_id
- ordinal
- step_key
- title
- description
- status
- input_json
- output_json
- attempt_count
- started_at
- finished_at

## Agent / Tool trace

agent_runs
- id
- task_run_id
- task_step_id nullable
- agent_name
- model_name
- status
- started_at
- finished_at
- input_summary
- output_summary
- token_input
- token_output
- cost_estimate

tool_calls
- id
- agent_run_id
- task_step_id
- tool_name
- skill_name nullable
- risk_level
- status
- args_sanitized_json
- result_summary_json
- error_class
- attempt
- started_at
- finished_at
- idempotency_key nullable

## Approval

approval_requests
- id
- task_id
- task_run_id
- task_step_id
- tool_call_id
- risk_level
- proposed_action
- proposed_args_sanitized_json
- status
- requested_by_user_id
- decided_by_user_id nullable
- decision_reason nullable
- created_at
- decided_at

## Knowledge / memory

documents
document_chunks
user_memories
conversation_threads
messages

Do not over-normalize V1 memory until retrieval behavior is understood.

## Evaluation

evaluation_cases
evaluation_runs
evaluation_results

## Important constraints

- indices on organization_id, user_id, task_id, task_run_id
- uniqueness/idempotency where applicable
- FK behavior explicit
- no silent cascade delete for audit-critical records without review
- state values constrained
- all tenant-scoped queries tested
