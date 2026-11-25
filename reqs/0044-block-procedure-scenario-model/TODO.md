# TODO.md — 需求 0044 交付/阶段状态

> 基线：`REQS.md` **v2**（2026-11-24，块—用例—场景模型重建；§8.1 六条已拍板）。
> 一个阶段验收通过才勾 `- [x]`；细粒度步骤见后续 `PLAN.md`（待生成）。
> 前置：REQS-0042 已交付（真机 `meter_smoke_min` 5/5 PASS）；`docs/blocks/` 五份文档已产出待审。

## P0：开工门禁
- [x] 复核基线：`pytest libs/case_runner -q` 全绿（开工时 939 passed 2 skipped；收口时 960 passed 2 skipped）；`case_run check` problems==[]
- [x] **用户已拍板 REQS §8 六条**（2026-11-24）→ 结论见 `REQS.md` §8.1
- [x] 五份文档按 §8.1 口径回填（翻页拉全 / 不校验数值真值 / 等待预算写死）
- [ ] 五份 `docs/blocks/` 文档过审；`archive.query` 块文档补齐（第二批）
- [ ] `PLAN.md` 生成

## P1：判据归位（观测 / 期望求值器）
- [x] 块返回值收敛为"观测 + 可得性 + 没拿到原因"（不含 pass/fail）——7 块切片完成（AC-1 门禁 test_gates_0044.py）
- [x] 抄表类期望从"等于某值"改为"可解出非空值"；精确比对仅在显式给期望值时启用（§8.1-4）——真机取证：只验有值且可解，1234.56/220.1 仅作观测记录
- [x] Step 增加 `expects`：求值器按"期望 vs 观测"产出步骤态（core/expect.py + executor 双轨判定）
- [x] 四态聚合链路不变（`fail > blocked > inconclusive > pass`），回归测试全绿
- [x] 门禁：块层出现结论常量即失败（AC-1，test_gates_0044.py AST 扫描）

## P2：块迁移（单表抄表链优先）
- [x] 5 块迁移：下发档案 / 查档 / 组网判定 / 绑表取址 / 抄表（另含 fix×2，共 7 块观测化）
- [x] 16 块全量补 `kind` / `side_effect` / `version` 三字段 + 声明指纹（AC-4，loader check_block_metadata 门禁）
- [x] **组网块支持翻页拉全**（§8.1-2）：topology_snapshot_paged 按「节点总数量」翻页取全；取不全 reason_code=incomplete（修 R-1）
- [x] 修 R-2：7 个已迁移块声明 assert 元数据改为 observation_fields（与实现同步）
- [x] 修 R-3：等待预算唯一口径 = Procedure 步骤显式给（块默认只兜底，loader wait_param 门禁拦缺预算）
- [x] 补事实链三条：archive.written / nwk.joined / sta.addr_obtained（AC-5，非参数化，运行内回写）
- [x] 门禁：调换"建档/组网/绑表/抄表"顺序会被事实层拦下（test_gates_0044.py::test_ac5_swapped_order_is_blocked_by_fact_chain）

## P3：用例与场景归一
- [x] `Case` 正名 `Procedure`（ProcedureSpec，CaseSpec 别名保留），补 `kind: regression|scenario|setup`（变更 3 L1）
- [x] 现有用例补 `expects`：bench_ready(kind=setup) / meter_daily(6步) / meter_first_join(7步) / archive_ready / archive_absent(新增，AC-2 反向) / nwk_join / archive_multi；`case_run check` problems==[]
- [ ] 6 个场景模板翻译成 `kind=scenario` 的 Procedure（含人肉注入改成注入块）
- [ ] 场景级判据：`only_once` / `cross_step` / `flow`（复用 `compare.py` 五类差异）
- [x] 门禁：无期望的 Procedure 不许入库（AC-6，loader check_procedure_judgement：无 expect / 缺等待预算都拒）
- [x] 生产页（/pages/cases）移植小修四项（REQS 变更 4）：`GET /api/cases` 暴露 `kind` 徽标（回归=默认/场景=蓝/台架准备=灰）；新增 `GET /api/cases/runs?limit=N` 历史运行索引（缺 result.json 跳过）+ 清单页下拉载入；台架横幅非闸门事实（required=false 回写型）三态芯片中性灰（闸门判定一字不动）；契约口径对齐（setup 行 steps_text 有值即渲染、evidence_paths 为宿主机绝对路径页面只复制）

## P4：文档同源 + 离线校验
- [ ] `docs/blocks/*.md` 由块声明生成（只读），手改被判漂移（AC-7）
- [ ] 每份块文档含"反例"节，且反例在单测里可执行
- [ ] 不启动工作台时：`list` / `show` / `check` / 文档生成全部可用（AC-8）
- [ ] 代码扫描：块层零 HTTP 字面量、零端口常量

## P5：收编旧路径 + 收口（§8.1-6：`autotest/` 与 `scenarios/` 合并为一个）
- [ ] `autotest/` 降为薄适配器（生成 plan 调 CLI），bool 结论不再产出
- [ ] `scenarios/*.json` + 只读场景页：按用户决定删除或降为只读适配器
- [x] 真机验收（提前完成，P5 之外的 0044 DoD）：meter_smoke_min（bench_ready setup + meter_daily 6 步含查档）真机 PASS ×2（30.9s / 18.0s，连跑结论一致）；archive_absent（期望不含）真机 PASS —— AC-2 双向真机达成。证据：apps/workbench/runtime/ai-control/case_runs/{20261124-161133-979545, 20261124-161243-db2fb8, 20261124-161306-56e177, 20261124-161629-df6184}/result.json
- [ ] `DONE.md` + `REQS-INDEX.md` 状态更新

## 遗留风险跟踪
- [ ] R-4 期望值容差（真表漂移 → 假失败）
- [ ] R-5 整轮超时不杀线程（僵尸线程占串口）
- [ ] R-6 `cleanup` 死字段（0/16 声明）
- [ ] R-7 场景里的人肉注入
- [x] 工位 E：用例库页简洁化（用户 5 条反馈）：job 面板单行/终态自动收起可展开；台架横幅就绪态一行收起、不就绪才展开；事实芯片收进折叠；新增「默认参数」入口（`GET/PUT /api/cases/defaults` 白名单覆盖 `config/case_defaults.json`，写前备份、未知键 422）；删冗余说明——门禁 `pytest apps/workbench/test_cases_api.py apps/workbench/test_cases_page.py -q` 104 passed，Playwright E2E + 截图 11 张见 DONE.md 2026-11-24 工位 E 段
