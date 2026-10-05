# TaskPilot V0.3 Implementation Plan

状态：Planning draft，等待用户批准 baseline、AOI 许可和预算后才能开始 Implementation A。

本文件只规划 V0.3，不实施业务代码、数据库、依赖、分支或 Git 状态变更。代码标识使用英文；用户界面和规划结论使用中文。

## A. 产品目标与冻结范围

V0.3 的首个真实产品闭环是：用户请求“比较武汉市江汉区 2023 年 7 月和 2024 年 7 月的 NDVI 变化”，DeepSeek 形成受限结构化意图，服务端解析并展示受信 AOI，用户确认 AOI、指标、两个时段和数据模式后，服务端通过 Microsoft Planetary Computer 的 `landsat-c2-l2` 检索 Landsat 8/9 Collection 2 Level-2 Surface Reflectance，执行有界 COG 读取、辐射定标、QA 掩膜、统一网格、AOI 裁剪和共同有效像元比较，验证真实数值和地理成果，保存 TaskRun 与成果，并允许从历史记录重新打开同一结果。

冻结实现选择：

| 项目 | V0.3 决定 |
|---|---|
| 架构 | Hybrid；首版只做 server-controlled local processing |
| AOI | 仅武汉市江汉区，行政代码 `420103` |
| 传感器 | Landsat 8/9 |
| 数据 | Collection 2 Level-2 Surface Reflectance，30 m |
| Provider | Microsoft Planetary Computer |
| STAC collection | `landsat-c2-l2` |
| 指标 | NDVI |
| 默认时段 | `2023-07-01..2023-07-31` 与 `2024-07-01..2024-07-31` |
| LLM | 平台托管 DeepSeek；只负责意图和基于证据的文字解读 |
| 前端 | 现有 React + TypeScript + MapLibre，保留左聊天/右地图布局 |
| 后端 | 现有 FastAPI、PostgreSQL、Task/TaskRun、Agent Runtime、Skills、Verifier |
| 栅格 | 复用 NumPy；在现有 `agent_service` 镜像中固定兼容的 Rasterio/GDAL |

不在本版本实现：全国或武汉全市 AOI、GEE、多 provider、动态 NDWI/NDBI、高分一号/MODIS/HLS、SHP 上传、全国边界库、任意 URL 下载、土地覆盖分类、植被面积归因、复杂趋势、新 Agent 框架和无关旧模块重构。既有 Sentinel-2 缓存、NDWI/NDBI Skill 和历史结果必须保留。

## B. 当前仓库基线

以本地仓库为权威，不能用旧交接文档替代实际状态。

| 项目 | 实际观察 |
|---|---|
| 分支 | `codex/taskpilot-v02-b` |
| HEAD | `22b189e` (`fix: stabilize runtime history and live acceptance`) |
| `origin` | `https://github.com/2634499673lqh-gif/agent-service-toolkit.git` |
| `upstream` | `https://github.com/JoshuaC215/agent-service-toolkit.git` |
| `main` | `af23621`，当前 V0.2 B 提交不在 `main` |
| V0.2 B 远端 | `origin/codex/taskpilot-v02-b` 指向同一 `22b189e` |
| 工作树 | `process/PROGRESS_LOG.md` 有未提交修改；多份 V0.2 prompt/handoff 为未跟踪文件；存在 pytest 临时目录权限警告 |
| 预检记录 | `process/PROGRESS_LOG.md` 已追加 2026-10-04 Landsat read-only preflight |

V0.2 B 没有被证明已经合入 `main`。本规划可以在用户批准后进入**条件批准**，但 Implementation A 不能开始，直到以下 release gate 全部满足：

1. 完成 V0.2 B 的独立 Strong Review/Final Audit，并由用户将批准结果合入更新后的 `main`；推荐从该更新后的 `main` 创建 V0.3 分支。
2. 在合并前修复或明确关闭两个前置问题：新 proposal 不能立即显示“已过期”；旧“武汉东湖”结果不能被重新标成江汉区。
3. 对上述修复完成独立复核；历史结果只做 presentation 限制或不可用标记，不重写持久化证据。
4. 保留现有未提交文档和预检记录，Implementation 会话不得 reset、clean、restore、stash 或覆盖它们。

Planning 不执行合并、分支或 B 修改。若 release gate 未通过，状态是 `CONDITIONALLY APPROVED — A BLOCKED BY BASELINE GATE`，而不是允许 A 先行。

## C. 已确认的数据证据

`process/PROGRESS_LOG.md` 的预检只证明访问可行性，不证明江汉区分析完成：

