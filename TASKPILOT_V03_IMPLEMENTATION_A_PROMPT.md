# TaskPilot V0.3 Implementation A — Trusted AOI & Data Preparation

你是独立的 Codex Implementation 会话。执行本 Prompt 前必须阅读仓库根目录 `AGENTS.md`、本文件和 `TASKPILOT_V03_IMPLEMENTATION_PLAN.md`。本批次只实现 A；不得实现 B 的完整 Dynamic NDVI Agent workflow。

## 1. Authority and baseline gate

用户已批准本 Prompt 后，仍以实际 Git 状态为权威。先只读检查：

- `git status --short`
- `git branch --show-current`
- `git rev-parse HEAD`
- `git remote -v`
- `git log --oneline --decorate -12`
- `git diff --stat main...HEAD`

`codex/taskpilot-v02-b` / `22b189e` 只是当前观察值，不是 A 的启动授权。A 只能从用户批准、已完成 V0.2 B 独立复核并合入的更新后 `main` 开始；推荐由用户随后创建 V0.3 分支。若 V0.2 B 尚未合入更新后的 `main`，或 proposal 过期/历史 East Lake geography 问题尚未完成批准的处理，状态为 `BLOCKED BY BASELINE GATE`，停止并报告，不改代码。不得自行切换/创建分支、reset、clean、restore、stash、commit、push、PR 或 merge。

保留工作树已有的 `process/PROGRESS_LOG.md` 修改和未跟踪 V0.2 文档；只修改属于 A 的文件以及必要的测试/依赖/锁文件。若依赖修改超出用户批准范围，先停止。

## 2. Frozen scope

本批次把唯一受支持 AOI 做成真实、可验证的江汉区：

- id `jianghan_district_420103`
- `武汉市江汉区` / `Jianghan District, Wuhan`
- `admin_code=420103`
- OSM relation `3077256` version `21`，必须提交 immutable GeoJSON snapshot，按固定 UTF-8 canonical serialization 计算 snapshot SHA-256，记录 relation/version、获取日期、源 URL/查询、ODbL attribution/license
- GeoJSON `Polygon`/`MultiPolygon`，真正 `EPSG:4326`

Provider 固定 Microsoft Planetary Computer STAC `https://planetarycomputer.microsoft.com/api/stac/v1`，collection `landsat-c2-l2`，Landsat 8/9，Level-2 Surface Reflectance。A 默认支持 2023-07-01..2023-07-31 与 2024-07-01..2024-07-31；也必须支持同一 AOI/source/NDVI 的 bounded alternative monthly windows：每期为一个自然月 `[YYYY-MM-01, next_month-01)` UTC 区间、年份 2023–2025、跨度不超过 31 天、两期不重叠，每期仍受 20 candidate/3 selected scene 上限。替代 period 必须来自新 proposal 和显式用户确认，不能静默换月；不保证任何 period 一定能通过 AOI 质量门槛。

不得：改前端布局、实现完整 Dynamic NDVI workflow、替换现有 Skill/fixture、实现动态 NDWI/NDBI、添加 GEE/多 provider/任意 URL/全国 AOI/新 Agent 框架、重写旧 East Lake evidence。

## 3. 先阅读的仓库代码

至少检查并遵循现有契约：

- `src/geochange/aoi.py`
- `src/geochange/stac.py`
- `src/geochange/raster.py`
- `src/geochange/fixture.py`
- `src/geochange/provenance.py`
- `src/geochange/skill.py`
- `src/geochange/verifier.py`
- `src/schema/confirmed_intent.py`、`src/geochange/models.py`
- `src/service/task_runtime.py`、`src/service/task_api.py`、`src/service/conversation_api.py`
- `src/runtime/capability.py`
- `compose.yaml`、`docker/Dockerfile.service`、`pyproject.toml`、`uv.lock`
- `tests/geochange/`、`tests/runtime/`、`tests/service/` 中现有 GeoChange/authorization/artifact tests

先画出当前 legacy Sentinel-2 fixture 路径：AOI → STAC/metadata → deterministic raster → Verifier → TaskRun/artifact。A 只能以增量方式添加 Landsat preparation path。

## 4. A 的实现责任

### 4.1 Trusted AOI

扩展现有 AOI 目录或新增最小的 server-owned catalog，使 `resolve_aoi("jianghan_district_420103")` 只返回受信对象。将 OSM relation 3077256 version 21 导出为 immutable GeoJSON snapshot；运行时只加载该 snapshot，并保存 source URL、relation/version、acquisition timestamp、license、snapshot SHA-256。校验：supported id、admin code/name、geometry validity、坐标范围、bbox、20–50 km² 面积量级、centroid/位置、multipart/holes、无 antimeridian。显示几何可简化，但精确裁剪几何和 hash 必须保留；不能把 GCJ-02 当 WGS84。

