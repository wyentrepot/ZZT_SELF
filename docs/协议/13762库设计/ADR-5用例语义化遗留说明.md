# 用例语义化改造（ADR-5）遗留说明与明日待办

> 2026-08-28 完成，供用户 pull 后查阅。配套 ADR-5（DECISIONS.md）、
> 契约文档 `docs/协议/13762库设计/用例语义化与Profile契约.md`。

## 一、本次改动摘要

- **用例语义化**：task step 的 `send` 不再写 raw/手写 hex，只写 `afn/fn + params`；
  全局信息（cco_addr、sta 档案、comm_mode）进 `profiles/anhui.json` 共享。
- **13762 库补构建侧**：`adapter_10376` 新增 `encode_app_data(afn, fn, params)`，
  未覆盖 Fn 抛 `UnsupportedFn` 明确报错（替代 raw 兜底）。
- **转换层**：`libs/sim_concentrator/scenario_codec.py` 的 `build_send` 把
  `send + profile` → `build_13762_frame`；runner 已接入。
- **迁移**：`anhui_698_meter_collect.json`、`anhui_minute_collect.json` 已迁移。
- 全量回归：**192 passed**（sim_concentrator + adapter_10376 + workbench 编排）。

## 二、需要你确认/关注的点（未决项）

1. **698 档案地址统一为 `080000000000`**：旧验证帧实际字节 `08 00 00 00 00 00`
   （BCD 正序 = 080000000000），你原用例名写的是 `080000000008`。已按验证帧统一，
   但若 `080000000008` 才是真实电表地址，需真机复验后改回 task + profile。

2. **F232 新帧带地址域（module_id=1）**：按你定稿，构帧现带地址域
   （A1=cco_addr、A3=sta_addr）。**旧验证帧无地址域**，帧结构变了，需 CCO 端
   确认是否接受新帧（真机验证范畴，代码层无法自证）。

3. **未覆盖 Fn 明确报错**：如 `05H-F10 设双模串口速率` 等暂未建模板，用例若用到会
   报 `UnsupportedFn`。按你要求"下次再加"，需要时补 `_encode_app_data` 分支即可。

4. **`analyze_oad_coverage.py` 与 `docs/_archive/2026-08-28/`**：这两个是会话前
   你工作区已有的改动/归档（698 OAD 相关），**未纳入本次提交**，保持未跟踪/未暂存，
   避免混入本次任务。如需提交请单独处理。

## 三、技术备忘

- 帧字节序：多字节 BIN 一律小端（据安徽已验证帧核查，如 11H-F231 每组长 2B 小端）。
- 地址域规则：下行 A1=cco_addr、A3=sta_addr（无具体目标时 A3 同址 cco）；
  上行 A1=sta_addr、A3=cco_addr；广播 A3=999999999999H。
- `send.raw` 彻底移除，传入即报错；`format:"local"+buff` 保留为迁移期兼容分支
  （迁移完成可删，代码中有注释标注）。
- 测试新增：`test_10376_encode.py`(13)、`test_scenario_codec.py`(14)、
  `test_scenario_contract.py`(4)、`orchestration/test_profile_loading.py`(7)。
- 真机验证前建议先跑：`pytest libs/sim_concentrator/ libs/parser_lib/adapters/adapter_10376/tests/ apps/workbench/test_anhui_task.py apps/workbench/test_orchestration.py apps/workbench/orchestration/ -q`