- STAC HTTP 200；2023 年 7 月返回 6 个 Items，2024 年 7 月返回 4 个 Items。
- 匿名 SAS signing 通过；一次 HTTP 504 在有界重试后恢复。
- `LC08_L2SP_123039_20230727_02_T1` 和 `LC08_L2SP_123039_20240729_02_T1` 是可访问候选。
- 两期 Red/NIR 128×128 COG window 读取通过；2024 QA window 完整读取通过；2023 QA 只完成签名和 Blob Range，完整解码仍需 A 的 Docker smoke 补齐。
- 源 CRS `EPSG:32649`，分辨率 30 m，`uint16`，fill/nodata 0，scale `0.0000275`，offset `-0.2`。
- 采样点 `(114.27, 30.59)` 不是行政 AOI；候选 footprint 与该点相交不等于覆盖江汉区，也不等于云后有效覆盖。

实现不得把上述候选 scene、点 probe 或 scene-wide cloud cover 写成江汉区科学结论。A 必须用真实 AOI polygon 做覆盖和像元级质量检查。

## D. AOI 与地理真实性决定

### D.1 推荐源

V0.3 采用 OpenStreetMap relation `3077256`（当前核实 version `21`，timestamp `2024-04-22T07:21:05Z`）作为首选技术边界源，要求随成果保留 OSM attribution 和 ODbL 1.0 合规说明。A 必须把批准版本导出为仓库内的 immutable GeoJSON snapshot，并对规范化 UTF-8 字节（固定字段顺序、坐标精度和换行规则）计算 `source_sha256`；运行时只加载已固定 snapshot，不每次在线解析。snapshot metadata 必须记录：源 URL、relation id/version、acquisition timestamp、导出工具/查询、坐标参考、license 文本和 SHA-256。当前 API 元数据核实到：`boundary=administrative`、`admin_level=6`、`name=江汉区`、`name:en=Jianghan District`、`division_code=420103`；Nominatim geometry 为 WGS84 Polygon，bbox 约 `[114.2196754, 30.5661345, 114.2993827, 30.6440902]`。

候选比较：

| 来源 | 优点 | 风险/结论 |
|---|---|---|
| 政府发布行政边界 | 法律/行政权威最高 | 当前仓库没有确认可公开下载且可再分发的带许可 GeoJSON；获得明确许可前不能作为已解决输入 |
| OSM relation 3077256 | 无账号可取、含行政代码、WGS84、可固定版本、ODbL 条款明确 | 社区数据不是政府法律裁定；衍生数据库/归因义务必须履行；边界更新需要重新验收 |
| Aliyun DataV/AMap 派生数据 | 获取方便、视觉完整 | 公开再分发和商业许可不明确，不能默认合法打包进仓库 |

因此本规划选择 OSM，但把“产品是否接受 ODbL 归因/相应共享义务”列为用户审批项。若产品必须使用政府许可边界，A 在找到明确许可前停止，不得用矩形、bbox、东湖或 GCJ-02 数据冒充江汉区。

### D.2 几何契约

- canonical AOI id：`jianghan_district_420103`；中文 `武汉市江汉区`；英文 `Jianghan District, Wuhan`；行政代码 `420103`。
- 输入/显示格式：GeoJSON `Polygon` 或 `MultiPolygon`，坐标参考为真正的 `EPSG:4326`；不得把 GCJ-02 数组重标成 WGS84。
- `AOI` 需保留 `geometry`, `bbox`, `crs`, `source`, `source_version`, `source_hash`, `license`, `admin_code`, `area_m2` 等受信字段。精确几何用于裁剪和面积，允许单独生成简化显示几何并记录 tolerance/hash。
- 用 Shapely/Rasterio 等现有或新增已批准依赖执行 validity、闭环、坐标范围、反经线、面积量级检查；洞和多 multipart 不能被丢弃。建议把 snapshot 面积 sanity gate 设为 20–50 km²、bbox 落在 `[114.20, 30.54, 114.32, 30.67]`，并检查 centroid 位于江汉区预期位置；这些是防错范围，不是行政面积新权威。
- 面积 sanity check 使用投影后的等积/米制几何；超出 gate 失败关闭，不静默修复。显示 AOI、STAC 搜索 bbox、scene footprint、像元 valid mask 是四个不同对象。
- 处理 CRS 从每个 Landsat asset 的真实 metadata 推导；预检的 `EPSG:32649` 和旧 Sentinel-2 的 `EPSG:32650` 都不能写成全局常量。

### D.3 UI 边界

