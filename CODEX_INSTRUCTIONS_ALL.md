# CODEX INSTRUCTIONS — COMBINED COPY
This file concatenates the key instructions for easy reading. Prefer keeping the individual files in the repository so Codex can load targeted context.


---

## FILE: MASTER_PROMPT.md


# Master Prompt — 给 Codex 的总控指令

你正在帮助我开发 **TaskPilot**。请先把自己当成“高级软件工程师 + Agent Systems Engineer + 代码审查者 + 面向零基础学习者的导师”。

我目前几乎没有 Agent 工程经验，所以你必须同时做到两件事：

1. 把系统逐步做对；
2. 让我能够跟着项目理解“为什么这样做”。

## 一、项目定位

TaskPilot 是“面向真实知识工作任务的可观测、可恢复、有人机协同的 Agent Task Platform”。

它不是普通聊天机器人。

核心流程：

用户创建 Task
→ 保存 Task/TaskRun
→ 构建上下文
→ Planner 产生结构化计划
→ Executor 调用 Skill / Tool
→ Verifier 验证
→ 若失败则分类并 Retry / Replan / Pause
→ 高风险操作进入 Human Approval
→ approve/reject 后恢复
→ 输出结果
→ 保存 trace、metrics、audit、evaluation

V1 支持四类业务：
- Research
- Document Analysis
- Tabular Data Analysis
- Approved Action / dry-run

## 二、技术基线

优先复用当前仓库已有能力。预期基线大致为：

- Python
- FastAPI
- LangGraph
- PostgreSQL
- SQLAlchemy/Alembic（若当前仓库已有其他 ORM/迁移方案，先评估，不要直接替换）
- pgvector 或现有向量库
- Redis：只有真正需要队列/缓存/锁时引入
- structured logging
- OpenTelemetry-compatible tracing，必要时可接 LangSmith/Langfuse
- Docker Compose
- pytest

前端：
- 先保留/利用现有 Streamlit 作为开发和验证 UI
- 后端稳定后再决定是否添加 Next.js/React 产品 UI
- 不要为了“看起来高级”提前重写前端

## 三、必须遵守的工作方式

### 1. 不要一次性开发完整项目

你必须按照 `ROADMAP.md` 分阶段推进。

一次只执行一个明确的小任务。

### 2. 每个任务开始前

必须：
- 阅读 `AGENTS.md`
- 阅读对应任务文件
- 检查相关代码
- 说明你准备改什么、为什么
- 指出可能影响的数据模型/API/安全/迁移

### 3. 每个任务结束前

必须：
- 跑实际测试
- 报告真实结果
- 更新必要文档
- 更新 `process/PROGRESS_LOG.md`
- 给我“Learner notes”
- 给出下一个推荐任务

### 4. 文档是产品的一部分

整个开发过程中持续维护：

- README / Quickstart
- User Guide
- Developer Guide
- Architecture
- Agent Design
- Context Engineering
- Database Design
- API Design
- Security & HITL
- Observability & Evaluation
- Deployment Runbook
- Troubleshooting
- Code Reading Order
- Progress Log
- Decision Log
- Changelog

文档必须与当前代码一致；不要写“未来可能做的功能”时使用已完成语气。

### 5. 对我进行教学

不要只回复“已实现”。

每次告诉我：
- 这一块在整个系统中的位置
- 为什么需要它
- 关键文件在哪里
- 数据/请求是怎么流动的
- 我现在应该读哪 3–5 个文件
- 给我一个 10–20 分钟可以自己做的小练习

## 四、架构原则

### Platform 与 Agent 分层

不要把所有逻辑塞进 Agent。

建议逻辑分层：

API / Auth / Tenant
→ Task Service
→ Agent Runtime / Graph
→ Skill Registry
→ Tool Layer
→ External Systems

横切能力：
- Context
- Persistence
- Approval
- Observability
- Evaluation
- Audit

### Agent 角色

V1 以最少的角色开始：

- Planner
- Executor
- Verifier
- Recovery（可作为 graph node/service，不一定必须独立成 LLM Agent）

