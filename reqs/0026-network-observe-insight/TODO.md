# TODO — REQS-0026 组网观测价值化

> 只追加不覆盖。完成一项勾选一项，并在 `DONE.md` 追加记录。
> 阶段门：每完成一个阶段停下报告，等用户显式指定下一步。
> 判定门限出处：蒸馏库 CCO实现逻辑 07/08 篇（心跳 4 周期离网、成功率 98/90%、CSMA 60%/80%、冲突仲裁时序），代码注释必须标明出处；完整断言库仍归 REQS-0025 C1。

## 阶段 1 — 后端：结论层与按需解析

- [ ] Step 1.1：事件三级分级（异常/关注/常规）与人话摘要模板，落到 nwk_service 事件查询层（不改 parser_lib 协议纯度）
- [ ] Step 1.2：TEI 档案实时聚合（从 nwk_events fields_json 提取 MAC/表地址，事件摘要翻译）
- [ ] Step 1.3：`GET /api/network/digest`（≤4KB：总体判定一句话 + 异常清单 + 网络概要 + 自适应时间桶计数，桶内含异常数）
- [ ] Step 1.4：`GET /api/network/events/{frame_id}/brief`（按需单帧粗略解析，adapter_dualmac 现场解，≤2KB 分层中文摘要）
- [ ] Step 1.5：单测（真机夹具：被拒关联→异常、常规心跳→折叠、brief 与 digest 体积口径）

## 阶段 2 — 前端双副本：结论卡 + 定点排查 + 行内解析

- [ ] Step 2.1：顶部印象结论卡（一句话判定 + 异常计数 chips + "信道质量分→网络承载评估"分工跳转）；卡片区删除信标周期/CSMA 占用重复指标
- [ ] Step 2.2：时间桶导航条（自适应粒度，桶高=事件数、标红=有异常；点击桶→事件表只加载该窗；空桶折叠不渲染）
- [ ] Step 2.3：事件表降噪（默认只显异常+关注，"显示常规"开关；行加分级色与人话摘要）
- [ ] Step 2.4：点击行 → 行内展开粗略解析面板（懒加载 brief，不整页预载）
- [ ] Step 2.5：评估页时间线异常周期 → 带时间窗跳转组网观测（下钻联动）
- [ ] Step 2.6：独立版与 workbench 双副本同步（API 前缀参数化沿用 0024 补丁脚本方式）

## 阶段 3 — AI 消费路径

- [ ] Step 3.1：ai-control-plane 技能补一节：digest=L1 结论 → events=L2 明细 → brief=L3 单帧 的低 token 排查路径与示例问题
- [ ] Step 3.2：docs/api-contract.md、features.md 同步

## 阶段 4 — 收尾

- [ ] Step 4.1：全量回归（listener / parser_lib / 前端静态校验）+ REQS-INDEX 状态更新 + DONE.md 归档