首次执行前显示该受信边界、名称、来源、版本和许可提示，允许缩放和检查，确认卡必须明确 AOI、指标、请求的两个时段、`REAL_STAC_LANDSAT_LOCAL` 数据模式和已知 policy；在真实数据获取前不得显示声称已验证的 scene dates 或 pixel coverage。确认必须先于 Task 创建和 provider execution。V0.3 不要求上传 SHP，也不开放任意绘制 AOI。

## E. Provider 与访问契约

Provider 固定为 `https://planetarycomputer.microsoft.com/api/stac/v1`，collection 固定为 `landsat-c2-l2`；请求只能由服务端根据已确认 AOI/时段构造。允许平台 `landsat-8`/`landsat-9`，要求 Level-2 Surface Reflectance。

### E.1 发现和选择

`discover_landsat(aoi, periods, *, collection, platforms, asset_roles, limits)` 返回每个 period 的候选 Item 摘要：stable item id、acquisition datetime、footprint、`eo:cloud_cover`、collection/platform/processing level、真实 asset keys、provider 和 coverage probe。必须使用 polygon intersection；bbox 仅用于受限搜索窗口，不能宣布完整覆盖。

`select_scenes` 以 AOI 相交面积/有效覆盖、像元级 cloud-free ratio、scene-wide cloud cover、日期接近度和稳定 item id 做确定性排序；单景足够时优先单景，必要时才允许同月且有界的最多 3 景 composite。场景选择、同月多景准备和优先级顺序全部属于 A；B 不重新选择或组合。MVP composite 优先使用 deterministic priority-fill：按 A 的排序逐景读取同一 scene 的 Red/NIR/QA，对同一像元只有在 Red/NIR 来自同一 scene 且该 scene 的质量 mask 通过时才填充尚未填充的目标像元。不得把 LLM 输出的 href、用户 URL 或重定向地址作为信任输入。

每个 NDVI period 需要通过实际 STAC Item/asset metadata 和 MTL 证据解析物理波段到 PC asset key，而不是把物理名当作字典键。固定语义映射为 `SR_B4`（物理 Red）→ `red`、`SR_B5`（物理 NIR08）→ `nir08`、`QA_PIXEL` → `qa_pixel`、`QA_RADSAT` → `qa_radsat`，以及可用时 `SR_QA_AEROSOL` → `qa_aerosol`；实际 Item 中的键和 href 必须逐项核验，不存在或不一致就失败关闭。asset key、dtype、nodata、scale、offset、CRS、transform、dimensions 都从 Item/MTL metadata 核验。稳定 item/asset identities 可以写入 provenance，SAS URL 不可以。
当前只读核验的 2023-07 Item `LC08_L2SP_123039_20230727_02_T1` 已显示 PC keys `red`, `nir08`, `qa_pixel`, `qa_radsat`, `qa_aerosol`，其 red/nir08 href basename 分别含物理 `SR_B4`/`SR_B5`；这只是 mapping evidence，不是江汉 AOI coverage 结论。A 仍需对实际选中的每个 Item 和 MTL 重复核验。

### E.1a 日期输入边界

2023-07-01..2023-07-31 与 2024-07-01..2024-07-31 是默认 period pair，不是接口唯一允许值。A 也支持同一 Jianghan AOI、同一 Landsat C2 L2/PC source、同一 NDVI indicator 的两个 bounded monthly windows：每个窗口必须是一个自然月的 `[YYYY-MM-01, next_month-01)` UTC 日期区间；两期不重叠，起止年份限定在 2023–2025，跨度各不超过 31 天，候选/实际 scene 仍受每期 20/3 上限约束。替代月份必须产生新 proposal 并在 provider 访问和 Task 创建前再次由用户明确确认；不得静默替换 July。实际 acquisition dates、scene identities、coverage 和 comparability 只在数据获取后报告。

### E.2 SAS、COG 与网络边界

- SAS 只能在 server-side 产生，短期存在于一次读取调用内；不能进入 API 响应、TaskRun、日志或持久缓存。
- 对 403/429/5xx 只做最多 3 次、指数退避的有界重试；签名过期时重新签名一次，超过预算转为可操作失败。
- Rasterio/GDAL 只能对 AOI 与 scene 相交 window 做 HTTP Range/COG 读取；禁止全景下载。
- A-owned staged output 使用有界 writer/预分配计数，累计超过 128 MiB 临时预算或每 artifact 16 MiB/六个 artifact 96 MiB 预算前即失败并删除；不声称能限制 driver 未管理的系统级 spill，若检测到非受控 spill/无法证明释放则 fail closed。SAS 和完整远端响应不写入临时目录。
- 只接受预先允许的 STAC host 和 provider blob host；校验 scheme、DNS/redirect host 和 asset identity。任何用户提供 STAC URL、LLM href、跨 host redirect 都拒绝。
- 当前 Rasterio/GDAL COG 路径不能可靠承诺一个跨 driver、redirect 和内部 Range 请求的硬网络字节上限。网络读取字节只作为 `bytes_observed` operational metric；能从所用 HTTP 层可靠观测时记录并在接近预算时告警，不能观测时明确记录 `unknown`，不得声称已强制 256/512 MiB。安全 fallback 是依靠下方可硬执行的 scene/window/pixel/concurrency/timeout/memory/temp-disk 限制，在 timeout 或其他硬预算失败关闭；不引入 proxy、transport framework 或多租户缓存来制造字节保证。
- 读取失败分类为 `retryable_provider`, `insufficient_data`, `invalid_provider_asset`, `security_rejected` 或 `terminal_processing`; 上层不循环重试。

