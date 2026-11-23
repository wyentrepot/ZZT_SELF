# TODO.md — 需求 0043 交付/阶段状态

> 基线：`REQS.md` **v1**（2026-11-23，用例库扩充 + 长跑验证）。
> 一个阶段验收通过才勾 `- [x]`；细粒度步骤见 `PLAN.md`（由 `writing-plans` 生成，**当前待生成**）。
> 前置：REQS-0042 已打通真实台架（`meter_smoke_min` 5/5 PASS、单轮 17s、连跑两次一致）。

## P0：开工门禁
- [ ] 复核 0042 交付基线：`pytest libs/case_runner -q` 全绿；`case_run check` problems==[]
- [ ] 台架自检：`GET /api/serial-bridge/status` 5 口 usable（Windows 侧桥需先启动）
- [ ] 复核知识库用例库分类体系文档：`reqs/0042-*/CASE-LIBRARY-taxonomy.md` 存在且有出处
- [ ] `PLAN.md` 生成（writing-plans）

## P1：用例库盘点与可测性标注（CL-1 / CL-2）
- [ ] `CASE-BACKLOG.md`：维度表（常用度/CCO/STA/ECU/场景/省份/协议），每条带出处
- [ ] 可测性标注：逐条 `可测 / 不可测`，不可测写明缺什么
- [ ] 分档：可测且常用 → 第一批；其余分后续批次
- [ ] 门禁：抽查 10 条有出处；「库中未找到」项显式列出

## P2：补用例（CL-3 / CL-4）
- [ ] 新增 CCO 域用例 ≥ 1 条（真机出结论）
- [ ] 新增 STA 域用例 ≥ 1 条（真机出结论）
- [ ] 新用例均带 `plan` 步骤实例化 + `category/protocols` + `on_fail` + 等待预算
- [ ] 门禁：`case_run check` problems==[]；`list --cases` 分类可见

## P3：长跑验证（CL-5 / CL-6 / CL-7）
- [ ] 连跑 ≥ 20 轮（`meter_smoke_min`），汇总轮次/结论/耗时/RSS
- [ ] 持续 ≥ 2 小时（可间隔），异常轮次可定位
- [ ] 抽查 3 个 `result.json`：`verdict/params/decisions/interventions(恒空)/self_healed/时间戳/台架快照` 齐全

## P4：收口（CL-8）
- [ ] 收敛报告：已覆盖 / 不可测 / 下一批优先级
- [ ] `DONE.md` + `REQS-INDEX.md` 状态更新