禁止从用户文本、LLM 或客户端传入的 geometry 直接决定文件/URL。现有 East Lake resolver 和 fixture 仍必须通过原测试。

### 4.2 STAC discovery / selection

提供最小 typed 接口（可用等价名称，但字段语义固定）：

```text
resolve_aoi(aoi_id: str) -> TrustedAOI
discover_landsat(aoi: TrustedAOI, periods: PeriodPair, limits: DiscoveryLimits) -> DiscoveryReport
select_landsat_scenes(report: DiscoveryReport, policy: SelectionPolicy) -> SelectedScenePair
```

`DiscoveryReport` 必须返回 stable item id、date、footprint、collection、platform、processing level、scene cloud metadata、actual asset keys 和 provider identity。使用 polygon intersection；bbox 仅作受限查询，不可视为覆盖证明。Implementation A 必须根据实际 STAC Item/asset metadata 和 MTL 建立并验证以下物理波段→Planetary Computer asset-key 映射：`SR_B4`（Red）→`red`，`SR_B5`（NIR08）→`nir08`，`QA_PIXEL`→`qa_pixel`，`QA_RADSAT`→`qa_radsat`，可用时 `SR_QA_AEROSOL`→`qa_aerosol`。`SR_B4`/`SR_B5` 只能作为物理 band identity，绝不能直接作为 asset dictionary key；缺失、错键、href/metadata 不一致时失败关闭，并把实际键和证据写入 sanitized provenance。
已核验的 2023-07 Item `LC08_L2SP_123039_20230727_02_T1` 提供 `red`, `nir08`, `qa_pixel`, `qa_radsat`, `qa_aerosol` keys，red/nir08 href basename 对应 `SR_B4`/`SR_B5`；实现必须把这类 Item/MTL 证据复核扩展到最终选中的每个 Item，不得把该样例当作 AOI coverage 证据。

候选排序必须确定性：AOI 相交/coverage evidence、像元级 cloud-free 质量、scene cloud、日期接近度、item id。单景足够时单景优先；最多 3 景/period。A 必须完成同月多景 preparation，MVP 使用 deterministic priority-fill：按排序逐景把同一 scene 的 Red/NIR/QA 对齐到共同 target grid，只用质量通过且尚未填充的像元填充；同一像元的 Red/NIR 必须来自同一 scene，并记录 `source_scene_index`。B 不得重新选择、composite 或解释这些数组。不要选“第一个 item”。

### 4.3 SAS 和 bounded COG

所有 signing 只在服务端执行。SAS 不得出现在返回对象、日志、TaskRun、fixture 或 durable provenance。只允许 Planetary Computer STAC/blob host，拒绝用户 URL、LLM href、意外 redirect host。

使用 Rasterio/GDAL 的 window/Range read；硬执行 AOI/scene bounds intersection、max 500,000 target pixels、单 window 262,144 pixels、最多 2 个远端 asset operations、180 s request/task deadline、数组+GDAL cache 128 MiB（建议各 64 MiB）、每 run 128 MiB temporary disk、每个数值/mask artifact 16 MiB 和六个 V0.3 numeric/mask artifacts 合计 96 MiB。对 403/429/5xx/timeout 最多 3 次重试，不能用无限 retry。Rasterio/GDAL 不能跨 driver 可靠保证整个 run 的 network byte cap；只记录可观测的 `bytes_observed`，256/512 MiB 是 warning threshold，不得声称硬限制。`asyncio.wait_for` 只取消等待，不等于 blocking GDAL 已终止；实际读取必须在可监督 child process 中执行，deadline/cancel 后 terminate、bounded join、必要时 kill、再次 join，关闭 pipes/handles、删除 temp files，并记录 child exit/cleanup evidence，才释放资源。支持取消和超时，失败不写成功结果，不添加 proxy/transport framework 或 1 GiB/tenant provider cache。

### 4.4 Radiometry / QA / alignment primitives

对实际 asset metadata 验证 dtype、存储 DN valid range `1..65455`、nodata/fill `0`、scale、offset、CRS、transform、shape。剔除 DN=0 和超存储范围值后应用 `DN * scale + offset`；预检的 `0.0000275/-0.2` 只有在本次 metadata/MTL 再次证明后才能使用。负 reflectance 或大于 1 的 reflectance 默认保留并记录 `out_of_nominal_sr_range`，不因符号自动 mask；USGS 明确水体负值和亮目标大于 1 可是已知算法伪影。实现 QA_PIXEL bits：fill、dilated cloud、high-confidence cirrus、high-confidence cloud、high-confidence cloud shadow、high-confidence snow；QA_RADSAT 对 Red(B4) bit 3、NIR(B5) bit 4 和 terrain occlusion bit 11 屏蔽。Clear bit 不能代替完整 QA；water bit 7 只记录，不默认屏蔽。`SR_QA_AEROSOL` 的 fill/valid retrieval/interpolated/aerosol level 作为质量诊断，不把 aerosol interpolation 自动等同于 NoData。NDVI denominator 使用 finite 且 `abs(NIR+Red)>1e-6`，结果必须在 `[-1-1e-5,1+1e-5]` 内；这些是 B 的 final NDVI validity checks，不是 A 的 preparation mask。

