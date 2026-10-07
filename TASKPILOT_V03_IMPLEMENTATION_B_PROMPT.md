# TaskPilot V0.3 Implementation B — Dynamic NDVI & Product Integration

你是独立的 Codex Implementation 会话。仅在 Implementation A 已经通过独立 Strong Review、用户确认其接口和 baseline 后执行。本批次只做 B；不得重建 A、改变 A 的 AOI/provider 科学契约或把旧 fixture 当作动态数据。

## 当前 V0.3 状态与已冻结门槛（2026-10-07）

Implementation A、Implementation B 均为 COMPLETE / APPROVED；browser product wiring 为 COMPLETE，manual browser acceptance 为 PASS。用户已冻结 final NDVI-valid data coverage `>=60%`/period 和 final common-comparison valid-data coverage `>=50%`。这些是 V0.3 数据质量/有效比较门槛，不是 vegetation coverage、FVC 或植被面积百分比。Coverage-gate focused closure COMPLETE；focused Final Audit re-review APPROVED。上一轮 Final Audit NOT APPROVED 的唯一原因是 freeze decision 尚未正式记录；该 blocker CLOSED。Remaining blocker: none。V0.3 为 COMPLETE / FINAL AUDIT APPROVED；PR / merge main pending。历史记录中的 provisional 与 NOT APPROVED 状态保留为历史，不改变当前结论。

## 1. Dependency and baseline gate

先阅读 `AGENTS.md`、`TASKPILOT_V03_IMPLEMENTATION_PLAN.md`、本 Prompt、A 的最终报告/接口模型和当前仓库状态。只读核对：

- approved A 的 commit/文件/接口是否实际存在；
- `git status --short`、当前 branch、HEAD、remotes；
- `src/geochange/aoi.py`、A 的 Landsat preparation modules、`skill.py`、`raster.py`、`provenance.py`、`verifier.py`；
- `src/schema/confirmed_intent.py`、`src/service/task_runtime.py`、Task/TaskRun repositories、artifact/map/conversation APIs、`web/src/main.tsx`；
- 现有 GeoChange/authorization/postgres/browser acceptance tests。

若 A 未获批准、接口字段不完整、V0.2 B 尚未完成独立复核并合入用户批准的更新后 `main`、实际 baseline 不是用户批准版本，或要靠改 A contract 才能继续，停止并报告。不要自行修 A、merge、rebase、switch branch、commit、push、PR 或 merge。

## 2. Frozen B scope

实现一个新的、与 legacy Sentinel-2 隔离的 execution path：

- `data_mode`/execution mode：`real_stac_landsat_local`（名称可等价，但必须独立且稳定）；
- AOI：`jianghan_district_420103` / `武汉市江汉区` / `admin_code=420103`；
- indicator：`NDVI`，analysis type `vegetation_change`；
- source：Landsat 8/9 C2 L2 SR，Planetary Computer `landsat-c2-l2`；
- periods：用户确认的两个不重叠 monthly windows，默认 2023-07 与 2024-07；每期必须是 `[YYYY-MM-01, next_month-01)` UTC、年份 2023–2025、≤31 天，两期不重叠。替代月份只能来自新 proposal 和再次确认；不能静默替换 July。
- input：A 已完成的两个 `PreparedPeriodDataset` 和 A 生成的 `PreparedPeriodPair.common_preparation_valid_mask`；B 不接收 raw DN、未对齐 window 或 provider URL。
- output：两期 NDVI、delta、`final_ndvi_valid_mask_a/b`、`final_common_comparison_mask`、numeric georeferenced rasters、派生 PNG/map layers、trusted metrics/provenance。

不得增加动态 NDWI/NDBI、全国 AOI、GEE、多 provider、SHP 上传、分类/归因/趋势、新 Agent 框架或任意地理 URL。旧 `CACHED_REAL_SENTINEL2_RASTER`、NDWI/NDBI exploratory path、旧 artifact 和 East Lake history 必须继续可测试和可读。

## 3. B 调用 A 的唯一入口

禁止从 B 重新解析 STAC、拼接 href、重新选择 scene、重新做 priority-fill/composite、解 QA 或 reprojection。B 只调用 A 发布的 server-owned preparation boundary：

```text
resolve_aoi(aoi_id) -> TrustedAOI
prepare_landsat_periods(trusted_aoi, period_pair, limits) -> PreparedPeriodPair | PreparationFailure
```

