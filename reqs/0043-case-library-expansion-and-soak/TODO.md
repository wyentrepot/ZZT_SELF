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
- [x] F0 知识库核查完成（2026-11-24：并发抄表/单表多DI/并发多帧/698/协议切换 五项，落档 `CASE-BACKLOG.md` §F0；协议切换口径以用户拍板为准——645↔698 必须复位 STA，STA 运行期禁止直改协议）
- [x] F1 补块批次 1 完成（2026-11-24，5 块全部带单测与声明，真机运行留 F3）：`meter.batch_read`（simcon batch_read 薄编排，99s 档，F0-2）/ `gate.multi_frame_110`（>13 条否认 6E，F0-4）/ `gate.concurrent_109`（连发 21 帧否认 6D，F0-4）/ `meter.read_698`（02H-F1 protocol=3 冒烟档，F0-5）/ `param.init_01h_f2`（协议切换三件套第一步，F0-6）；附带修复 batch.py 698 构帧贴错 645 标签（F0-5 发现，仅构帧分流一处）。实施记录见 `DONE.md`；pytest `libs/case_runner` 933 passed，`case_run check` problems==[]（块 21）
- [x] F2 新用例声明批次完成（2026-11-24，5 条 Procedure + 1 方案全部声明式，真机验收留 F3）：`meter_di_multi_item`（5 DI 逐项，F0-3）/ `meter_batch_single`（并发单表，expect_timeout 缺省按 99s 档、预算 130s 写死，F0-2）/ `gate_multi_frame_6e`（count=14 否定式期望否认码 6E）与 `gate_concurrent_6d`（frame_count=21 期望 6D）（F0-4）/ `meter_698_smoke`（kind=scenario 重用例：复位 STA → 01H-F2 → protocol=3 重建档 → 组网 → 绑表 → read_698；复位恢复 ≈10min 单列——绑表/抄读各 600s，整轮需 run --timeout 放大，日常回归不进，F0-5/F0-6）；方案 `meter_expand_smoke`（4 条，stop_on_fail=false：闸门类失败不挡观测类）。实施记录见 `DONE.md`；`case_run check` problems==[]（块 21 / 事实 11 / 用例 13 / 方案 3）；pytest `libs/case_runner` 940 passed、`apps/workbench/test_cases_api.py` 74 passed
- [x] H 698 链修复执行批次完成（2026-11-24，按 CASE-BACKLOG §G-6 缺块清单补齐，全程 fake 单测不碰台架）：新块 `bench.simeter_protocol`（setup/reversible：切/读 simeter 协议模式 jzjc.protocol_type，写后回读确认，判据归 Step）/ `sta.meter_protocol`（probe/none：表口协议日志指纹 check_procotol:N 判读，口径未经真机校准不设 provides）/ `archive.query` 观测增强（10H-F2 D15~D13 → 在档协议类型字段，不改判据）/ `meter_698_smoke` 重构（s0 切 698 + s3c 档案协议核验=3 + s4 组网预算 300s→900s 写死，§G-2 H1 run 20261124-192633 实测 ≈11 分钟入网证据，整轮建议 run --timeout 2400）/ simeter 补 STA 698 探帧 OAD 40010200 应答（§G-2 H2，meter.py _handle_698 唯一分支 + 逐字节黄金帧单测）。实施记录见 `DONE.md` §H；pytest `libs/case_runner libs/sim_meter` 1109 passed 2 skipped、`apps/workbench/test_cases_api.py` 74 passed，`case_run check` problems==[]（块 23 / 事实 11 / 用例 13 / 方案 3）

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
