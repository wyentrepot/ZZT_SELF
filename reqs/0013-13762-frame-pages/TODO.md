# REQS-0013 TODO

> 变更记录只追加不覆盖。

## P0 库扩展契约（先行）
- [x] afn_fn.json 增加 `req`/`resp`/`list`/`pageMode`/`persist` v2 契约（兼容旧 `fields`）→ scripts/migrate_afn_fn_v2.py（74 req 化 + 21 resp 注入）
- [x] 以 10H F2 为样板补全上行响应建模，写库校验脚本（对照 03 蒸馏文档逐字段核对）→ scripts/check_afn_fn_v2.py（9/9 分页 + 06H persist + 字节抽查全绿）
- [x] sqlite 建库：data/listener_13762.sqlite（持久层 report_event / report_meter_data / frame_log + 临时层 query_snapshot / query_snapshot_item）→ libs/sim_concentrator/store.py
- [x] 记录提取器（契约驱动）→ libs/sim_concentrator/record_extractor.py + sink.py；journal.append 自动落库；测试 16 passed + 06H-F1 集成验证通过

## P1 核心页面（G1–G3）
- [x] P10 路由查询页骨架：AFN 导航 + 分页表格组件（指定范围 / 自动全量双模式）+ 快照读写 → simcon.html/js 响应表格区（resp-grid）+ /api/simcon/store/snapshots|events 端点
- [x] P10 F21 网络拓扑列表打通（截图形态复现，结果写临时快照）→ G1 确认点（表格列：地址/TEI/代理/层级/角色，无信道类型列；端到端 enrich_response 验证通过）
- [x] P10 其余 6 个分页列表：F2/F5/F6/F7/F31/F101/F112 → G2（test_p10_resp.py 13/13，真实帧回放 15/15）
- [x] P10 标量项：F1/F4/F9/F100/F40/F111；F104 标注"待补蒸馏"
- [x] P06 主动上报页：5 个 Fn 监听解析 + 表格 + 落库 + 历史回查 → G3（sink 06H 落库验证 F2→meter_data/F1F3F4F5→event；UI 上报历史面板）

## P2 其余页面批量铺开（G4）
- [x] P03 查询数据页（含 F3 侦听信息分页列表、F11 位图展示）→ 03H 补 13 个标量 Fn resp 建模（F1/F2/F4/F5/F6/F7/F8/F9/F10/F11/F12/F16/F100）
- [x] P00/P01/P02/P04/P05/P11/P12/P13/P14/P15/PF1 按 §3 契约铺开（这些页 req 字段已在字典中，UI 通用渲染，标量/命令类无分页）

## P3 收敛
- [x] 真机帧回归：测试文件/构帧全量清单_TX_RX_20260829.txt 回放 → 03H 14/14、06H 7/7、10H 15/15 全部解析成功（scripts/replay_frames_check.py）
- [x] 更新 REQS-INDEX 登记状态

## 日志
- 2026-08-31 需求建立；现状核查：74 Fn / 38 空字段；git pull 完成（16e6756→0795773）。
- 2026-08-31 用户拍板数据分层：06H 主动上报持久入库（只追加）；下发查询做临时快照（档案会增减、在网节点会变，不做长期档案表）；单文件 sqlite（data/listener_13762.sqlite）。已写入 REQS.md §3.5。
- 2026-09-01 P0 完成：v2 契约迁移 + 校验脚本 + store/record_extractor/sink + journal 自动落库接线，全部测试通过。
- 2026-09-01 G1 完成：simcon 响应表格区 + F21 网络拓扑表格 + 分页双模式 UI + 快照/上报 store 端点；修复 normalize_afn 对 int 的 hex 语义 bug；142 tests passed。
- 2026-09-01 G2/G3/G4/P3 完成：34 Fn resp 建模；P10 全 Fn 契约测试 13/13；06H 上报落库+历史回查 UI；03H 标量建模；真实帧回放 36/36（03H 14 + 06H 7 + 10H 15）；195 passed。
- 2026-09-01 上报数据保留策略落地：06H 上报按天保存（day 列），滚动保留最近 5 天，第 6 天写入自动清第 1 天；`_REPORT_RETAIN_DAYS=5`；`_prune_reports()` 写入时触发；验证 6 天数据→清最旧 1 天、留 5 天。
- 2026-09-01 frame_log 也纳入 5 天滚动：三表（frame_log/report_event/report_meter_data）统一按 day 滚动保留 5 天，add_frame 也触发清理；验证三表同步清最旧一天、各留 5 天。
