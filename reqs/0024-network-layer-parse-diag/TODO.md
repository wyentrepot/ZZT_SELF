# TODO — REQS-0024 网络层与组网解析基建

> 只追加不覆盖。完成一项勾选一项，并在 `DONE.md` 追加记录。
> 阶段门：每完成一个阶段停下报告，等用户显式指定下一步。
> 位域口径：以蒸馏卡 `协议层/13_双模4-2`、`16_双模4-1` 为准；与真机样本冲突时**开工实测优先**（沿 REQS-0009 先例）。

## 阶段 0 — 真机样本核验（前置门）

- [x] Step 0.1：样本集——复用 reqs/0009 三份真机抓包（2276 帧，含建网期关联/代理变更全流程），关键帧原样固化至 `libs/parser_lib/adapters/adapter_dualmac/tests/samples.py`；`verify_layouts.py` 为实证脚本存档（2026-09-05）
- [x] Step 0.2：透传性核验**通过**——四类定界符全透传（信标 209/SOF 1434/SACK 562/网间协调 71）；关联 table60/70、心跳 table94 等管理帧在样本中全量可见，G2 主通路确定为空口侧
- [x] Step 0.3：决策点三项按 REQS 变更 2 拍板执行（独立适配器/独立 events 表/fallback 保留）

## 阶段 1 — G1：4-2 链路层信封解析

- [x] Step 1.1：FCH/表13 帧控制 + 表4 MAC 头（TEIs/TEId/MSDU 序号/重启次数/路由跳数/头长 16·28）
- [x] Step 1.2：表19/30 帧长域、表23/34 选择确认、表17/27 信标可变区定位
- [x] Step 1.3：GW 封装剥离公共入口（`gw.py`，实证格式：PB 块体载荷区 + ICV=crc32(MSDU) LE + 4B 对齐填充 + 4B 网关尾）
- [x] Step 1.4：单测 23 用例（真机夹具回归 + 合成信标）全绿
- [x] Step 1.5：字段透出——经新「组网观测」页签透出（frames-pro 深解析链路仍走 DLL，未动）

## 阶段 2 — G2：NWK 管理帧解析 → 组网事件流

- [x] Step 2.1：管理帧全量表解析（table60/70/76/79/84/88/91/94/100/102/111/114/117/118/121），含 9 种关联拒绝原因码 + 退避时间、心跳位图
- [x] Step 2.2：空中格式实证锚定（mmtype 2B + 保留 2B + 表内容；表70 用 CCO MAC 位置+retryTime≈300s 工程常量交叉验证）
- [x] Step 2.3：事件落库（`nwk_events` 表，frame_id 主键幂等增量扫描）+ 查询 API（`/api/network/events|overview|beacons`）
- [ ] Step 2.4：AI v2 查询面暴露（可选，留后续——按 0021/0022 惯例加 match.kind 或轻量端点）

## 阶段 3 — G3：信标条目级解析 + 时隙重建

- [x] Step 3.1：table38 头 + 47/48/49/50/54/55/57/J1 条目逐项解析 + BPCS CRC32 校验（真机 59/59 全对）
- [x] Step 3.2：信标/TDMA/CSMA/绑定CSMA 四时段重建（含 CSMA 提前 2ms、分片×10ms；重建信标区 3348ms 与网间协调帧 duration 交叉一致）
- [x] Step 3.3：单测覆盖（含合成最小信标 + 真机中央/代理信标）
- [ ] Step 3.4：`network_assessment.py` B/C 档换结构化数据源——**有意延后**：既有路径经真机验证且可用，换源属内部重构（抗 Detail 文本漂移），建议单独 Commit 跟进；新「组网观测」已提供结构化时隙数据

## 阶段 4 — 收尾

- [x] Step 4.1：回归——parser_lib 301 passed、listener 261 passed（3 个 test_ui_layout 失败为存量，基线复现确认）、nwk_service 6 passed
- [x] Step 4.2：真机回放验收——0009 样本全量回放（2276 帧）+ 路由 TestClient 端到端冒烟；未新采真机帧（透传性已由样本证实）
- [x] Step 4.3：同步 `docs/features.md` / `docs/api-contract.md`（新增 §3.4a）；REQS-INDEX.md 状态更新；DONE.md 归档
