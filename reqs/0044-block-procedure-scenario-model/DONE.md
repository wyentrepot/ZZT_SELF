# DONE.md — 需求 0044 完成日志（只追加，最新在上）

> 基线：`REQS.md` **v3**（2026-11-24，含变更 3：领导 L1/L2/L3）。
> 规则：每个阶段落地一段；带证据（命令输出 / 真机结论 / 文件行号）。

## 2026-11-24 ｜ §6.2 终局验收：C2 UI 端到端验收（真实浏览器 × 真实台架 × 生产页）

**验收方式**：Playwright 驱动真浏览器（Chromium headless 1560×1000）走生产页用户路径
`http://127.0.0.1:8790/#cases`（壳 SPA → 保活 iframe 载 `pages/cases/cases.html`），
全程只点页面控件（方案下拉 / ▶按方案运行 / 返回清单 / 历史运行下拉 / run_id 输入），
零 CLI 代跑、零手动补参（`params={}`，`meter_addr` 留空走 case_defaults），`interventions[]` 恒空。

**步骤 1 ｜ 台架横幅 + 运行闸门**：横幅 `✓ 就绪`，hint「5 口全通 · 6 条关键事实成立」；
串口 5 口可用（listener COM27 / sta-main COM18 / cco-main COM12 / simeter COM20 / simcon COM4，
ecu-biz/ecu-mgmt 显式停用不计）；必需事实 `bench.simcon_online` / `bench.sta_online` 均「成立」；
闸门通过后 `▶按方案运行` 可用（未选方案时 title=「先选择方案」，符合预期）。
截图 `docs/ui/shots/c2_e2e_01_bench_banner.png`。

**步骤 2–3 ｜ 第一轮 UI 运行**：下拉选 `meter_smoke_min` → 点「▶按方案运行」→ job 面板
进度条 50%、「运行中…」（截图 `c2_e2e_02_round1_progress.png`）→ 终态「已结束」自动切结果视图。
run **20261124-173119-79e0c0**：verdict=**pass**，32.4s；结果表 2 行——
setup「台架就绪自检」✔（大类/子类=「—」）+ regression「抄表日常回归」✔
（测试大类=抄表、子类=CCO；steps_text=「1.档案下发；2.档案查询；3.组网；4.绑表（取表地址）；5.抄表；6.抄表」六步；
失败原因列=「—」，仅证据路径折叠）；汇总条 product_pass_rate=**1/1**（✔2 ✘0 ⊘0 ？0，
「台架准备 1 项不计入分母」）；证据路径指向本轮
`.../case_runs/20261124-173119-79e0c0/result.json`（与历史 df6184 区分）。截图 `c2_e2e_03_round1_result.png`。

**步骤 4 ｜ 幂等复跑**：返回清单间隔 15s 后**同一路径第二次点击**，run **20261124-173204-2bf0e3**：
verdict=**pass**，17.9s，product_pass_rate=1/1，失败原因列=「—」（截图
`c2_e2e_04_round2_progress.png` / `c2_e2e_05_round2_result.png`）。两轮 run_id 不同、结论一致；
第二轮建档块观测「下发动作=已存在，跳过下发」「未发 11H-F1，避免 CCO 对重复建档回 00H-F2 否认帧」
——ID-1 幂等零 11H-F1 在 UI 路径复现。两轮 `interventions=[] / self_healed=[] / problems=[]`。

**步骤 5 ｜ 开发者详情核对**：结果视图展开「开发者详情 · 块级观测明细」，块级 observation
（fix.simcon_session / fix.simeter_session / archive.add / archive.query / nwk.topology /
meter.addr_probe / meter.read_645 ×2）与 `GET /api/cases/runs/20261124-173204-2bf0e3` 逐块一致
（含本轮特有「沿用已开口」措辞、电能 1234.56 kWh、电压 220.1 V、档案地址 129078563412）；
历史下拉刷新后含两轮新 run，经下拉载入 2bf0e3、run_id 输入载入 79e0c0 双路径核对通过。
截图 `c2_e2e_06_round2_dev_details.png`。