### E.3 生产镜像

首选在现有 `agent_service` Docker image 内固定 Linux ABI 兼容的 Rasterio/GDAL/NumPy 版本，更新依赖和 lockfile 仅在用户批准并确有必要时进行。A 必须在 Docker 中完成 real bounded read；宿主临时安装 Rasterio 的预检不能作为生产能力。只有证明 image 无法安全承载且资源边界无法满足时，才提出独立 worker 选项并停下等待批准；不得为架构纯洁性新建微服务。

## F. 科学处理规则

### F.1 定标、QA 和 NDVI

对每个 asset 先按 provider metadata 验证 `uint16`、存储 valid range `1..65455` 和 fill `DN=0`，再执行 `reflectance = DN * scale + offset`；C2 L2 预期为 `scale=0.0000275`、`offset=-0.2`，但实现必须以实际 STAC/MTL 证据绑定，不能盲目硬编码。DN=0、超出存储 valid range、QA fill、非 finite 或读取损坏是 radiometry invalid。缩放后的负值或大于 1 的值默认保留并记录 `out_of_nominal_sr_range`，不能仅凭符号判 NoData；USGS 已明确水体负值和亮目标大于 1 是表面反射率算法的已知 computational artifacts。需要严格模式时只能作为明确配置，不得默默改变 MVP 结果。

QA、辐射率和 NDVI 分开处理：QA_PIXEL 的 fill/dilated cloud/high-confidence cirrus/high-confidence cloud/high-confidence cloud shadow/high-confidence snow 必须 mask；QA_RADSAT 对 Landsat 8/9 的 Red(B4) bit 3、NIR(B5) bit 4 和 terrain occlusion bit 11 必须检查；water bit 7 是诊断信息，默认不 mask。只有经过这些 mask、DN/metadata 校验和 finite 检查的 Red/NIR 才进入指数。`NDVI=(NIR-Red)/(NIR+Red)`，仅当分母 finite 且 `abs(denominator) > 1e-6` 时计算；不裁剪或替换负反射率，结果非 finite 或超出 `[-1-1e-5,1+1e-5]` 时该指数像元为 NoData 并计入原因。水和低反射率像元只要质量/辐射率有效，允许产生负 NDVI。

QA_PIXEL 必须按 Landsat Collection 2 官方 bit 定义解码并测试 fill、dilated cloud、cirrus、cloud、cloud shadow、snow；QA_RADSAT 用于饱和/缺陷排除。不能只看 Clear bit 或 scene-wide `cloud_cover`。水像元可以是有效的负 NDVI；统计只排除 no-data/云/饱和等质量无效像元，不因 NDVI 低而当成无效植被。

### F.2 对齐、composite 与统计

两期先把有效 Red/NIR 和质量 mask 重投影到由真实 source CRS/transform/resolution 推导的共同 target grid，再生成同一 AOI raster mask。连续反射率与分类 QA 使用分别适合的 resampling（如连续值 bilinear、QA nearest），不得直接相减两个未知网格数组。重投影只改变网格，不创造新观测。

同月 composite 由 A 完成，MVP 优先使用 deterministic priority-fill 而不是 B 侧 median：A 保存每个目标像元的 `source_scene_index`，并保证同一像元的 Red/NIR 来自同一源 scene。若任一期 preparation 或 common-preparation coverage 不足，A 返回 `insufficient_preparation_coverage` 或 `no_common_preparation_pixels`，不自动改月；B 的 final NDVI/common comparison coverage 不足分别返回 `insufficient_ndvi_coverage` 或 `insufficient_comparison_coverage`。需要新时段时重新生成 proposal 并要求用户明确确认。

