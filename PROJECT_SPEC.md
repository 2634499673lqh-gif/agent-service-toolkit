# TaskPilot Project Spec

## 1. Problem

LLM Agent 很容易做出“能跑的 Demo”，但真实业务需要：
- 多用户隔离
- 权限
- 状态持久化
- 并发
- 失败恢复
- 人工审批
- 可观察
- 可审计
- 可评估
- 可维护文档

TaskPilot 的目标是把“单次 Agent 调用”包装成可靠的业务任务执行生命周期。

## 2. Target users

V1:
- Individual knowledge worker
- Small internal team
- Developer/researcher evaluating Agent workflows

Not V1:
- Financial execution
- Medical decision automation
- destructive production administration
- unrestricted shell/code execution for untrusted users

## 3. Core entities

- Organization
- User
- Role / Permission
- Task
- TaskRun
- TaskStep
- AgentRun
- Skill
- ToolCall
- ApprovalRequest
- Conversation / Message
- Memory
- KnowledgeDocument / Chunk
- TraceEvent
- EvaluationRun / EvaluationCase
- Feedback

## 4. Task lifecycle

DRAFT
→ QUEUED
→ RUNNING
→ WAITING_APPROVAL
→ RUNNING
→ VERIFYING
→ SUCCEEDED

Alternative paths:
- RUNNING → RETRYING → RUNNING
- RUNNING → REPLANNING → RUNNING
- * → FAILED
- * → CANCELLED

Every transition must be explicit and validated.

## 5. V1 functional requirements

### Identity
- login
- user/org identity
- basic RBAC
- tenant isolation

### Task
- create task
- list/get own permitted tasks
- start run
- inspect steps
- cancel when allowed
- resume from approval/checkpoint

### Agent runtime
- structured planner
- execution loop
- verifier
- error classification
- bounded retry
- bounded replan
- checkpoint

### Skill registry
Minimum:
- research
- document_analysis
- tabular_analysis

### Tools
Minimum:
- knowledge retrieval
- file/document read
- safe tabular analysis
- web/research adapter or mockable search adapter

External side-effect tool:
- V1 may implement a safe internal action / dry-run adapter to prove approval flow.

### Context
- task objective
- acceptance criteria
- selected prior conversation
- selected user memory
- selected org knowledge
- tool results
- execution state
- budget/truncation rules

### Human approval
- risk classification
- create approval
- pause graph/task
- approve/reject
- authorization check
- resume

### Observability
- IDs across request/task/run/step/agent/tool
- structured logs
- timing
- errors
- retries
- model/tool metadata
- token usage/cost when provider exposes it

### Evaluation
- deterministic test cases
- workflow success
- plan validity
- tool-call success
- verifier accuracy
- approval correctness
- latency/cost reporting

## 6. Non-functional requirements

- safe defaults
- typed contracts
- API versioning approach
- DB migrations
- testability
- local Docker startup
- no secrets in repo
- concise user-facing errors
- developer-facing trace detail
- documentation kept in sync

## 7. Out of scope until V1 is stable

- Kubernetes
- Kafka
- microservice split
- A2A federation
- large multi-agent “society”
- autonomous destructive actions
- unrestricted arbitrary code execution
- complex billing
- enterprise SSO
- full marketplace
- GraphRAG unless measured need exists

## 8. Demo acceptance scenarios

1. Research brief:
   User asks a scoped question; planner uses research + KB; verifier checks sources/criteria; final answer and trace visible.

2. Document analysis:
   User uploads two documents; system compares specified fields; unsupported claims are flagged.

3. Tabular analysis:
   User uploads CSV/XLSX; system computes a defined metric; tool output is recorded; verifier recomputes or checks evidence.

4. Approval:
   Agent proposes an L2 action; task enters WAITING_APPROVAL; unauthorized user cannot approve; authorized user approves; action executes exactly once; run resumes.

5. Recovery:
   A tool intentionally fails once; system records retry, succeeds on retry, and exposes failure/recovery in trace.