B 的输入中不得有 SAS URL。它必须把 A 返回的 AOI/source/scene/grid/hash 作为 evidence binder，不能被 LLM、浏览器或请求体覆盖。A 的 handoff 必须包含 `contract_version="v0.3-preparation-1"`、两个同 shape、同 pair-wide target grid 的 float32 Red/NIR arrays、bounded `requested_period`、`preparation_valid_mask`、`aoi_mask`、`source_scene_index`、A 生成的 `common_preparation_valid_mask`、每期不超过 3 个 scene/asset identities（含物理 band→PC asset-key map）、date/provenance、hard-limit evidence、operational metrics 和 warnings。沿用批准的 500,000 pixels/period、3 scenes/period、最多 2 个远端 asset operations、180 s request/task deadline、128 MiB temporary disk、128 MiB arrays/GDAL cache、每个六类 numeric/mask artifact 16 MiB（整 run 96 MiB）和 3 retries；network bytes 只作为 observed metric/warning，不能当作硬 cap。`wait_for` 不是 GDAL cancellation proof；A 必须提供 blocking-read child termination/cleanup evidence。

## 4. ConfirmedIntent 和 proposal 生命周期

在 `ConfirmedIntent`、proposal parser、确认 API 和 TaskRuntime 中增加/接通江汉+Landsat+NDVI 的明确组合。服务器必须验证：

1. AOI id、admin code、geometry hash 与 A 输出一致；
2. indicator/analysis type 是 NDVI/vegetation_change；
3. period A/B 与用户确认完全一致、不重叠且符合 monthly-window bounds；
4. `data_mode` 是受信 `real_stac_landsat_local`；
5. execution 只能使用确认的 AOI、dates、sensor/collection。

proposal 状态至少能区分 draft/ready、confirming、submitted、superseded、failed/cancelled；不能因 React object-reference 改变而把刚生成的 proposal 当 expired。已 submitted proposal 必须幂等，快速重复确认不能创建重复 Task/TaskRun。未确认不得执行 provider 或创建任务。

DeepSeek 的 structured intent 只是候选；服务端 Pydantic/allowlist 才是授权边界。不能把模型生成的 AOI/scene/date/href 当事实。

## 5. 动态 NDVI 执行与 verifier

### 5.1 Prepared-data policy

在执行时调用 A 的 `prepare_landsat_periods`，保存 A 返回的最终真实 item/date/asset identities、AOI intersection、AOI rasterized pixel count、preparation-valid coverage、priority-fill source scene indices、selection reason 和 failure classification。scene-wide cloud cover 只作为 A 的排序证据，不替代 AOI mask。B 不得重新筛选 scene、拼 composite、解 QA 或重投影。A 返回 `insufficient_preparation_coverage`、缺资产、云/coverage 不足或其他 preparation failure 时，B 原样映射为可操作失败，不静默改月。

### 5.2 计算

消费 A 已校验、定标和对齐的 arrays/masks；不要从 raw DN 或不同 grid 数组直接相减。A 已完成 DN/radiometry/QA/composite/reprojection；B 先逐期计算 NDVI 和 `final_ndvi_valid_mask`，再以两期 mask 的交集计算：

```text
NDVI = (NIR - Red) / (NIR + Red)
delta = NDVI_period_b - NDVI_period_a
```

`final_ndvi_valid_mask_p = aoi_mask & preparation_valid_mask_p & finite(red_p) & finite(nir_p) & finite(denominator_p) & (abs(denominator_p) > 1e-6) & finite(ndvi_p) & (ndvi_p within [-1-1e-5,1+1e-5])`；B 必须校验 A 的 `common_preparation_valid_mask == aoi_mask & preparation_valid_mask_a & preparation_valid_mask_b`，并生成 `final_common_comparison_mask = common_preparation_valid_mask & final_ndvi_valid_mask_a & final_ndvi_valid_mask_b`。A/B means 和 delta stats 必须使用这一相同 final comparison mask；period-specific counts 另行报告。保留质量/辐射率有效水像元的负 NDVI；NoData、QA invalid、Red/NIR 对应饱和或 denominator 非 finite/`abs(sum) <= 1e-6`、NDVI 非 finite/超范围才排除。B 不因负 reflectance、低反射率或 water bit 单独排除像元。Verifier 必须检查 A 的 DN/QA/radiometry evidence、finite、range、preparation/final/common counts、A/B mean、delta identity、period/common coverage、target grid、AOI area 和 artifact/checksum 绑定。不得从 NDVI 低值推导植被面积或因果。

