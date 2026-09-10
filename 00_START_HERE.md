# TaskPilot Codex 开发包：从零开始

> 目标：让一个没有 Agent 工程经验的人，可以在 Codex 的帮助下，一边学习，一边把 TaskPilot 做成一个真实可运行、可恢复、可观测、可审批的 Agent 平台。

## 1. 先理解你要做的东西

TaskPilot 不是“聊天机器人”，也不是“几个 Prompt 串起来”。

它是一个面向真实知识工作任务的 Agent 执行平台：

用户提交任务 → 系统建立任务状态 → Planner 拆解 → Executor 调工具 → Verifier 验证 → 失败时 Recovery → 高风险操作等待人工审批 → 完成后保存结果与完整 Trace。

V1 重点支持四类业务：

1. Research：搜索资料、结合企业知识库形成研究简报。
2. Document：读取/比较文档、抽取信息、生成总结或报告。
3. Data Analysis：读取 CSV/XLSX，执行受限的数据分析并生成结论。
4. Action：执行会产生外部影响的动作；V1 先实现“审批 + dry-run/内部动作”，后续再接 Email、Calendar、CRM 等真实系统。

## 2. 推荐起点

推荐基于公开项目 `JoshuaC215/agent-service-toolkit` 二次开发，而不是从空目录手搓全部基础设施。

理由：它已经提供 LangGraph + FastAPI + PostgreSQL + 异步接口 + RAG + Human-in-the-loop + 多 Agent 支持 + Docker + 测试等基础能力。你要做的是把它改造成“真实任务平台”，而不是复制一个聊天 Demo。

### 初次准备

你需要：

- Git / GitHub 账号
- Docker Desktop
- 一个可用的 LLM API Key
- Codex
- 一个你自己的 GitHub 仓库（建议 fork 上游项目）
- 能看懂少量 Python；看不懂也没关系，本开发包会要求 Codex 同步写学习文档

## 3. 第一次打开 Codex 后做什么

不要让 Codex“一次性完成整个项目”。

第一轮只做：

1. 把本开发包中的 `AGENTS.md`、`MASTER_PROMPT.md`、`PROJECT_SPEC.md`、`ROADMAP.md` 等文件复制进你的项目。
2. 把 `MASTER_PROMPT.md` 的内容发给 Codex。
3. 接着执行 `prompts/00_repo_assessment.md`。
4. 让 Codex只检查现状、运行基线测试、写差距分析，不实现大量功能。
5. 你先阅读 Codex 生成/更新的：
   - `docs/ARCHITECTURE.md`
   - `docs/CODE_READING_ORDER.md`
   - `process/PROGRESS_LOG.md`
6. 再进入 Phase 1。

## 4. 你每天的工作方式

推荐固定循环：

> 读一个小任务 → 让 Codex 先解释 → 让它实现 → 看 diff → 跑测试 → 阅读学习说明 → 自己复述“这一块解决什么问题” → 再做下一个任务。

每次只做一个小任务。不要连续让低成本模型修改多个核心模块。

## 5. 强模型和低成本模型怎么分工

### 强模型优先处理

- 系统架构
- 数据模型大调整
- Agent State / Graph 设计
- 并发、事务、一致性
- 权限和安全
- Human-in-the-loop
- 失败恢复
- 跨模块疑难 Bug
- 最终架构审查

### 低成本模型优先处理

- 单个 CRUD
- 小范围 API
- Pydantic schema
- 普通单元测试
- 文档同步
- 类型标注
- 小型重构
- UI 单组件
- 测试 fixture
- README/使用说明更新

硬规则：低成本模型的任务应当“边界清晰、输入清楚、验收可自动测试”，通常不要跨两个以上核心领域。

## 6. 学习时不要追求全部记住

你只需要逐层理解：

1. HTTP/FastAPI：请求是怎么进来的。
2. Database：任务状态存在哪里。
3. Task domain：Task、Run、Step 分别是什么。
4. LangGraph：一个任务如何在节点之间流转。
5. Tool：Agent 如何调用外部能力。
6. Context：模型每一步到底看到了什么。
7. HITL：为什么能暂停并恢复。
8. Observability：怎么知道 Agent 做了什么。
9. Evaluation：怎么判断它真的变好了。
10. Deployment：如何让别人能使用。

详细顺序见 `docs/CODE_READING_ORDER.md` 和 `LEARNING_GUIDE.md`。

## 7. 什么时候算 V1 完成

至少满足：

- 两个测试用户可以隔离访问自己的任务。
- 用户能创建 Task 并看到 TaskRun / Step 状态。
- Planner 能生成结构化计划。
- Executor 至少能调用 3 类工具。
- Verifier 能给出 PASS / FAIL 与证据。
- 可恢复错误能重试或重新规划。
- 高风险 Tool Call 会进入 WAITING_APPROVAL。
- 用户批准/拒绝后任务能正确恢复。
- 每次 Agent/Tool 调用有 trace、latency、token/cost（能获取时）。
- 有固定 eval 数据集，可以重复跑。
- Docker Compose 能一键启动开发环境。
- README、用户文档、技术文档、代码阅读顺序与实际代码一致。

## 8. 原子任务

`TASK_BACKLOG.md` 给出了从 T000 到 T136 的原子任务地图。Phase 0 后，让强模型运行 `prompts/12_generate_next_low_cost_task.md`，把下一项变成带有真实文件路径和验收测试的低成本模型任务卡，再交给低成本模型执行。