只有当权限、上下文隔离、独立验证或模型专业化确实需要时才新增 Agent。

### Skill

Skill 是可复用能力包，不是独立平台。

V1 可以定义 Skill Registry，并至少实现：
- research
- document_analysis
- tabular_analysis

每个 Skill 应描述：
- name/version
- purpose
- accepted input
- required tools
- instructions/reference
- risk level
- output contract
- evaluation cases

### Tool

Tool 必须：
- typed input/output
- 可追踪
- 可分类失败
- 具有风险级别
- 尽量幂等
- 不直接绕过审批策略

### Context

必须明确区分：
- task context
- step context
- conversation context
- user memory
- org knowledge
- tool results
- execution history

上下文构造必须可解释，不能简单把所有消息全部塞进模型。

### Human-in-the-loop

服务端强制审批。

L2 外部副作用操作必须审批。
L3 高风险行为 V1 默认阻断或只做模拟。

### Observability

至少能回答：
- 哪个用户的哪个 Task？
- 哪个 Run / Step？
- 调了哪个 Agent？
- 用了什么 model？
- 加载了什么 Skill？
- 调了哪个 Tool？
- 输入/输出是否成功？
- 为什么失败？
- 重试了几次？
- 用了多久？
- token/cost 多少（可获取时）？
- Verifier 为什么通过/失败？
- 是否有人审批？

## 五、工程与安全要求

- 多租户数据必须通过 server-side authorization 隔离
- 密码安全哈希
- JWT/session 不记录明文敏感信息
- secrets 只通过 env/secret manager
- 日志脱敏
- 外部 side effect 加 idempotency
- DB 写操作考虑事务
- 状态转换做校验
- 失败恢复避免重复执行 side effect
- 单测默认 mock LLM/network
- Eval 与普通 unit tests 分开

## 六、省额度/低模型协作规则

我要尽量节省 Codex 额度。

因此请把工作拆成“小而可验证”的任务。

### 只有这些任务建议使用强模型

- Phase 0 差距分析与总体架构
- 核心 DB/domain 大调整
- Agent State / Graph
- checkpoint/resume
- 并发/事务/幂等
- RBAC/tenant security design
- Human approval / side-effect safety
- 跨模块复杂 bug
- Final architecture/security audit

### 这些任务应主动设计成可交给低成本模型

- 一个模型/表的普通 CRUD
- 一个 API endpoint
- 一个 Pydantic schema
- 单文件/单模块工具实现
- unit tests
- fixture
- docs sync
- type hints
- 小型重构
- UI component
- error message / validation
- README update

为低成本模型创建任务时，必须给它：
- Goal
- Exact scope
- Relevant files
- Constraints
- Acceptance criteria
- Tests to run
- Docs to update
- Explicit “Do not change” list

如果一个任务无法清晰写出以上内容，说明它还不适合交给低成本模型。

## 七、当前动作

现在不要开始大规模编码。

先执行 `prompts/00_repo_assessment.md`。

完成 Phase 0 评估之后停下来，给我：
1. 当前仓库结构图；
2. 可直接复用的能力；
3. 与 TaskPilot 目标的 gap；
4. 推荐的最小架构变更；
5. 风险；
6. 更新后的 roadmap；
7. 我应该先读的代码文件顺序。

不要进入下一 Phase，直到 Phase 0 的验收项已经完成。


---

## FILE: PROJECT_SPEC.md


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


---

## FILE: ROADMAP.md


# TaskPilot Roadmap

> Principle: vertical slices before feature breadth. Each phase must leave the repository runnable.

## Phase 0 — Repository Assessment [STRONG MODEL]

Goal: understand the upstream/base repository and produce an explicit delta plan.

Tasks:
- T000 baseline inventory
- T001 run current tests/build
- T002 map request/agent/data flow
- T003 identify reusable modules
- T004 write gap analysis and architecture decision

Exit:
- no major speculative refactor
- baseline commands documented
- code reading order exists
- architecture delta reviewed

## Phase 1 — Foundation & Documentation [ECONOMY/STANDARD]

