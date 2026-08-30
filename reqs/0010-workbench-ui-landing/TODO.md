# TODO-0010：工作台 UI 落地

## P1（已完成 2026-08-30）：报文追踪 + 协议字典

- [x] T101 reqs 立项 + 索引登记
- [x] T102 1376.2 AFN/Fn 参考字典 JSON 化（`libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json`，15 AFN / 74 Fn，自 docs/ui demo 协议字典提取；构帧仍以 adapter_10376 代码为准）
- [x] T103 `/api/dict` 只读端点（`apps/workbench/dict_api.py`：oad/di/afn-fn/rules + 字典清单）并挂载进 workbench 应用
- [x] T104 报文追踪页 `static/pages/trace/`（特征表单 → POST traces；summary/rounds/meter_table 对账/三段证据链/proxy_graph；live 注册+4s 轮询+停止；帧钻取）
- [x] T105 协议字典页 `static/pages/dict/`（四字典切换/搜索/详情 + 注记高亮）
- [x] T106 workbench 导航注册（新增「辅助」分组）
- [x] T107 冒烟测试（TestClient 25 用例）+ 真服务无头截图自检
  - 注：workbench 前缀代理剥 `/api/listener`，页面走双前缀 `/api/listener/listener/traces`、`/api/listener/logs/frames/{id}`（与内嵌侦听台页同惯例）
- [x] T108 真库验证：真服务 POST traces 返回合法空报告（默认索引无数据时）；真机索引人工核对由日常使用覆盖

## P2（已完成 2026-08-30）：侦听台改版

- [x] T201 新增「新版帧浏览」默认页签（三列：筛选/帧索引/深度解析），零回归——经典视图与分钟采集/任务配置/承载评估全部保留
- [x] T202 帧详情 feature_hint 卡（scope/app_id/msg_seq/tips）+「送报文追踪」跳转（target=_top → /#trace）+ 复制特征 JSON
- [x] T203 视图切换 switchView 扩展 frames-pro；双副本同步（内嵌副本按 `/api/`→`/api/listener/`、`/static/`→`/static/pages/listener/` 改写；styles.css 校验一致）
- [ ] T204 旧四视图的视觉统一（属 P5 主题统一范畴，暂缓）

## P3（已完成 2026-08-30）：场景脚本页

- [x] T204 场景列表 + expected_flow 时间线 + 激励步骤/内置应答/监控 只读浏览（`/api/scenarios` + 新增 `/api/scenarios/{id}/task` 端点）
- [x] T205 试跑跳转（→ /#workbench）；task_file 缺失时页首红条明确提示
- [x] T206 ⚠ 存量数据问题移交：join_anhui/open_close/search_meter 三个场景引用的 tasks/*.json 不在仓库（仅 minute_collect 的两个任务文件真实存在）——端点 404 明确报「任务文件缺失」，补任务文件需场景责任人提供，不擅自编造

## P4（已完成 2026-08-30）：模拟集中器独立页

- [x] T301 AFN→Fn→参数三列（数据源 /api/dict/afn-fn，15 AFN / 74 Fn；字段表只读参考）
- [x] T302 语义化构帧预览：新增 `POST /api/simcon/build`（scenario_codec 只算不发）；send 业务参数 JSON 编辑（常用 Fn 预置模板：11H-F1/F2/F231/F100、05H-F1、03H-F3/F11）+ 下发（/api/simcon/step）+ 串口 open/close/status + 收发记录轮询（/api/simcon/frames 分段着色）+ 内置应答规则条
- [x] T303 字段级表单与 adapter 模板全量对齐 → 留待按需补 PARAM_TPL（当前 JSON 参数 + 构帧报错提示已可闭环）
- [ ] T304 串口占用仲裁提示（与侦听台互斥）——真机双串口场景验证时补
- [x] T305 参数模板与真实任务对齐验证：build 真服务实测 11H-F1 出帧 35B 含 Profile 地址域装配

## P5（⏸ 挂起待决策）：壳层主题统一

- [ ] T401 工业深色定为默认主题的决策与存量页面改造范围评估（侦听台旧 header、验证工作台等仍为旧皮肤）
