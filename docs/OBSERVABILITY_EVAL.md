# Observability & Evaluation

## Observability questions

For any task, system should answer:

- Who started it?
- Which tenant?
- What task/run/step?
- Which agent/node?
- Which model?
- Which skill?
- Which tool?
- What status?
- How long?
- How many attempts?
- Why did it fail?
- Was there approval?
- What did verifier conclude?
- Token/cost if available?

## Correlation IDs

At minimum:
- request_id
- task_id
- task_run_id
- task_step_id
- agent_run_id
- tool_call_id
- approval_id

## Metrics

Platform:
- request latency
- error rate
- active runs

Agent:
- task success rate
- planner parse failure
- verifier pass rate
- retry rate
- replan rate
- human intervention rate

Tools:
- call count
- latency
- error rate
- retry count

Economics:
- input/output tokens
- estimated cost per run
- cost by model/skill/task type

## Evaluation layers

### Unit
Deterministic functions and policies.

### Workflow
Does the graph transition correctly?

### Agent task eval
Given fixed task + fixtures:
- plan valid?
- correct skill?
- correct tool?
- final answer meets criteria?

### Safety
- approval obeyed?
- injection resisted?
- tenant isolation?

### Regression
Run same versioned eval set after important changes.

## Avoid vanity metrics

“LLM said success” is not success.

Prefer:
- deterministic expected values where possible
- evidence-based verification
- human-labeled golden cases for subjective outputs
