# REQS-0031 · 全工作台软件化统一壳（v4 方案落地）

> 状态：🚧 进行中 ｜ 分支：`req/0031-workbench-suite-shell` ｜ 建立日期：2026-09-13
> 上游：REQS-0010（工作台 UI 落地，P5 主题统一挂起）、REQS-0011（94 项功能基线）
> 配套方案：`ui-demo/全工作台统一壳-设计思路-v4.md`、`ui-demo/workbench-suite-demo-v4.html`、`ui-demo/simcon-redesign-设计思路.md`、`ui-demo/simcon-ui-demo-v3.html`

## 基线（当前生效版本 v1）

用户对工作台整体提出软件化诉求（2026-09-13）：

1. 模拟集中器等页面"全塞一页、没有收缩、不像软件"，需要按功能划分、可收缩侧栏、菜单栏，像软件界面一样简洁明了；
2. 软件化不只针对模拟集中器——侦听台、模块日志等全部页面都要按同一设计原则处理；
3. 搭配方式采纳 AI 推荐结论：**骨架上升为全局壳，模块变成壳内工作区**，反对每页各套壳（双左栏）；
4. 落地策略：**分两步**。第一步=壳骨架（菜单栏/侧栏升级/全局 Dock/状态栏）+ 模拟集中器四页签改造；第二步（另行排期）=侦听台与模块日志平移；
5. 全部工作在独立分支进行，不干扰 master（master 正在处理缺陷），完成后等用户指令合并。

### 功能基线与红线

- 功能基线：`ui-demo/功能清单.md`（REQS-0011，外壳 9 项 + 9 页面共 94 项），禁止丢功能；
- 壳机制红线（保持不变）：iframe 首次访问才创建、切页仅隐藏/显示（`framesByPage`/`ensureFrame`，`frame.src` 只赋一次）；hash 路由；主题广播 `wb-theme-change`；窄屏抽屉（Escape/遮罩/焦点回归）；侧栏折叠态存 localStorage；
- `apps/workbench/test_shell_navigation.py` 的 7 项契约测试必须保持绿；
- 设计语言：沿用 tokens-v2.css（墨夜/晴昼双主题、蔚蓝主色 #06b6d4），禁止新造 token 方言。

### 第一步范围（本需求）

| 项 | 内容 |
|---|---|
| P0 | 方案与 Demo 入库（v3/v4 设计文档 + 两个交互 demo） |
| P1 | 壳骨架软件化：菜单栏（会话/数据/视图/帮助）、侧栏视觉升级（图标+计数）、全局 Dock（帧日志/日志流/事件，postMessage 接收）、底部状态栏；iframe 保活/hash/主题广播/抽屉机制不动 |
| P2 | 模拟集中器页接入：去掉页内面包屑顶栏、四列+三横条改为四页签（构帧下发/并发抄表/数据观测/会话设置），元素 id 全保留使 simcon.js 零改动或最小改动；TX/RX 帧推送全局 Dock |

### 第二步范围（原另行排期，变更 2 起纳入本需求）

侦听台（29 项）与模块日志（21 项）页内头部改二级页签条、接入全局 Dock；轻模块（追踪/字典/场景）深度换肤仍留后续。
注：`apps/listener` 与 `apps/module_log` 为**独立服务版双副本**（功能同源、接口前缀不同），不含统一壳，本次不改版；其 test_ui_layout 3 项失败为存量（与本需求无关）。

## 变更记录

### 变更 2 ｜ 2026-09-13 ｜ 用户指令
- **改成什么**: 第二步（侦听台 29 项、模块日志 21 项平移进壳）从"另行排期"改为**纳入本需求完成**。
- **为什么**: 用户在 P0-P2 完成后明确指示"第二步也要完成"。
- **影响**: apps/workbench/static/pages/listener/{index.html,styles.css,app.js}、apps/workbench/static/pages/module-serial/{module-serial.html,styles.css,module-serial.js}；仍在本分支。
- **被取代**: 变更 1 中"第二步范围（另行排期，不在本需求）"的排期口径。

### 变更 1 ｜ 2026-09-13 ｜ AI（王瑜 授权）
- **改成什么**: 新建需求，基线 v1 如上（软件化统一壳第一步：P0 方案入库 + P1 壳骨架 + P2 simcon 四页签）。
- **为什么**: 用户对现有页面布局提出软件化重设计诉求，并明确"去工作分支处理、不干扰 master、完成后等指令合并"。
- **影响**: apps/workbench/static/{index.html,styles.css,app.js}、apps/workbench/static/pages/simcon/{simcon.html,simcon.js}；新分支 req/0031-workbench-suite-shell。
- **被取代**: 无（新建）。