Goal: stable config, documentation discipline, migrations, logging IDs.

Tasks:
- T010 TaskPilot naming/README while preserving upstream attribution/license
- T011 settings/env validation
- T012 request_id structured logging
- T013 DB migration baseline
- T014 developer commands/scripts

Exit:
- local startup reproducible
- tests reproduce upstream baseline
- progress/decision logs active

## Phase 2 — Identity, Organization, RBAC [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T020 User/Organization/Role domain design
- T021 migrations/models
- T022 auth login/token/session layer
- T023 authorization dependencies
- T024 tenant isolation tests
- T025 auth docs

Exit:
- two users in different orgs cannot read each other's resources
- negative auth tests exist

## Phase 3 — Task Domain [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T030 Task/TaskRun/TaskStep states
- T031 persistence/repository
- T032 create/list/get APIs
- T033 transition service
- T034 idempotency/concurrency controls
- T035 task tests/docs

Exit:
- task lifecycle works without LLM
- invalid transitions rejected

## Phase 4 — Agent Runtime [STRONG MODEL]

Tasks:
- T040 AgentState contract
- T041 Planner structured output
- T042 Executor node
- T043 Verifier contract
- T044 failure classifier/retry/replan
- T045 checkpoint/resume
- T046 deterministic runtime tests

Exit:
- one complete task can execute through graph
- a forced retry is visible and recoverable

## Phase 5 — Skills, Tools, Context [STRONG DESIGN + STANDARD/ECONOMY IMPLEMENTATION]

Tasks:
- T050 Skill manifest + registry
- T051 research skill
- T052 document-analysis skill
- T053 tabular-analysis skill
- T054 typed tool registry
- T055 context builder
- T056 user memory
- T057 org knowledge retrieval
- T058 context budget/tests

Exit:
- planner selects a skill
- tool calls are typed/traced
- context sources can be explained

## Phase 6 — Human-in-the-loop & Safety [STRONG MODEL]

Tasks:
- T060 risk policy
- T061 approval persistence/API
- T062 graph interrupt/pause
- T063 approve/reject/resume
- T064 exactly-once protection for approved side effects
- T065 audit/security tests

Exit:
- L2 action cannot execute before approval
- duplicate approval/action cannot duplicate effect

## Phase 7 — Observability [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T070 trace/event schema
- T071 structured correlation
- T072 agent/tool/model metrics
- T073 token/cost adapter
- T074 trace query API
- T075 basic trace UI

Exit:
- one task can be reconstructed from trace
- failure point and retry are visible

## Phase 8 — Evaluation [STRONG DESIGN + ECONOMY IMPLEMENTATION]

Tasks:
- T080 eval schema/dataset
- T081 deterministic fixtures
- T082 evaluator runner
- T083 workflow/accuracy/recovery metrics
- T084 regression report
- T085 CI smoke gate

Exit:
- same eval suite can be rerun
- results are versioned/comparable

## Phase 9 — Product UI [STANDARD/ECONOMY]

First stabilize existing Streamlit UI.
Optional second step: add Next.js/React when APIs are stable.

Views:
- login
- task create
- task list/detail
- step/trace timeline
- approval queue
- knowledge/file upload
- eval/observability summary

Exit:
- demo can be used without Postman

## Phase 10 — Concurrency & Deployment Hardening [STRONG DESIGN + STANDARD IMPLEMENTATION]

Tasks:
- T100 long-running work queue decision
- T101 Redis/worker only if needed
- T102 rate limiting/backpressure
- T103 health/readiness
- T104 Docker Compose production-like local stack
- T105 CI
- T106 security config/CORS/secrets
- T107 load/concurrency smoke test

Exit:
- multiple simultaneous demo users do not corrupt state
- startup/health/recovery documented

## Phase 11 — Documentation, Demo, Final Audit [STRONG REVIEW + ECONOMY DOC WORK]

Tasks:
- T110 user guide
- T111 developer guide
- T112 architecture diagrams
- T113 code reading order final
- T114 troubleshooting/runbook
- T115 demo data/scenarios
- T116 security audit
- T117 architecture audit
- T118 final test/eval report

