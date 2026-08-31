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
- [ ] P10 其余 6 个分页列表：F2/F5/F6/F7/F31/F101/F112 → G2 确认点
- [ ] P10 标量项：F1/F4/F9/F100/F40/F111；F104 标注"待补蒸馏"
- [ ] P06 主动上报页：5 个 Fn 监听解析 + 表格 + 落库 + 历史回查 → G3 确认点

## P2 其余页面批量铺开（G4）
- [ ] P00/P01/P02/P04/P05/P11/P12/P13/P14/P15/PF1 按 §3 契约铺开
- [ ] P03 查询数据页（含 F3 侦听信息分页列表、F11 位图展示）

## P3 收敛
- [ ] 真机帧回归（用 测试文件/ 样例回放）
- [ ] 更新 REQS-INDEX 登记状态

## 日志
- 2026-08-31 需求建立；现状核查：74 Fn / 38 空字段；git pull 完成（16e6756→0795773）。
- 2026-08-31 用户拍板数据分层：06H 主动上报持久入库（只追加）；下发查询做临时快照（档案会增减、在网节点会变，不做长期档案表）；单文件 sqlite（data/listener_13762.sqlite）。已写入 REQS.md §3.5。
- 2026-09-01 P0 完成：v2 契约迁移 + 校验脚本 + store/record_extractor/sink + journal 自动落库接线，全部测试通过。
- 2026-09-01 G1 完成：simcon 响应表格区 + F21 网络拓扑表格 + 分页双模式 UI + 快照/上报 store 端点；修复 normalize_afn 对 int 的 hex 语义 bug；142 tests passed。