提供完整共同网格/掩膜和 period preparation：以 source metadata 计算一个 pair-wide target CRS/transform/shape；连续反射率与分类 QA 用不同 resampling；在每个 period 完成同月 priority-fill、AOI mask、`preparation_valid_mask`、source scene index、coverage 和 provenance。A 必须返回两个完全准备且已对齐的 period datasets，以及 A 计算的 `common_preparation_valid_mask`；B 不得重新做 scene selection、QA、composite 或 reprojection。A 不生成 final NDVI-valid mask；B 在同一 grid 上完成 finite/denominator/range checks 后生成 final masks。

科学字段和 bit 定义以 USGS [Collection 2 Level-2 product table](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)、[negative surface reflectance FAQ](https://www.usgs.gov/faqs/why-are-negative-values-observed-over-water-some-landsat-surface-reflectance-products) 和 [QA bit definitions](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands) 为依据；实现必须把这些来源和版本写入测试/报告，不得用旧 Sentinel-2 fixture 规则替代。

### 4.5 Docker capability

优先在现有 `agent_service` image 固定兼容的 Rasterio/GDAL/NumPy；不得依赖 Windows host 临时安装。运行 Docker build 和 bounded live read。若需要新 microservice 或不可逆 migration，停止并报告理由和 1–2 个方案。

## 5. 发布给 B 的冻结接口契约

A 完成后，在代码 docstring/typed model 和最终报告中发布以下字段，不携带 SAS：

```text
TrustedAOI:
  aoi_id, canonical_name, admin_code, geometry, bbox, crs,
  source, source_version, source_url, acquired_at, source_hash, license, area_m2

PreparedPeriodDataset:
  period_id: "a" | "b"
  requested_period: {start_utc, end_utc}    # one bounded calendar month
  red_reflectance: float32[height,width]       # NaN only where invalid
  nir_reflectance: float32[height,width]       # NaN only where invalid
  preparation_valid_mask: bool[height,width]  # QA + radiometry valid; not final NDVI validity
  aoi_mask: bool[height,width]
  source_scene_index: int16[height,width]      # -1 where no priority-fill source
  target_grid: {crs, transform, width, height, resolution_m}
  coverage: {aoi_rasterized_pixels, preparation_valid_pixels, preparation_coverage_pct, scene_count}
  provenance: {scene_ids <= 3, acquisition_dates, asset_key_map,
               asset_identity_hashes, qa_policy, scale_offset,
               resampling, composite_policy, source_hashes}

PreparedPeriodPair:
  period_a: PreparedPeriodDataset
  period_b: PreparedPeriodDataset
  common_preparation_valid_mask: bool[height,width] # aoi_mask & preparation_valid_a & preparation_valid_b
  pair_grid: target_grid                       # identical for a and b
  hard_limits_applied: {...}
  operational_metrics: {bytes_observed, elapsed_ms, peak_rss_bytes?}
  best_effort_warnings: [...]
  contract_version: "v0.3-preparation-1"
```

所有 arrays 必须同 shape、同 pair-wide target grid、同 AOI mask；reflectance 是已定标 float32，不能返回 raw DN、未对齐 window 或 SAS URL。`aoi_mask` 是 pair-wide target grid 的 rasterized AOI；`preparation_valid_mask` 只表示 QA/辐射率有效；`source_scene_index` 为 int16，-1 表示无 priority-fill source。`source_scene_index` 非 -1 时，Red/NIR 必须来自同一 scene。A 只发布 `preparation_valid_mask` 和 `common_preparation_valid_mask`；B 必须从两期 reflectance 完成 final NDVI finite/denominator/range checks，并生成 `final_ndvi_valid_mask_a/b` 及 `final_common_comparison_mask`。每期 scene/asset 列表最多 3 项，所有 metadata/warnings/diagnostics 有界且去敏；stable asset identity 可以含 canonical href hash 或 provider path，但不能含 query SAS。

A 失败时不返回部分可执行 dataset，返回有界 `PreparationFailure {code, stage, retryable, counts, sanitized_diagnostics}`：`unsupported_aoi`, `no_candidate`, `missing_asset`, `provider_unavailable`, `security_rejected`, `read_budget_exceeded`, `invalid_raster_metadata`, `invalid_radiometry`, `insufficient_preparation_coverage`, `grid_alignment_failed`, `no_common_preparation_pixels`, `timeout`, `cancelled`。失败必须无成功 artifact、无可执行半成品；允许保留去敏的失败诊断和预算/来源 evidence，但不得保留 SAS 或把部分数组当成功输入。

## 6. 必须添加的测试与证据

普通测试不依赖网络，provider client 要可 mock。至少覆盖：

- AOI id/name/code/geometry/hash、非法 geometry、越界坐标、错误 CRS、East Lake legacy 回归。
- STAC 缺 collection、错误 platform/date、缺 `SR_B4`/`SR_B5`/`QA_PIXEL`、错误 asset host、非 finite cloud、bbox-only 假覆盖。
- SAS 过期重签、429/5xx bounded retry、超过重试预算、意外 redirect/SSRF、URL 不进入日志/metadata。
- COG corrupt、invalid CRS/transform、window 越界、超过 scene/window/pixel/array/GDAL-cache/temp-disk/timeout budget、network-byte observed warning、取消传播。
- DN=0、DN 超存储范围、scale/offset、负水体 reflectance、亮目标 >1 reflectance、finite/denominator、QA 每个 bit pattern、QA_RADSAT Red/NIR/terrain bits、water negative NDVI 保留、SR_QA_AEROSOL 诊断、连续/分类 resampling 不混用。
- 两个源 grid 不同、AOI mask 空、preparation coverage 不足、priority-fill 同源 Red/NIR、common-preparation mask、scene selection tie-break deterministic、physical-band→PC-asset-key mapping。
- 未认证/跨租户访问（如果 A 暴露现有服务边界）不泄漏 AOI/asset evidence。

必须运行一次 bounded AOI-level quality probe（每期最多 3 scenes、四类核心资产、同一 target grid/QA policy），并在 Docker `agent_service` 内完成真实 relation 3077256 的 2023-07 与 2024-07 search/read、2023 QA 完整解码、source CRS/transform/scale/offset、AOI 相交和 stable provenance。probe 必须分别报告 AOI rasterized pixels、preparation-valid pixels/coverage；final NDVI-valid/common coverage 可由 B 的独立诊断计算，不得把 QA 处理移给 B。初始推荐门槛为 preparation ≥0.70/period、final NDVI ≥0.60/period、final common ≥0.50，必须由 probe 后用户冻结；probe 只用于验证/冻结覆盖门槛，不等同于完整产品运行或成功结论。若推荐门槛不成立，Implementation A 停止并报告 provisional thresholds，不能自行改阈值。

建议命令（按仓库实际环境调整，但不能凭空声称通过）：

```powershell
uv run pytest -q tests/geochange
uv run pytest -q tests/runtime tests/service/test_runtime_dispatch.py
uv run ruff check
uv run ruff format --check
uv run pyrefly check
uv lock --check
docker compose config
docker compose build agent_service
docker compose up -d postgres migrate agent_service
# 在容器内执行 A 的限定 real-provider smoke
git diff --check
```

如果依赖变更，必须报告 lockfile 和 image size；如果 PostgreSQL 受影响，运行仓库约定的 `TASKPILOT_TEST_DATABASE_URL` PostgreSQL tests。不要让完整单元套件依赖 Planetary Computer。

## 7. A 的停止条件

遇到以下任一情况立即停止，保留诊断并回报，不自行扩大范围：

1. V0.2 B 没有完成独立复核并合入用户批准的更新后 `main`，或 proposal/geography 前置缺陷仍无法安全隔离。
2. OSM ODbL 无法被产品接受且没有明确许可的替代边界。
3. 生产容器无法访问 provider，或需要不可逆架构/重大 migration。
4. 不能在 server-side 强制 host、pixel、retry、credential、array 或 temporary-disk 边界；或试图用无法保证的网络字节 cap 作为 A gate。
5. 真实 AOI 无法形成可信 coverage evidence，只能靠矩形、点 probe 或 LLM 猜测。
6. 为通过资源预算必须改变冻结产品范围。

## 8. A 最终报告（必须包含）

用中文报告：实际 branch/HEAD/status；按文件列出修改；immutable AOI snapshot 的 source/version/acquisition/hash/license；provider/asset/scene contract；两个完整 PreparedPeriodDataset 的 shape/mask/grid/provenance 示例；Docker Rasterio/GDAL 版本与 real smoke；硬限制、operational metrics 和 best-effort warnings 的实际证据；测试命令和真实结果；授权/SSRF/secret 检查；旧 fixture 是否保持；未解决风险；是否需要用户决策。

最终结果只能是：

- `IMPLEMENTATION A READY FOR STRONG REVIEW`：A gate 全部证据齐全，未声称 B 完成；或
- `BLOCKED`：列出具体证据、影响和最小决策。

不要 commit、push、PR、merge，也不要把 Strong Review 的 `APPROVED` 预写进状态。