至少保存/显示：实际 acquisition date、scene count、AOI administrative area、各期 preparation-valid pixels、各期 final NDVI-valid pixels、final common-comparison pixels、各期 preparation/final coverage%、common-comparison coverage%、共同像元上的 mean NDVI A/B/delta，以及在不误导的前提下的 min/max。必须区分 AOI rasterized pixels、preparation-valid pixels、final NDVI-valid pixels、common comparison-valid pixels、行政面积和有效观测面积；本版本不输出“植被变化面积”或因果归因。

覆盖门槛（待 bounded AOI quality probe 后由用户冻结）：coverage denominator 是 pair-wide target grid 中 `aoi_mask == true` 的 rasterized AOI pixel count，而不是 scene footprint、bbox 或全国面积。推荐初始门槛为每期 preparation-valid coverage ≥ 0.70、每期 final NDVI-valid coverage ≥ 0.60、final common-comparison coverage ≥ 0.50；任一不满足即返回 `insufficient_preparation_coverage`（A）或 `insufficient_ndvi_coverage`/`insufficient_comparison_coverage`（B），不得换月或输出成功统计。这里的百分比只能由相应 mask 计数得到，真实面积需使用 AOI 投影几何/像元面积 evidence。现有 preflight 只有 bbox/点和 window 读取，没有真实 AOI 面积或 July pixel-quality 证据，因此这些值在 probe 前明确是 provisional；若 probe 不能支持它们，A/B 必须先提交修改后的明确门槛供用户批准，实现者不得自行猜测。

### F.3 推荐但待用户批准的初始预算

这些数值是工程安全上限，不是科学真理；用户批准后才冻结到 A/B 接口。

| 资源 | 推荐上限 | 理由/失败行为 |
|---|---:|---|
| 支持 AOI | 仅 `jianghan_district_420103` | 防止全国/任意几何扩张；其他 AOI 返回 unsupported |
| 每期候选 Item | 20 | 2023/2024 预检结果远低于此；超出截断并记录 |
| 每期实际 scene | 3 | 单景优先；避免无界 mosaic |
| 每期目标像元 | 500,000 | 江汉区 30 m 面积级别加余量；超出返回 budget_exceeded |
| 单次 window | 262,144 pixels | 限制单次内存和 Range 请求 |
| 单期网络读取 | 不声明硬上限；记录 `bytes_observed`，256/512 MiB 作为告警阈值 | Rasterio/GDAL 内部 Range 行为无法跨 driver 硬保证；超阈值记录 warning，依靠其他硬预算安全失败 |
| 并发 provider 请求 | 2（硬） | server-side semaphore；其余排队 |
| 数组与 GDAL cache | 128 MiB（硬；建议数组 64 MiB + GDAL cache 64 MiB） | 在分配前按 shape/dtype 估算；超出直接 `resource_exhausted` |
| 处理内存 | 数组分配和 GDAL cache 128 MiB 为硬；过程 RSS 是 measured metric，512 MiB 是 warning/可监测时的 fail-closed 阈值 | 无法可靠监测 RSS 时不得声称硬上限；不引入 proxy/worker 仅为伪造 RSS 或字节保证 |
| 处理超时 | 180 s（硬 request/task deadline；Docker smoke 可放宽至 300 s） | `wait_for` 只结束等待，不证明阻塞 GDAL 已停止；A 必须在受监督的独立 child process 中执行 blocking GDAL/Rasterio read，deadline 后 terminate→bounded join→kill→join，关闭 pipes、删除 staged temp files，并以 child exit/cleanup evidence 才释放 run。无法证明时报告 timeout/cancelled，不写成功结果 |
| 每个远端操作重试 | 3 次 | 仅指定 403/429/5xx/timeout，禁止无限重试 |
| 临时磁盘 | A-owned staged bytes 每 run 128 MiB（硬，写入前计数/预分配，结束即删除）；driver 未管理 spill 不作虚假硬保证，无法证明时 fail closed | 不创建 1 GiB/tenant 的多租户 provider cache；不缓存 SAS |
| 数值 GeoTIFF/valid mask | 每个 16 MiB、六个 V0.3 数值/mask artifacts 整 run 96 MiB（硬） | 与 3 个 NDVI rasters + 3 个 validity masks 的 allowlist 对齐；数字栅格为权威，PNG/tiles 为派生展示 |
| PNG 展示文件 | 沿用现有 2 MiB/文件约束 | 不能替代 GeoTIFF 或 provenance |

预算分类必须写入运行 evidence：`hard_limits_applied`（scene/window/pixel/concurrency/timeout/array/GDAL-cache/temp-disk）、`operational_metrics`（实际读取字节、RSS、耗时）和 `best_effort_warnings`（无法观测的网络行为、provider throttle、无法可靠监测的 RSS）。不得把 measured bytes、RSS 或 warning 写成 hard cap 已执行。

