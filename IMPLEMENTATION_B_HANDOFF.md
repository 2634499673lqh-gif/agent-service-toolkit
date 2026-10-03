# Implementation B Handoff

## A 当前交付

Blocker 修复后的 A 增加了真正的登录/注销工作流（opaque session 仅保存在 React 运行时内存）、持续中文对话、澄清日期、显式方案确认、任务轮询、历史结果打开和实际 MapLibre GL JS 地图。A 仍复用确定性 conversation API；真实 LLM 意图理解和解释生成明确留给 B。

服务端 `GET /api/v1/tasks/{task_id}/runs/{run_id}/map` 会先做租户和成功/Verifier 检查，再从服务端 fixture manifest 校验指标对应的 manifest/hash，并返回 native CRS、native bounds、raster dimensions、scene identity、target transform、fixture version 和受信 artifact URL。客户端不得提交或覆盖这些值。

## 运行与证据

- `docker compose up -d postgres migrate agent_service web`
- 浏览器：`http://localhost:5173/`
- 前端：`pnpm --dir web install && pnpm --dir web run build && pnpm --dir web run typecheck`
- 已验证：focused GeoChange/Runtime tests、Ruff、Pyrefly、Docker Compose config、Web image build。
- 需现场提供账号和运行中的 PostgreSQL 才能完成真实登录、执行和 artifact 浏览器验收；没有这些条件时不能声称 E2E 已通过。

## B 入口

1. 在 `src/service/conversation_service.py` 替换确定性 proposal parser 为结构化真实 LLM，同时保留 Pydantic、显式确认、服务端 AOI/时段/指标校验和失败分类。
2. 在 `web/src/main.tsx` 保留 token 内存边界，把真实解释接入助手消息；不得把模型输出当授权或科学证据。
3. 扩展 `src/geochange/provenance.py` 仅在新增受信 manifest 后支持更多产品；当前不增加任意 AOI 或在线 raster 下载。


## Final A focused fixes

Chinese year/month requests such as “武汉东湖地区 2023 年 7 月与 2024 年 7 月的 NDVI 变化” now normalize only the controlled Wuhan East Lake label and bounded month periods before ConfirmedIntent validation; unknown areas remain rejected. MapLibre reports only source-scoped tile/result failures, clears transient errors after recovery, and authenticated artifact fetches use the in-memory bearer token before creating a blob URL for the georeferenced image source. Pydantic validation details are mapped to a concise Chinese safety message at the confirm route.

## Raster visualization correction

The result map now waits for the MapLibre `load` lifecycle event instead of dropping a one-time pre-style attempt. Each authenticated artifact is checked as a non-empty PNG, decoded before use, added with trusted native bounds and EPSG:32650-to-WGS84 corner conversion, and considered successful only after the source/layer exists and MapLibre reaches `idle`. The UI distinguishes computation complete, artifact fetched, and map layer rendered; failures remain visible as concise Chinese diagnostics. The 64×64 fixture is fit to its native footprint rather than stretched across the AOI.

The final state correction now uses the result source's `isSourceLoaded` state rather than a global `idle` event, preventing a rendered image from being reported as failed when unrelated map activity keeps `idle` pending. Superseded requests are cancelled, and late raster errors no longer overwrite a confirmed rendered state. NoData alpha/masking remains supplied by the trusted PNG; the client does not reinterpret low index values.
