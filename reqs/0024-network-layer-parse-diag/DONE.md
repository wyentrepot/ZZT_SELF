# DONE — REQS-0024 网络层与组网解析基建

## 2026-09-05 ｜ G1-G3 全部交付（Commit `7ccbcb4` + `b21bdcf` + 收尾提交）

**交付内容**：

1. **解析库**（Commit `7ccbcb4`）：新增 `libs/parser_lib/adapters/adapter_dualmac/`——GW 侦听台封装帧剥离（gw.py）、FCH 帧控制域表13+可变区（fch.py）、MAC 帧头表4+MSDU/ICV（mac_header.py）、信标表38+条目+时隙重建（beacon.py）、NWK 管理帧全量表（mgmt.py）、组网事件归一（events.py）；23 单测全绿，parser_lib 全量 301 passed 无回归。
2. **侦听台服务与 API**（Commit `b21bdcf`）：`apps/listener/nwk_service.py` 增量扫描 frames 表→事件落 `nwk_events`（幂等）+ `nwk_scan_state` 链路质量计数；新端点 `/api/network/events|overview|beacons`；前端双副本（独立版+workbench）新增「组网观测」页签（网络卡片/信标时隙横条/事件流表格+详情）；nwk_service 6 测全绿，listener 套 261 passed。
3. **文档**：api-contract.md §3.4a、features.md 侦听台节同步；REQS-INDEX 状态更新。

**关键实证结论（开工实测优先于文档）**：

- GW 封装帧格式：`7E FF 02 | GW头20B | FCH16B | PB块体载荷区 | 4B对齐填充 | GW尾4B | 7E`；GW 硬件剥离块头/PBCS，载荷区=完整块体（TMI4→132B 等）。
- ICV=crc32(MSDU) 小端紧跟 MSDU（1066 帧通过）；信标 BPCS=crc32(载荷+填充) 位于块体尾部（59/59 全对）。
- 空中管理消息 = mmtype(2B LE) + 保留 2B(实测恒 00) + 表内容（表70 用 CCO MAC 位置 + retryTime≈300s 常量交叉锚定）。
- 信标周期实测 14878ms（表50 权威值），重建信标区 3348ms 与网间协调帧 duration 交叉一致；TEIs/TEId 两套半字节布局并存（表4 vs GET_TEI）。

**遗留（均已记 TODO，建议单独需求跟进）**：

- Step 3.4：network_assessment B/C 档换结构化数据源（既有路径可用，纯内部重构）。
- Step 2.4：AI v2 查询面暴露组网事件（可选）。
- frames-pro 深解析链路仍走 DLL，MAC/NWK 字段只在「组网观测」透出。
- test_ui_layout 3 个失败为存量（基线复现确认，与本需求无关）。