**完成判据**：两轮终态均 pass（79e0c0 / 2bf0e3）✅；结果表渲染本轮数据（标题/证据路径/时间戳随 run_id）✅；
截图 6 张 ≥5 ✅；无阻断性 bug ✅。

**发现的问题（只记录不修，均非阻断）**：
1. job 终态后历史运行下拉不即时刷新（`loadRunsIndex()` 仅页面初始加载时调用，cases.js L414）：
   当轮结果视图已自动渲染不受影响，刷新页面后新 run 出现在下拉中（已实测）。
2. 结果行「证据 ▸」折叠默认收起属设计（Reason 列只留按钮），无功能缺陷，仅提示关注窄屏表现。

**证据**：截图 `docs/ui/shots/c2_e2e_01…06_*.png`（6 张）；DOM 取证与两轮 result.json 见
`apps/workbench/runtime/ai-control/case_runs/{20261124-173119-79e0c0,20261124-173204-2bf0e3}/`。
复位 STA：未执行（链路在网，未发生卡死，符合红线）。

## 2026-11-24 ｜ §6.2 第 1–4 步实施完成（判据归位 + 实体归一 + 报表契约 + 真机验收）

**文档落档**
- `REQS.md` v3：变更 3（L1 Procedure.kind=setup / L2 报表契约字段冻结 / L3 一句话人话口径），只追加不覆盖
- `PLAN.md`：文件级步骤 + 验证命令（基线 v2+变更3）

**模型与门禁（S1/S2）**
- `core/models.py`：`CaseSpec` 正名 `ProcedureSpec`（别名保留）；Procedure 增 `kind(regression|scenario|setup)` +
  `category{usage,domain,scene,region}` + `protocols`；BlockSpec 增 `kind/side_effect/version` +
  `observation_only` + `wait_param` + `fingerprint`（声明内容 sha256 前 16 位）
- `blocks/loader.py`：`check_block_metadata`（AC-4：16 块必须显式带三字段）+
  `check_procedure_judgement`（AC-6：引用 observation_only 块缺 expect → 「无判据不入库」；
  等待型块缺显式 wait_param 预算 → 「缺预算视为缺判据」，v2 拍板 #3）；两者挂进 load_all problems，
  check / run / API 一并拒绝

**判据搬移切片（S4，7 块观测化）**
- `blocks/fix.py` / `archive.py` / `nwk.py` / `meter.py`：`fix.simcon_session` / `fix.simeter_session` /
  `archive.add` / `archive.query` / `nwk.topology` / `meter.addr_probe` / `meter.read_645`
  只返回 `Observation(values/complete/reason_code/detail)`，不再输出 pass/fail（AC-1）；
  原因码口径：串口占/409→serial_busy、不可达/404→bench_unreachable、等满无帧→timeout、
  配置错→invalid_param、内层不可解/查不全→incomplete（evaluator 聚合映射不变）
- `meter.py`：`Verdict` 增 `values/observed`（判据函数同时产出观测字段，事实层复用不变）；
  `wait_attempts` 增 `stop_when`（拿到可判观测即停，不再为等一个不存在的 pass 反复重发）
- `nwk.py`：`topology_snapshot_paged` 翻页拉全节点记录（修 R-1：主节点数取全才算观测完整，
  取不全 complete=False + incomplete）；`block_topology` 观测化（观测字段含 门槛，由步骤 ge_field 比对）
- 幂等/地址换算/只读闸门/自愈行为逐字保留（archive.add 幂等预查、call_verify 409 自愈、readonly_violation）

**事实链（S3，AC-5）**
- 新增非参数化事实 `archive.written` / `nwk.joined` / `sta.addr_obtained`
  （`data/facts/*.json` + `blocks/facts_chain.py`：独立探测恒 unknown，yes 只能由块 provides 回写）；
  块声明补链：archive.add provides→nwk.topology needs/provides→meter.addr_probe→meter.read_645；
  参数化事实（archive.contains 等）仍禁止块回写（跨地址假 PASS 门禁不变）

