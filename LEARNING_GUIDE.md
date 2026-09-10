# Learning Guide — 零基础跟项目学 Agent 工程

## Stage 1：先学“请求怎么进系统”

目标：理解 FastAPI，不追求框架细节。

你要能回答：
- 一个 POST 请求从哪个文件进入？
- Pydantic schema 做什么？
- route 为什么不应该塞业务逻辑？
- HTTP 401/403/404 有什么区别？

练习：
手工调用 health endpoint，再找到对应 route 和 handler。

## Stage 2：理解数据库

你要能回答：
- ORM model 和 API schema 有什么区别？
- migration 为什么重要？
- Task 保存在哪张表？
- user_id / org_id 为什么必须明确？
- transaction 是什么？

练习：
创建一条 Task，再在 DB 中找到它。

## Stage 3：理解 Task / Run / Step

核心：
- Task = 用户想完成什么
- TaskRun = 一次实际执行
- TaskStep = 计划/执行的一个步骤

练习：
不用任何 LLM，手工创建一个三步 TaskRun。

## Stage 4：理解 LangGraph

只理解 5 个词：
- State
- Node
- Edge
- Checkpoint
- Interrupt

练习：
画出 Planner → Executor → Verifier → End/Recovery。

## Stage 5：理解 Tool 与 Skill

Tool = 可执行动作。
Skill = 一组可复用的方法/SOP/工具组合。

练习：
写一个 deterministic calculator 或 CSV summary tool，并从 Agent 调用。

## Stage 6：理解 Context Engineering

你要理解：
模型“聪不聪明”之外，关键是每一步给它什么。

练习：
打印一次 Planner 真正得到的 context summary，并标记每段来源。

## Stage 7：理解 Human-in-the-loop

关键不是按钮，而是：
- pause
- persistence
- authorization
- resume
- idempotency

练习：
构造一个必须审批的 mock action；批准前确认它绝对没有执行。

## Stage 8：理解 Observability

你要能从一个 task_id 找到：
- run
- step
- agent
- tool
- error
- retry
- latency

练习：
人为让工具失败一次，然后在 trace 中解释发生了什么。

## Stage 9：理解 Evaluation

“回答看起来不错”不是指标。

练习：
写 10 个固定任务，每次改 Agent 后重跑，比较：
- success rate
- verifier pass
- tool success
- retry
- latency
- cost

## Stage 10：理解部署

你要知道：
- Docker image
- container
- service
- env
- health check
- DB migration
- logs

先会 Docker Compose，不急着 Kubernetes。