## G. Runtime/Skill 集成

保留 `src/geochange/skill.py` 的 typed capability 和确定性 Python 计算；LLM 不生成数学结果。保留 `src/geochange/fixture.py`、现有 Sentinel-2 manifest/hash 及 NDWI/NDBI 路径，动态 Landsat 使用独立 execution mode 和 provenance，不套用旧 fixture hash。

Implementation A 发布以下供 B 使用的唯一稳定 preparation boundary（名称可在实现时采用等价 typed 名称，但字段语义不得改变）。A 负责到两个 period dataset 完全准备且已对齐为止；B 不得重新选择 scene、解 QA、composite 或 reprojection。A 内部可以拆分 discovery/selection/read 函数，但这些不是 B 的数据入口：

```text
resolve_aoi("jianghan_district_420103") -> TrustedAOI
prepare_landsat_periods(TrustedAOI, PeriodPair, PreparationLimits) -> PreparedPeriodPair | PreparationFailure
```

精确 handoff contract：

```text
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

所有 arrays 必须同 shape、同 target grid、同 AOI mask；`red_reflectance`/`nir_reflectance` 为已定标 float32，不能返回 raw DN、未对齐 window 或 SAS URL。`aoi_mask` 是 pair-wide target grid 的 rasterized AOI；`preparation_valid_mask` 只表示 QA/辐射率有效；`source_scene_index` 为 int16，-1 表示无 priority-fill source。A 的 priority-fill 必须保证每个非 -1 `source_scene_index` 对应的 Red/NIR 来自同一 scene。A 只生成 `preparation_valid_mask` 和 `common_preparation_valid_mask`；B 必须从两期数组在相同 pair grid 上独立执行 finite/denominator/range 检查后生成 `final_ndvi_valid_mask_a/b`，并生成唯一的 `final_common_comparison_mask = common_preparation_valid_mask & final_ndvi_valid_mask_a & final_ndvi_valid_mask_b`。所有 scene/asset 列表最多 3 项/period，metadata 字符串、warning 和 diagnostics 有界且去敏。A 失败时不返回部分可执行 dataset，返回有界 `PreparationFailure {code, stage, retryable, counts, sanitized_diagnostics}`（`unsupported_aoi`, `no_candidate`, `missing_asset`, `provider_unavailable`, `security_rejected`, `read_budget_exceeded`, `invalid_raster_metadata`, `invalid_radiometry`, `insufficient_preparation_coverage`, `grid_alignment_failed`, `no_common_preparation_pixels`, `timeout`, `cancelled`），并保证没有成功 artifact。

ConfirmedIntent 扩展为江汉/NDVI/Landsat 的显式组合，并在 server-side 比较已持久化 intent、proposal 和运行输入。建议新的 `data_mode` 为 `real_stac_landsat_local`，旧 `local_real_raster_fixture` 等模式继续兼容。确认前不创建 Task；已提交 proposal 不能重复创建；不能只靠 React object identity 判断有效期。

Verifier 以服务端的 AOI hash、scene identities、target grid、mask、metrics、artifact hashes 为证据，检查数值、coverage、共同像元和 artifact 完整性。DeepSeek 解释只能消费已验证结果 projection；不接受其声称的 scene/date/coverage 作为证据。

## H. 成果持久化与地图

权威成果是带 CRS/transform/bounds/nodata 的数值栅格：NDVI period A、period B、delta，以及 final NDVI-valid/common-comparison masks；PNG/MapLibre source 是派生展示。B 只在现有 tenant-authorized artifact contract 上增加明确 allowlist：`ndvi_before`, `ndvi_after`, `ndvi_change` 为 numeric GeoTIFF（`image/tiff`，`.tif`，每个 ≤16 MiB），`ndvi_valid_before`, `ndvi_valid_after`, `ndvi_common_comparison` 为 uint8 GeoTIFF masks（`image/tiff`，`.tif`，每个 ≤16 MiB）；既有九个 legacy PNG names/URLs（`image/png`，`.png`，≤2 MiB）和历史 Sentinel-2 结果不变。服务端固定 artifact name→extension/MIME/type registry，拒绝 caller-supplied path/extension；每个 reference 绑定 tenant/task/run、SHA-256、AOI/period/grid/provenance hash，写入后重新以原始 numeric dtype/CRS/transform 打开校验，容器重建后必须从现有 artifact mount 恢复。尽量复用现有 TaskRun `result_metadata` 和 artifact root，避免新增表；若实现需要不可逆或重大 schema migration，停止并请求批准。

metadata 至少包含 AOI id/hash/source/version、source provider、collection、每期 item/date/asset identity、target grid、scene count、valid/common metrics、artifact names/checksums、execution mode 和 scientific limits；不得包含 SAS、原始凭证或完整敏感 provider payload。容器重建不得删除持久成果。

MapLibre 只从 server-authorized metadata 添加图层：AOI boundary、真实 imagery footprint、NDVI A/B/delta，支持显隐/切换、透明 NoData、科学 legend、日期、coverage、来源和限制。使用真实 geographic footprint 转换，不通过视觉平移修正 CRS。历史打开沿用原 Run 的 artifacts/provenance，不静默重算或换 scene。

旧“武汉东湖”结果：保留原 immutable task/run、scene、fixture 和坐标；在 UI/API 明确标为历史 Sentinel-2 East Lake fixture/地理范围待复核或不可用于江汉比较。不得移动 raster、改写历史 evidence、改名为 Jianghan，或把旧 bbox 作为新 AOI。

## I. 安全与失败行为

所有 AOI、period、indicator、data mode、asset host、artifact reference、tenant 和 authorization 检查在 server-side 完成。确认前只显示受信 AOI/source policy、请求的 period 和已知的默认/约束信息，不显示尚未获取的 scene dates 或 pixel coverage；confirmation 必须先于 Task 创建和 provider execution。执行后才显示 verified scene dates、coverage、provenance。Backend Run 的 `succeeded`/`verifier_passed` 与 frontend map `rendered` 是独立状态；map source/load/render failure 只能标记 map error，不能把已经 verified 的 Run 改写为 failed。401/403/404 继续遵循现有认证/租户约定。外部数据错误不可伪造成成功；在 verifier 通过前 UI 不得显示“分析完成”。

必须定义并测试：unsupported AOI、invalid geometry/date、wrong collection/asset、missing Red/NIR/QA、unexpected host/redirect、SAS expiry、STAC 429/5xx、corrupt COG/CRS/grid、fill-only/cloudy/incomplete coverage、duplicate confirmation、unauthorized task/artifact、missing historical artifact 和 provider outage。失败应给出可操作的中文提示和受限内部 error class，不暴露签名 URL、凭证或完整远端响应。

## J. A/B 模块所有权

| 领域 | A：Trusted AOI & Data Preparation | B：Dynamic NDVI & Product Integration |
|---|---|---|
| AOI | OSM 版本化 polygon、校验、provenance、显示/精确几何 | 只消费 A 的 `TrustedAOI`，接入确认和地图 |
| Provider | STAC 查询、asset 解析、SAS、bounded COG、场景选择和同月多景准备 | 不重建 provider；调用 A 接口 |
| 科学基础 | 定标、QA bit、radiometry/valid masks、priority-fill、common grid、AOI mask、coverage 和 provenance | 只消费两个 PreparedPeriodDataset，计算 NDVI/delta/metrics |
| Docker | Rasterio/GDAL 能力与 real smoke | 使用 A 的镜像能力 |
| Runtime | 发布 typed interface，不接完整 Agent workflow | Skill/TaskRuntime/Verifier/proposal/lifecycle |
| 成果 | 输入/中间 evidence contract | GeoTIFF/PNG/metadata 持久化、API、MapLibre、history |
| LLM/UI | 不改前端和完整对话 | DeepSeek proposal/interpretation 与现有 React 集成 |
| 旧路径 | 不改写 fixture | NDVI/NDWI/NDBI legacy path 回归，禁止数据串线 |

## K. 验收矩阵

| 验收项 | A gate | B gate / 证据 |
|---|---|---|
| AOI | relation 3077256 或批准替代源，合法 GeoJSON，hash/license/area 检查 | UI 显示并要求明确确认；运行 intent 精确匹配 |
| 数据发现 | 默认 July 或已确认 alternative monthly pair 的真实 item/asset/日期/footprint/candidate report | TaskRun 记录最终 scene identities，不接受 LLM href |
| QA/读取 | Docker 内两期 Red/NIR/QA bounded read，2023 QA 解码补齐 | 真实执行使用同一 pipeline；异常可分类失败 |
| 科学 | scale、fill、QA bits、负水体/亮目标、radiometry flags、priority-fill、common grid/mask 的 deterministic tests；两期 dataset 完全准备好 | B 不重做 scene/QA/composite/reprojection；仅用 handoff dataset 计算 NDVI A/B/delta、共同有效 coverage 与 metrics，并由 verifier 通过 |
| 安全 | host allowlist、SAS 不落盘、预算和取消/重试 tests | tenant/artifact/confirmed-intent/duplicate-confirmation regression |
| artifacts | A 发布 metadata/evidence contract | 数值 GeoTIFF + valid mask + 派生 PNG，重建后仍可读 |
| 地图 | A 提供真实 bounds/CRS evidence | AOI/footprint/A/B/delta 对齐，NoData 透明，legend/date/coverage 正确 |
| AI | 不作为 A 的科学证据 | DeepSeek 只解释 verifier evidence；无 key/timeout 清楚失败 |
| 历史 | A 不接触旧 fixture | 历史真实 Run 原 provenance 重开，不换 scene，不标为 Jianghan |
| 端到端 | limited real-provider smoke | 一个授权用户完成确认→真实 Landsat→verifier→地图→解读→历史重开 |

若 July 数据达不到 preparation、final NDVI 或 final common-comparison 门槛，正确结果分别是 `insufficient_preparation_coverage`、`insufficient_ndvi_coverage` 或 `insufficient_comparison_coverage`，不是静默换月。要做成功演示，B 必须先展示候选替代时段和已获取证据，再让用户重新确认；替代时段不是默认值，也不能伪称 July 已完成。

## L. 风险、阻塞与解决方案

1. **Baseline 未批准或 B 前置问题未闭合。** 这是 A 的 release gate；先完成 V0.2 B 最终复核并合入更新后的 approved main，再创建 V0.3 分支。Planning 可以条件批准，但 A 仍保持 blocked。
2. **OSM 许可不适合产品发布。** 当前可用方案是保留 ODbL attribution/相应共享义务；若产品不能接受，停止 A，换取明确许可的政府边界。不能使用 Aliyun 作为默认替代。
3. **真实江汉区 July 云/覆盖不足。** 保留对应的 preparation/NDVI/comparison insufficient failure；由用户确认新的 period pair 后重新 proposal。禁止静默扩展时段。
4. **Docker 无 Rasterio/GDAL。** A 先在现有 image 固定兼容依赖并验证；若 ABI 或镜像风险不可接受，提出 worker 方案，停在用户批准处。
5. **AOI 仍可能被误当作 bbox/scene footprint。** Verifier 同时绑定 AOI hash、pixel mask 和 source footprint；缺任一证据则失败。
6. **旧 artifact 生命周期不完整。** 使用现有受控 artifact mount/tenant endpoint，缺失历史文件明确不可用，不伪造恢复。
7. **数据源和匿名 SAS 限流。** 2 并发、3 重试、读取字节预算和可重试/终止分类；预算耗尽转可操作失败。
8. **需要重大数据库改造。** 先用现有 TaskRun JSON 和 artifact store；若无法表达必要证据，停止规划/实现并单独批准 migration。

## M. 需要用户批准的决策

1. 是否先完成 V0.2 B 的最终复核并合入更新后的 `main`，再从该 main 创建 V0.3 分支；在此之前 A 不启动。
2. 是否接受 OSM relation `3077256` 的 immutable snapshot 和 ODbL 合规路径（含 attribution 和衍生数据库义务）；若要 broader/commercial distribution，是否完成单独 license review。
3. bounded AOI quality probe 后，是否冻结推荐的 preparation ≥0.70、final NDVI ≥0.60、final common ≥0.50 覆盖门槛，或批准 probe 支持的替代数值；probe 前不得把 provisional 值当成科学批准。
4. 是否批准表 F.3 的硬限制、operational metrics 和 best-effort warnings 分类；这些是可调整的工程 budget，不是科学阈值。
5. 是否批准优先沿用 `result_metadata` + 受控 artifact store，只有证据表明不足时才引入 migration。
6. 当 July 质量不足时，是否允许在用户重新确认的前提下使用 A 发现的替代月份/日期作为单独演示；不得预先指定或静默替换。

外部依据：OSM relation [3077256](https://www.openstreetmap.org/relation/3077256)、[OSM copyright/ODbL](https://www.openstreetmap.org/copyright)、USGS [Landsat Collection 2 Level-2 products](https://www.usgs.gov/landsat-missions/landsat-collection-2-level-2-science-products)、[negative surface reflectance FAQ](https://www.usgs.gov/faqs/why-are-negative-values-observed-over-water-some-landsat-surface-reflectance-products)、[QA bands](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)、Planetary Computer 的 [STAC 文档](https://planetarycomputer.microsoft.com/docs/reference/stac/) 与 [数据访问说明](https://planetarycomputer.microsoft.com/docs/concepts/sas/)。

## Planning status

PLANNING READY FOR CONDITIONAL USER APPROVAL