**Procedure 迁移（S5）**
- `data/cases/`：`bench_ready`（kind=setup）/ `meter_daily`（6 步含查档①b）/ `meter_first_join`（7 步含复位）/
  `archive_ready`（期望含）/ **`archive_absent`（新增，期望不含，AC-2 反向）** / `nwk_join` / `archive_multi`
  —— 全部带声明式 expect + 显式等待预算；1234.56/220.1 从默认判据移除（R-4，显式给才比对）；
  删除行为说明见各文件与 test_*.py 的 D7-1 注记

**报表契约（S6，L2）**
- `core/report.py`：`build_run_report`（rows/summary，字段逐条按冻结契约）+ `steps_text_of`（业务动作文本，零块名）
- `apps/workbench/cases_api.py`：`RunRecordView.report` 新字段（落盘 result.json 与 GET /api/cases/runs/{run_id} 读回一致）；
  `BlockResultView.observation` 新字段（每步观测进证据）
- 契约测试：`test_cases_api.py::test_run_report_contract_rows_and_summary`（setup 行 group/subgroup=null、
  counts_as_product=false、product_pass_rate 分母不含 setup）

**门禁测试（S7）**
- 新增 `libs/case_runner/test_gates_0044.py`：AC-1（AST 扫描 7 块函数：无 BLOCK_PASS/BLOCK_FAIL 常量、
  输出字符串无「通过/失败/PASS/FAIL」，docstring 豁免）/ AC-2（同一查询块双向 + 端到端执行器链）/
  AC-4（16 块三字段 + 指纹敏感性）/ AC-5（链声明 + 调换顺序被拦）/ AC-6（无 expect / 缺预算 / 零预算三种拒绝 + 对照通过）
- 受影响既有测试按 D7-1 改写/删除（每处注明覆盖行为）：test_case_meter / test_cli / test_defaults /
  test_cases_declaration / blocks/test_{fix_reset,archive_nwk,nwk_topology,meter}.py / apps/workbench/test_cases_api.py

**门禁结果**
- `pytest libs/case_runner apps/workbench/test_cases_api.py -q` → **960 passed, 2 skipped**（开工基线 939+2）
- `test_layering.py` 全绿（10 passed）；`grep -rn '^from apps' libs/` = 0；块层无 HTTP 字面量/端口常量

**真机验收（DoD，台架 simcon COM4 / CCO COM12 / STA COM18 / simeter COM20，meter_addr 走 config/case_defaults.json）**
- `meter_smoke_min`（bench_ready setup + meter_daily 6 步含查档）：**PASS ×2**
  - run 20261124-161133-979545：verdict=pass，30.9s，6 步全过（写入确认=确认 → 在档=true →
    在网从节点数 1 ≥ 门槛 1（翻页取全）→ 地址一致=true → 电能 1234.56 kWh / 电压 220.1 V 有值且可解）
  - run 20261124-161243-db2fb8：verdict=pass，18.0s —— 连跑两次结论一致（ID-1 幂等：第二轮建档
    「已存在，跳过下发」零 11H-F1 帧）
- `archive_absent`（期望「不含」）：run 20261124-161306-56e177 **PASS** —— AC-2 双向真机达成
  （与 meter_daily 查档步「期望含」同一 archive.query 块、相反期望）
- 复位 STA：未执行（链路在网，无需复位，符合「不要复位 STA」要求）
- 证据：`apps/workbench/runtime/ai-control/case_runs/<run_id>/result.json`（含每步 observation、
  report.rows/summary、interventions=[]、decisions[]）
- 运维备注：工作台以 WORKBENCH_LOCAL_FULL_ACCESS=1 重启（D2-9 授权范围内；v2 网关 token 闸门
  会拦块能力层的 /api/ai/v2 调用，本机 loopback 免 token 是 0034 既定姿势）

**本轮不做（按范围红线）**：P5 收编旧路径（autotest//scenarios/）；场景级判据（only_once/cross_step/flow）；
文档生成器（P4）；其余 9 块观测化（下一轮）。