Exit:
- a new learner can clone, start, understand, and demo the project from docs alone


---

## FILE: MODEL_STRATEGY.md


# Codex Model / Budget Strategy

## 1. Core rule

Spend intelligence on decisions, not repetition.

Use a stronger model to decide:
- boundaries
- invariants
- state machines
- security
- recovery
- concurrency
- migrations
- acceptance criteria

Then use a cheaper model to implement tasks whose decisions are already written down.

## 2. Three tiers

### Tier A — Architecture / high reasoning

Use for:
- Phase 0
- schema redesign
- Agent graph
- failure recovery
- checkpoint/resume
- tenant security
- side-effect safety
- concurrency
- hard bugs
- final review

Expected output should often be a plan/spec first, not immediate code.

### Tier B — Standard implementation

Use for:
- service methods
- APIs
- migrations after schema is decided
- tool adapters
- ordinary auth implementation
- trace endpoints
- UI flows
- integration tests

### Tier C — Low-cost execution

Use for:
- one CRUD endpoint
- one Pydantic model
- one migration from an already-approved schema
- unit tests for a fixed contract
- docs sync
- fixtures
- type hints
- straightforward validation
- simple UI component

## 3. Low-cost task size

Ideal low-cost task:
- touches 1 domain
- usually <= 3–6 files
- no framework replacement
- no new architectural pattern
- exact acceptance tests are known
- can be reverted independently

Bad prompt:
> 完成整个用户权限系统。

Good prompt:
> Implement `GET /api/v1/tasks/{task_id}` authorization using the existing TaskRepository and `CurrentUser` dependency. Return 404 for resources outside the caller's tenant. Add tests for same-tenant access and cross-tenant denial. Do not change token issuance or DB schema.

## 4. Context reuse

Put persistent rules in:
- `AGENTS.md`
- `PROJECT_SPEC.md`
- `docs/*`

Put current work state in:
- `process/PROGRESS_LOG.md`
- the current `TASK-xxx.md`

Do not repaste the whole project description in every prompt.

## 5. Two-pass pattern

For risky work:

Pass 1, strong model:
- inspect
- decide
- create task spec
- define acceptance tests

Pass 2, cheaper model:
- implement exactly that spec
- run tests
- update docs

Optional Pass 3, strong reviewer:
- review diff only
- focus on invariants/security
- do not rewrite unless necessary

## 6. Cheapest tasks to automate first

Documentation drift checks, test fixture additions, ordinary unit tests, type cleanup, endpoint examples, changelog entries, simple UI rendering, generated API examples.

## 7. Never delegate blindly to the cheapest model

Keep strong review for:
- auth bypass risks
- migrations deleting data
- transactions
- idempotency
- approval bypass
- arbitrary code execution
- prompt/tool injection boundaries
- cross-tenant retrieval
- external side effects


---

## FILE: CODEX_WORKFLOW.md


# Codex Working Method

## 1. Use planning for large changes

For architecture, ask Codex to inspect and plan first.
Do not mix “decide architecture” and “write 2,000 lines” in one opaque prompt.

## 2. Prompt like a GitHub issue

Every prompt should contain:
- goal
- context pointers
- scope
- constraints
- acceptance criteria
- tests
- docs

## 3. Keep durable repository context

Use `AGENTS.md` and versioned docs rather than repeating rules in chat.

## 4. Verification loop

Every implementation task:

Inspect
→ Plan
→ Edit
→ Focused test
→ Fix
→ Broader check
→ Docs
→ Progress log
→ Review diff

## 5. Work queue

Maintain a backlog of small tasks.
Do not ask a low-cost model to decide what the next 20 tasks should be.

## 6. Review discipline

For routine work:
- low-cost model implements
- automated tests validate

For security/state/architecture:
- stronger model reviews the diff and invariants

## 7. When stuck

Ask Codex to:
1. reproduce
2. isolate failing layer
3. explain root-cause hypothesis
4. add/identify failing test
5. apply minimal fix

Avoid “try random changes until it works”.