最小 metrics：`aoi_area_m2`、`aoi_rasterized_pixels`、`preparation_valid_pixels_period_a/b`、`final_ndvi_valid_pixels_period_a/b`、`final_common_comparison_pixels`、`preparation_coverage_period_a/b_pct`、`final_ndvi_coverage_period_a/b_pct`、`final_common_comparison_coverage_pct`、`mean_ndvi_period_a`、`mean_ndvi_period_b`、`mean_delta_ndvi`，以及有理由保留的 min/max。所有 coverage denominator 都是 pair-wide `aoi_mask` pixels；不得用 scene footprint、bbox 或行政面积替代。V0.3 final NDVI-valid data coverage gate 为 ≥0.60/period，final common-comparison valid-data coverage gate 为 ≥0.50，已由用户冻结；它们不表示 vegetation coverage、FVC 或植被面积。Preparation gate 依照 A contract。任一不满足返回 `insufficient_ndvi_coverage` 或 `insufficient_comparison_coverage`，不生成成功统计。字段有界、finite、不可由用户覆盖。

### 5.3 Evidence/result boundary

扩展现有 `SkillSpec`/result schema/Verifier 以允许独立 dynamic execution mode 和动态 provenance；不得把旧 `MANIFEST_SHA256` 复制给 Landsat。结果在 verifier 通过前不可标记 succeeded/complete。LLM 文本只能引用 verified metrics/scene dates/source/limitations，服务端证据优先于模型原话。