**遗留项（顺延）**
- R-5 整轮超时不杀线程；R-6 cleanup 死字段；R-7 场景人肉注入；其余 9 块判据搬移（reset.sta /
  archive.del / nwk.nodes_online / nwk.route_status / nwk.archive_count / nwk.onnet_count /
  nwk.route_restart / nwk.active_registration / meter.brain_local）
- P4 文档同源 + 离线校验；P5 收编旧路径

---

# DONE.md — 需求 0044 完成日志（只追加，最新在上）

> 基线：`REQS.md` v1（2026-11-24，块—用例—场景模型重建）。
> 规则：每个阶段落地一段；带证据（命令输出 / 真机结论 / 文件行号）。

## 2026-11-24 ｜ 六条口径拍板回填（v2）

**用户拍板（REQS §8.1）**
1. 查档步：要（单表抄表固定 6 步 / 首次 7 步）
2. 组网节点数超一页：必须翻页拉全，取不全标"观测不完整"
3. 等待预算：写死在用例里，块默认仅兜底
4. 期望值：只验"有值且可解"，不校验数值真实性
5. 解耦三档：认可（读/校验离线、跑依赖台架）
6. 旧路径：`autotest/` 与 `scenarios/` 合并成一个

**已回填**
- `REQS.md` v2（§8.1 + 变更记录）
- `TODO.md`（P0 拍板勾选；P1/P2 增加翻页拉全与容差口径任务）
- 五份 `docs/blocks/` 文档：组网（翻页拉全、严格口径唯一）、绑表（预算写死）、抄表（不校验真值）、流程（④⑤ 改"可解即可"、①b 必备）、下发档案（预算兜底说明）

**代码状态**：仍未改动。

## 2026-11-24 ｜ 立项（尚未开始实施）

**已完成（文档形态）**
- 立项 0044，落 `REQS.md` v1 + `TODO.md`（P0–P5）
- 首批块与流程文档 5 份（`docs/blocks/`）：下发档案 / 组网判定 / 绑表取址 / 抄表 / 单表抄表流程

**代码状态**：**未改动**。现有块仍是"块内判据"的旧口径，文档描述的是目标态；每个块文档末尾有"与当前实现的差异"一节。

**待用户拍板**：REQS §8 六条（查档步、组网严格口径、等待预算、期望容差、解耦边界、旧路径去留）。

## 2026-11-24 ｜ 工位 E：用例库页简洁化（用户 5 条反馈逐条落地，领导已拍板）

**范围**：仅用例库页（`apps/workbench/static/pages/cases/`）+ `cases_api.py` 默认参数两端点；不做真机运行，job 运行演示用 Playwright route mock（不触台架）。

