# REQS-0030 — DONE

> 状态：✅ P0-P4 已完成（2026-09-13）
> 真机验收：预留待办（实机并发抄表跑一轮后核对统计口径）

## 已交付

### P1 模拟集中器——下发侧深化（应用层 1376.2）
1. **帧识别**（`libs/sim_concentrator/concurrent_match.py`）：AFN=F1H/FN=F1H 多写法
   （F1/0xF1/241/F1H）判定 + 上下行数据单元解析（下行有保留字节/上行无）+
   失败判定（上行长度域 0，88 号蒸馏卡口径）。
2. **深化应用核心**（`libs/sim_concentrator/deep_app.py`）：
   - `query_archive()`：10H-F2 构帧下发查档案，`record_extractor` 契约解析记录行
     （地址逐字节反转人读、信号品质 D7~D4、中继级别 D3~D0、在网判定）；
     **模块实时获取**，`ArchiveSession` 临时存储、复位不保存，超时不用旧数据顶替；
   - `export_excel()`：openpyxl 导出（列：序号/地址/信号/中继/在网/信息hex/获取时间）；
   - `query_online()`：10H-F1 网络规模（在网 total/容量 capacity）；
   - `precheck_max_concurrent()`：1~20 前置校验，超限拒绝（CCO concurrent_tab[20]，
     否认 109 口径，不真发）。
3. **周期统计**（`libs/sim_concentrator/period_stats.py`）：时间桶聚合，period 为
   API 参数（15m/30s/1h/900，默认 15 分钟）；四指标：最大并发数（在飞峰值扫掠）、
   成功数/成功率、平均耗时（未结清不计）、重复下发（同表未结清再发）。
4. **API 接线**（`libs/sim_concentrator/api.py`）：
   `GET /api/simcon/batch/stats?period=`、`/archive/query`、`/archive`、
   `/archive/export.xlsx`、`/online`；`batch_read` 创建时接入并发数前置校验。

### P2 侦听台——侦听侧统计（被动，只收不发）
5. `apps/listener/concurrent_readonly.py`：只读收发库（frame_log AFN=F1/FN=F1），
   下行帧从内嵌 645 地址域提取表地址 → 在飞；上行帧取链路层 A1 + 长度域判成败；
   同表未结清再发 → 旧尝试按超时结清；5 分钟生命周期兜底（CCO 口径）。
   配对后与 simcon 侧共用 `period_stats` 聚合（同口径可比对）。
6. 端点 `GET /api/concurrent/stats?period=`（侦听台无业务前缀形态；
   workbench 挂载下为 `/api/listener/concurrent/stats`）。

### P3 前端 UI（simcon 页新增「深化应用」页签）
7. 向导式流程：① 查档案（表格：地址/信号品质/中继级别/在网徽章）→ 查在网（规模/容量）
   → 在网过滤下拉 → 勾选（全选/单选，已选计数）→ ② 设并发数下发并发抄表
   （自动切到并发页签看进度）→ ③ 统计（周期下拉 15m/30s/5m/1h + 四指标表格，
   10s 自动刷新）；导出 Excel 按钮（浏览器直接下载）。
   布局（2026-09-13 追加，用户定稿）：从底部结果面板页签迁出，改为**页面最左侧
   独立章节「深化应用」**（四列网格 300px 首列），内含「并发抄表」小节常驻，
   不随协议页签切换；统计 10s 自动刷新。

### P4 AI 控制面同步
8. `ai-control-plane` 技能 v2.5.0：api-contract.md 补 6 个端点契约行
   （simcon 5 + listener 1），features.md 补周期统计/深化应用两行。

## 测试与验证
- 新增单测 34 项全绿：test_concurrent_stats.py（10）+ test_deep_app.py（8）+
  test_concurrent_readonly.py（6）+ 既有相邻套件回归中随行 10 项。
- 回归：5 项失败均为存量（干净 master stash 复现一致：test_ui_layout 3 +
  test_api_cli 2）；test_ai_simcon 收集失败为环境缺 `regex` 模块（存量）。
- 测试全构帧（FakeIO + 合成帧），不依赖实机日志/串口。

## 遗留（真机待办）
- [ ] 实机跑一轮真实并发抄表，核对侦听台侧 AFN/Fn 识别与配对口径
      （当前库里 6060 帧无并发帧可对拍，编码依据 88 号蒸馏卡 + 构帧自洽）
- [ ] 实机验证档案查询（模块应答 10H-F2 分页 start/count 参数确认）
- [ ] UI 浏览器实测走通 5 步流程（本环境无浏览器会话，逻辑已单测覆盖）
- [ ] 深化应用页签在真实链路上的重复下发判定与 CCO 实况比对