科学规则以 USGS [Collection 2 Level-2 product table](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)、[negative surface reflectance FAQ](https://www.usgs.gov/faqs/why-are-negative-values-observed-over-water-some-landsat-surface-reflectance-products) 和 [QA bit definitions](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands) 为依据；实现不得用旧 Sentinel-2 fixture 规则替代这些 Landsat 规则。

## 6. 持久化、artifact 与地图

优先复用现有 TaskRun `result_metadata`、tenant-scoped artifact endpoint 和 `data/geochange-artifacts` mount，不新增表。metadata 至少保存：

- `aoi_id`, `aoi_source_version`, `aoi_source_url`, `aoi_acquired_at`, `aoi_hash`, `aoi_license`, `admin_code`, `aoi_area_m2`；
- provider/collection/platform、period A/B、每期 scene count/date/item ids/asset identity hashes；
- source/target CRS、transform、dimensions、resolution、AOI/footprint bounds；
- valid/common counts/coverage、metrics、execution mode、verifier status、`hard_limits_applied`、`operational_metrics`、`best_effort_warnings`；
- numeric raster/mask/PNG artifact names and SHA-256。

不得保存 SAS、raw credential、完整 provider response 或任意 URL。服务端固定 allowlist：PNG IDs `ndvi_before`, `ndvi_after`, `ndvi_change` remain display-only `.png`/`image/png` ≤2 MiB；numeric IDs `ndvi_before_raster`, `ndvi_after_raster`, `ndvi_change_raster` are float32 GeoTIFF `.tif`/`image/tiff` ≤16 MiB each；mask IDs `ndvi_valid_before`, `ndvi_valid_after`, `ndvi_common_comparison` are uint8 GeoTIFF `.tif`/`image/tiff` ≤16 MiB each；调用方不能提交 extension/path；每个 reference 必须绑定 tenant/task/run、SHA-256、AOI/period/grid/provenance hash，并重新以原始 numeric dtype/CRS/transform 打开校验。GeoTIFF/数字 mask 是科学权威；PNG/map tiles 只做展示。artifact path 仍需 tenant/run authorization、allowlist 和大小上限，容器重建后从现有 mount 可恢复。若必须新 migration 才能保存上述 bounded metadata，停止并说明为什么 `result_metadata` 不足，不自行改变数据库契约。

MapLibre 接入现有左聊天/右地图：

1. 确认前只显示受信 Jianghan boundary、source/version/license、默认或用户请求的 period policy 和已知约束；不显示尚未取得的真实 scene dates 或 pixel coverage，且 confirmation 必须先于 Task 创建/provider execution；
2. 成功后显示真实 AOI、scene footprint、NDVI A/B/delta 图层；
3. 从服务端 metadata 使用真实 geographic corners/bounds/CRS，透明 NoData、正确 legend、日期和 coverage；
4. Backend Run 的 succeeded/verifier_passed 与 frontend map rendered 独立记录；图层获取/source loaded/render 失败只产生 map error，不能把真实成功 Run 改写为 failed；
5. 不用客户端 geometry/bounds 覆盖服务端值，不用视觉平移修正投影。

历史打开必须读取原 TaskRun 的 immutable provenance/artifacts；容器重建不删除成果；缺失旧 PNG 明确显示 unavailable，不创建伪 artifact；旧 East Lake fixture 只能标成历史路径，不能显示成 Jianghan。

## 7. DeepSeek 交互

保留现有受控上下文、timeout/concurrency、no-key/provider failure 分类和内存 token 边界。proposal 解读必须展示真实 provider、实际 dates、coverage 和限制。result interpretation endpoint 只能读取 tenant-authorized、verifier-passed result metadata，并把 evidence 作为不可覆盖的上下文。

以下情况不得产生“分析完成”：未确认、provider 不可用、A 返回 insufficient/invalid、Verifier failed 或 artifact 缺失。Map source/load/render failure 必须显示明确 map error，并保持已验证 Backend Run 的成功状态，不得把该 Run 改写为 failed。无 key/timeout/429 显示明确可行动的 AI unavailable/provider unavailable，不显示虚假的 online。

## 8. 必须添加的测试与验证

普通单元测试 mock Planetary Computer；仅一个小型真实 provider smoke 允许访问网络。至少覆盖：

- 新 AOI/intent/data_mode 匹配、wrong AOI/indicator/sensor/period、未确认执行、proposal 过期回归、重复确认幂等；
- A/B/preparation/final/common metrics、A handoff shape/grid/mask、priority-fill same-scene Red/NIR binding、physical band→PC asset-key mapping、water negative reflectance/negative NDVI、bright-target reflectance >1、empty mask、coverage gates、scene/date/asset binding、QA 和 radiometry evidence；
- verifier 拒绝 forged metadata/metrics/artifact、dynamic 结果使用旧 fixture hash、raw DN 计算、scene-wide cloud 冒充 local coverage；
- 401/403/404、跨租户 TaskRun/artifact/map/history、missing artifact、历史不可用、容器重建后路径；
- provider 429/5xx/timeout/SAS expiry/host redirect、hard scene/window/pixel/concurrency/timeout/array/GDAL-cache/temp-disk budget exceeded、observed network-byte/RSS metrics、blocking-read child termination and cleanup evidence、cancel/terminal state；
- React proposal lifecycle、AOI confirmation、layer switching/legend/error recovery；
- 旧 NDVI/NDWI/NDBI fixture 和 runtime/replan/resume regression。

建议命令：

```powershell
uv run pytest -q tests/geochange tests/runtime tests/service/test_conversation_service.py
uv run pytest -q tests/service tests/persistence  # 若共享基础设施受影响
uv run ruff check
uv run ruff format --check
uv run pyrefly check
uv lock --check
pnpm --dir web run typecheck
pnpm --dir web run build
docker compose config
docker compose build
docker compose up -d postgres migrate agent_service web
# 配置 TASKPILOT_TEST_DATABASE_URL 后运行受影响 PostgreSQL suites
git diff --check
```

若没有浏览器自动化，必须把真实账号登录、AOI 确认、地图渲染、历史重开记录为“需人工验收”，不能宣称 browser E2E passed。真实端到端 gate 需要一名授权用户完成：

`Jianghan request → AOI/period/indicator/data-mode confirmation → real Landsat selection → verifier-passed NDVI → real map layers → evidence-grounded DeepSeek interpretation → history reopen`

如果默认 July 不足，先以 `insufficient_preparation_coverage`、`insufficient_ndvi_coverage` 或 `insufficient_comparison_coverage` 结束，并只展示已获取证据支持的候选替代时段；只有用户重新确认新 period pair 后，才能执行替代演示。替代演示不得重写 July proposal 或把结果标作默认 July。

## 9. B 的停止条件

立即停止并回报：A contract 不完整或被迫重写；V0.2 B 未合入批准的更新后 `main`；需要全国 AOI/provider/GEE；必须放宽 QA/coverage/verifier/security；需要重大 migration/不可逆外部操作；无法阻止历史 East Lake 污染；资源预算不足以完成冻结目标；或真实数据不足只能静默换期。给出证据、1–2 个选项和推荐，不自行做高影响决定。

## 10. B 最终报告（必须包含）

用中文报告：实际 branch/HEAD/status；逐文件变更；A 接口版本和未改动边界；ConfirmedIntent/proposal/task safety；动态 NDVI science evidence；AOI/scene/grid/provenance；metrics/verifier/artifacts；tenant/security/secret 检查；Docker/数据库/前端/测试命令及结果；真实 provider smoke；真实浏览器 acceptance 是否完成；历史重开和容器重建证据；insufficient/fallback 情况；已知限制与用户决策。

最终结果只能是：

- `IMPLEMENTATION B READY FOR STRONG REVIEW`：实现和证据完整，但不预写 Strong Review/Final Audit；或
- `BLOCKED`：列出最小 blocker 和需要批准的决策。

不要 commit、push、PR、merge，也不要把 reviewer 的 `APPROVED` 写入实现报告。