**反馈逐条落地**
1. **job 面板臃肿且无收缩** → 运行中只显示单行（方案名 `job-line` + `进度 n/m · 当前用例名` `job-progress-line`），进度条/汇总/逐卡结果全部收进 `job-collapsible`（默认 hidden）；完成后自动收起为一行（`collapseJobDone`：✔/✘ 字形 + run_id + verdict 一句 + 耗时，`job-done-line`），**点击该行才展开**完整结果（报告卡 + 日志；仅终态可点，`S.jobTerminal`）；job 终态**不再整页自动跳转**结果视图，Excel 式结果视图保留，经「查看完整报表」（`btn-open-report → loadRun`）与历史运行下拉进入（六列表/汇总条/开发者详情一个不少）。
2. **台架横幅无收起按键** → 就绪态收成一行「✓ 就绪」+「▸ 详情」（`bench-detail-toggle`/`bench-details`，默认 hidden）；只有不就绪（`benchMode==="blocked"`，即 unmet_required 非空或口不可用）才**自动展开**列缺什么；口状态/事实芯片/逐项原因全部收进详情，用户点过「详情」以用户为准（`S.benchDetailsOpen`）。
3. **事实芯片太多** → 默认视图一张芯片都不显示（`bench-ports`/`bench-facts` 整体搬进折叠区，渲染逻辑原样保留，只是默认不展开）。
4. **默认信息没有改动的地方** → 工具条新增「默认参数」入口：一行当前默认（`默认参数：默认表地址 123456789012 等 3 项`）+「修改」；小表单（`defaults-modal`）只渲染 `GET /api/cases/defaults` 下发的键与业务文案。后端新增：
   - `GET /api/cases/defaults`（白名单 = `config/case_defaults.json` 里实际存在的键，摊平为 `meter_addr` / `simeter.addr` / `simeter.version`；出厂默认独有的 `mapping_id.*` **不进表单**，不造键）
   - `PUT /api/cases/defaults`（白名单校验，未知键 422 `defaults_key_unknown`；表地址类键 12 位十进制否则 422 `defaults_value_invalid`；null/容器值 422；**写前备份** `<config>.bak-defaults-<时间戳>`；原子写，`_note`/未点名键原样保留；config 坏 JSON 拒改写 500）
   - 键→业务文案映射 `DEFAULTS_LABELS`（默认表地址/模拟电表地址/模拟电表版本），未知键回退键名
   - **「STA 通信地址」无对应配置键（libs 与 config 都没有），未造键**，只在报告里说明
   - 原 `p-meter-addr` 逐轮覆盖输入框移除（`collectParams` 保留恒空，运行能力不回退——地址默认值统一由 config 承载）
5. **删冗余说明** → 删：横幅「运行闸门：5 口可用 + 必需事实成立」、清单页「点击卡片展开步骤…」、汇总条「setup 行不计入…防永真用例」大段解释、kind 徽标三处 title 悬浮说明、历史下拉/run_id 输入框 title 里的 API 细节、证据路径 title 里的文档引用；初始视图只剩【清单 + 运行 + 结果】三要素（改前基线：横幅 5 行 + 2 组芯片 + 开发者详情入口平铺、hints 8 处、工具条 8 控件 → 改后：横幅 1 行、hints 6 处、工具条 7 控件 + 默认参数入口）。

**守卫测试同步**（`test_cases_page.py` 26→30 条，每处注明覆盖行为，实质覆盖不降）：新增 `test_bench_banner_collapses_when_ready_and_autoexpands_when_blocked`（① 详情容器/按键存在默认收起 ② blocked 自动展开 ③ 芯片仍渲染在折叠区内可断言 + 反向守卫冗余说明已删）、`test_job_panel_single_line_and_collapsed_done`（单行结构/终态四要素/自动收起/点击展开/查看完整报表入口/禁止终态整页跳转）、`test_defaults_entry_modal_and_save_flow`（入口/表单/PUT 链路/保存后刷新显示 + 反向守卫不造键）、`test_redundant_explanations_are_gone`；`test_two_level_run_and_job_polling` 与 `test_bench_banner_and_run_gating` 保持全过（旧钩子全保留）。冒烟脚本 `testdata/cases_smoke.js` **零改动**仍全过（6 条路径）。另修 `__casesSelfcheck` 一处既有缺陷：块名泄漏扫描只跳过两个大折叠，未跳过报告卡内默认 hidden 的 `data-hook="dev-details"` 折叠（D11-2 本就允许块名进折叠区），已补上。

**门禁（原文）**
- `pytest apps/workbench/test_cases_api.py apps/workbench/test_cases_page.py -q` → **104 passed, 1 warning**（test_cases_api 78 + test_cases_page 30；新增 defaults 端点测试 8 条：白名单只含 config 键 / config 缺失只读降级 / PUT 写前备份+保 `_note` / 未知键 422 不写盘 / 非法值 422 / 嵌套键保兄弟键 / 空 values no-op / 坏 config 拒改写）
- 邻套回归：`pytest apps/workbench/test_app.py test_run_preflight.py test_shell_navigation.py test_dict_cases.py -q` → **44 passed**；node 冒烟 `SMOKE PASS`

