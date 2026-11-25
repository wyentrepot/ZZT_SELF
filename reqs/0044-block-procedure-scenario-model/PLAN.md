# PLAN.md — 0044 实施计划（基线：REQS v2 + 领导补充决策 L1/L2/L3 = 变更 3）

> 范围：REQS §6.2 迁移顺序第 1–4 步。第 5 步（收编 `autotest/`、`scenarios/` 旧路径）**不在本轮**，禁止顺手做。
> 工作区：`ZZT_SELF`（master，直接实施，不建分支、不 commit）。
> 既有半成品（接手时已在工作区，未 commit）：`core/expect.py`（观测/期望求值器）、`test_expect.py`、
> `core/models.py` StepSpec.expect、`core/planner.py` PlannedBlock.expect、`core/executor.py` 双轨判定（`_step_verdict`）。
> 本计划在其上续作；四态语义与聚合优先级（0042 §3.5）**一个字不改**。

## 文件级步骤与验证

### S0 文档落档（先于代码）
- `reqs/0044-*/REQS.md` 末尾追加「变更 3」（L1 Procedure.kind=setup / L2 报表契约字段 / L3 失败原因一句话口径），只追加不覆盖。
- 本 `PLAN.md`。
- 验证：`grep -c "变更 3" reqs/0044-*/REQS.md` ≥ 1。

### S1 模型层（`libs/case_runner/core/models.py`）
- `CaseSpec` 正名 `ProcedureSpec`（`CaseSpec = ProcedureSpec` 别名保留，对内引用渐进替换，对外 API 不变）。
- Procedure 加 `kind: regression|scenario|setup`（默认 regression；L1）。
- Procedure 加 `category: {usage,domain,scene,region}` 与 `protocols: []`（0042 D10-2 正式入模，替代 cases_api 的「未来字段容忍」）。
- `BlockSpec` 加 `kind: case|setup|teardown|probe`、`side_effect: none|reversible|irreversible`、`version: int`、
  `observation_only: bool`（声明该块已迁为只观测）、`wait_param: str`（等待型块声明预算参数名）；
  加 `fingerprint` property（整数 version + 声明内容 sha256）。
- 验证：`python3 -m pytest libs/case_runner/test_models.py -q`。

### S2 声明门禁（`libs/case_runner/blocks/loader.py`）
- 新增 `check_block_metadata`：16 块声明必须显式带 `kind/side_effect/version`（AC-4，缺失即 problem）。
- 新增 `check_procedure_judgement`（AC-6 + 等待预算门禁）：
  - 引用 `observation_only` 块的步骤没有 `expect` → problem「无判据不入库」；
  - 引用带 `wait_param` 块的步骤 `params` 缺该键（或 ≤0）→ problem「等待型步骤缺等待预算」。
- 两者都挂进 `load_all` 的 problems（`check`/`run`/API 一并拒绝）。
- 验证：`python3 -m pytest libs/case_runner/blocks -q -k loader`；`case_run check` problems==[]。

### S3 事实链（AC-5）
- 新增非参数化事实声明 + 探测体（运行期由前序步回写、预检/独立探测恒 unknown，绝不假 yes）：
  `archive.written`（档案写入完成）、`nwk.joined`（组网达标）、`sta.addr_obtained`（STA 已取得表地址）。
- 块声明补链：`archive.add` provides `archive.written`；`nwk.topology` needs `archive.written` provides `nwk.joined`；
  `meter.addr_probe` needs `nwk.joined` provides `sta.addr_obtained`；`meter.read_645` needs `sta.addr_obtained`。
- 验证：单测「调换 建档/组网/绑表/抄表 顺序会被事实层拦下」（planner 层）。

### S4 判据搬移切片（7 块观测化，只迁单表抄表链；其余 9 块下一轮）
- 迁移块（`observation_only: true`，函数只返回 `Observation`，不再输出 pass/fail）：
  `fix.simcon_session` / `fix.simeter_session` / `archive.add` / `archive.query` /
  `nwk.topology` / `meter.addr_probe` / `meter.read_645`。
- 观测 schema 对齐 `docs/blocks/` 五份文档（走到哪层报到哪层；没拿到=原因码；不判值）。
- 期望口径（v2 拍板 #4）：默认只验「有值且可解」+ CS 合法 + 控制码正常 + DI 回显；显式给期望值才比对。
- 等待预算（v2 拍板 #3）：写死在 Procedure 步骤 params；块默认只兜底。
- 组网翻页拉全（修 R-1，v2 拍板 #2）：`nwk.topology` 按 `节点总数量` 翻页取全节点记录；
  取不全 → `complete=false` + 原因码 `incomplete`，绝不按本页数下结论。
- 内部解析函数（`judge_*` 等）保留：事实探测体（facts 层）共用，不属于块层。
- 验证：`python3 -m pytest libs/case_runner/blocks -q`（受影响测试改写/删除并注明覆盖行为）。

### S5 Procedure 迁移（`libs/case_runner/data/cases/*.json`、`data/case_suites/*.json`）
- `bench_ready` → `kind=setup`，步骤带期望（L1：报表大类/子类「—」，不计产品通过率）。
- `meter_daily` 日常版 6 步（含查档①b，步序以 `docs/blocks/case-流程-单一档案抄表.md` §2/§5 为准）；
  `meter_first_join` 首次版 7 步（含复位）；每步 `expect` + 显式 `wait_s` 预算。
- `archive_ready`（期望「含」）/ 新增 `archive_absent`（期望「不含」）＝AC-2 双向证明的真机入口；
  `nwk_join`、`archive_multi` 补期望改写。删除/改写处注明覆盖行为（D7-1 口径）。
- 期望值 1234.56/220.1 从默认判据中移除（R-4），显式给才比对。
- 验证：`case_run check` problems==[]；`case_run list --cases` 可读。

### S6 报表契约（L2，`core/report.py` + `apps/workbench/cases_api.py`）
- 新增 `report.rows[]`（group/subgroup/name/kind/steps_text/verdict/reason/evidence_paths/counts_as_product）
  与 `report.summary`（total/pass/fail/blocked/inconclusive/product_pass_rate），
  作为 `result.json` 与 `GET /api/cases/runs/{run_id}` 的**新字段**（语义只加不改）。
- `steps_text` 零块名（D11-2）；`kind=setup` 行 group/subgroup=null、counts_as_product=false。
- 验证：`python3 -m pytest apps/workbench/test_cases_api.py -q`（新增 report 契约用例）。

### S7 门禁与收口
- AC-1 门禁测试：7 个已迁移块函数源码无 pass/fail 结论常量、无「通过/失败」字样。
- AC-2 单测：同一 `archive.query` 块，两条相反期望 Procedure 各自 pass（fake 台架）。
- 全量：`python3 -m pytest libs/case_runner apps/workbench/test_cases_api.py -q` 全绿；
  `grep -rn '^from apps' libs/` = 0；`test_layering.py` 全绿。
- 真机验收（DoD）：`meter_smoke_min`（bench_ready + meter_daily 6 步）真实台架跑通，
  `meter_addr` 走 `config/case_defaults.json`；台架不可达时用 fake 单测交付并标 blocked。
- `TODO.md` 勾选、`DONE.md` 追加。

## 不做（本轮红线）
- 第 5 步收编旧路径（`autotest/`、`scenarios/`）。
- 四态语义/聚合优先级改动；新增第二套执行器/结果模型；同台架并行。
- 场景级判据（only_once/cross_step/flow）与文档生成器（P4）——下一轮。
