# Implementation A 浏览器验收（blocker 修复后）

启动后端与前端：

```powershell
docker compose up -d postgres migrate agent_service web
```

浏览器访问 **http://localhost:5173/**。使用已由仓库 bootstrap 流程创建的 TaskPilot 账号登录；密码和会话令牌不写入 URL、localStorage 或 sessionStorage。

1. 打开首页，输入账号密码登录；预期进入左右分栏工作台，不出现访问令牌输入框。刷新页面后回到登录页。
2. 登录后确认顶部“分析工作台”“我的分析”“使用帮助”均为可聚焦按钮；切换“我的分析”看到真实历史任务，切换“使用帮助”看到操作和科学限制；右侧同时显示可缩放、定位的 MapLibre OpenStreetMap 底图、武汉东湖 AOI 边界和 attribution。
3. 点击 NDVI/NDWI/NDBI 示例问题，或输入自然语言请求；预期左侧出现用户消息、助手回复和中文分析方案卡。缺少时段时补充日期，点击“确认方案并开始分析”前不得创建 Run。
4. 确认方案后等待武汉东湖缓存任务完成；预期地图加载受 manifest 验证的 before/after/change 图层，结果卡显示指标、时段、数据来源和科学限制；地图图层请求应携带当前登录会话，再从“我的分析”重新打开成功结果。
5. 点击“退出”预期返回登录页；使用错误账号或失效会话、以及底图服务失败时，预期显示明确中文错误，不显示技术堆栈。

本地实际地址：`http://localhost:5173/`。若 Docker 或后端数据库未运行，按启动命令先启动；不要把失败误认为前端成功。