**Playwright 实测（http://127.0.0.1:8790/#cases，真后端 + mock job，不触发真机）**
- 初始视图区块数：改前 `bench_groups=2, banner_rows=5, toolbar_controls=8, hints=8` → 改后 `banner_rows=1（bench_details_hidden=true）, toolbar_controls=7 + 默认参数入口, hints=6, job_panel 隐藏`
- 横幅就绪态一行收起；点「详情」展开（芯片/原因可见）再点收起 —— `__casesSelfcheck` 清单视图零块名泄漏、零外链
- job（mock：running→done 带 report.rows）：运行中单行「勾选 1 例｜进度 1/3 · 33%　当前：抄表日常（不复位）」→ 完成自动收起「✘ mock-run-1 · 本轮 2 例通过、1 例未通过（组网超时…） · 42s」→ 点击展开报告卡 → 「查看完整报表」载入 Excel 六列结果视图（`测试大类|测试子类|用例名称|测试步骤|结果|失败原因/说明`）且 rows=3、自检全过
- 默认参数全链路（真后端）：UI 改 `meter_addr 123456789012→123456789099` → 保存后入口行即时刷新 → HTTP GET 读回新值 + 生成 `.bak-defaults-20261124-*` 备份（内含原值）→ UI 恢复原值 → GET/文件读回均原值；验证后用首个备份把 `config/case_defaults.json` **字节级还原**（`git diff` 为空），验证用备份文件已清理，未触发任何真机动作
- 深浅两主题分别以壳自身主题圆点（midnight/daylight）切换并经像素采样确认（深色顶栏 RGB(15,31,61) / 浅色 (255,255,255)），两套下横幅折叠、默认参数入口、job 单行渲染正常

**截图（docs/ui/shots/，11 张）**：`c2_e2e_e_before_dark_list.png` / `c2_e2e_e_before_light_list.png`（改前基线，由当前文件反向套用工位 E 全部补丁重建改前页面后截图；重建版通过未改动的冒烟脚本 + 基线指标逐项吻合）、`c2_e2e_e_after_dark_list.png` / `c2_e2e_e_after_light_list.png`（改后初始视图）、`c2_e2e_e_after_dark_banner_details.png`（横幅展开）、`c2_e2e_e_after_dark_job_running_singleline.png` / `c2_e2e_e_after_dark_job_done_collapsed.png` / `c2_e2e_e_after_dark_job_done_expanded.png` / `c2_e2e_e_after_dark_result_view.png`（job 单行→收起→展开→完整报表）、`c2_e2e_e_after_light_defaults_modal.png` / `c2_e2e_e_after_light_defaults_saved.png`（默认参数表单与保存后）。

**改动文件**：`apps/workbench/static/pages/cases/cases.html`、`cases.js`；`apps/workbench/cases_api.py`（+REPO_ROOT/DEFAULTS_* 常量与 helpers、`CaseDefaults*` 四模型、`CasesService.defaults_view/save_defaults`、两端点、`defaults_config_path` 注入位、`__all__`）；`apps/workbench/test_cases_api.py`（+8 条）；`apps/workbench/test_cases_page.py`（+4 条）；本 DONE.md、TODO.md 各一段。未触碰 `libs/`、`docs/ui/`（截图目录除外）、`docs/blocks/`；未 commit。

**发现但未修（顺延）**
- 主题自启（`localStorage wb-theme` → 开机内联脚本）依赖 tokens-v2.css 的 `--theme-registry` 读取，headless 首帧存在 CSS 未就绪竞态（读不到注册表就回落 daylight）；壳内切主题（圆点/广播）不受影响。生产浏览器有缓存后概率极低，如需根治可给内联脚本加一次重读。
- `GET /api/cases/defaults` 的 `source` 判定把「config 写 null（=取出厂默认）」报成 `factory`——语义正确但展示层未区分「config 显式 null」，暂无用户可见影响。
- `collapseJobDone` 的 ✔/✘ 只区分「全通过/有失败」，`cancelled` 态也显示 ✘（低频路径，可后续细分）。
