# DONE.md — 需求 0043 实施记录（按工位追加）

> 约定：每个工位收口时追加一节；只记「做了什么 / 判据出处 / 怎么验的」，不复制代码。

## F1 补块批次 1（2026-11-24）

**范围**：CASE-BACKLOG §F0 点名的 5 个缺块 + §F0-5 发现的 batch 698 构帧贴错标签修复。
全部块按 REQS-0044 v3 红线交付：只返回观测（零 pass/fail 判定）、期望全部留给
Step、三字段（kind/side_effect/version）显式齐备 + 声明指纹、事实链 needs 如实
（全部不声明 provides——不确定就不声明）、块层零 HTTP 字面量（走 capabilities）、
等待预算挂 `wait_param` 声明（Procedure 显式给）。真机运行不在本工位（F3 验收）。

### 1. `meter.batch_read`（kind=case / none / v1，observation_only，wait_param=wait_s）

- 实现：`libs/case_runner/blocks/meter_batch.py`；声明 `data/blocks/meter_batch_read.json`。
  薄编排 simcon 并发抄表任务：`POST /api/simcon/batch_read` 起 job → 轮询
  `GET /batch_read/{job_id}` 到 `finished` 或等满预算。滑窗调度/超时档/否认码人话/
  应答匹配全部复用 simcon 既有交付（REQS-0027 G5 / 0030），零重造。
- 判据出处（CASE-BACKLOG §F0-2）：单表变体=成功原样回装 / 失败回空载荷（报文长度
  域=0，`03_QGDW10376.2_全帧类型.md:1635`）/ 超时档 **99s**（`02-并发抄表.md:89` +
  `expect_rules.json:31-34`；60s/90s/99s 三处并存取 99s，F0-7-4 登记）。观测返回
  行明细（success/deny/timeout/nodata）、否认码列表、deny_breakdown、99s 档位。
- 参数：meter_addr / di（645 为 DI、698 为 OAD）/ expect_timeout（null=按档位）/
  mode（single/batch）/ protocol_type / max_concurrent（1~20，与 109 口径同源）/
  wait_s（轮询总预算）/ addr_mode。

### 2. `gate.multi_frame_110`（case / none / v1，observation_only）

- 实现：`libs/case_runner/blocks/gate.py`；声明 `gate_multi_frame_110.json`。
  多报文打包构帧器 `pack_645_payload`（N 条 CS 合法 645 读帧拼接）→ 经 simcon
  verify 语义化 send（afn=F1 fn=1，`encode_app_data` 补 §4.15 的
  规约类型+保留+长度 2B LE 头）→ 一步下发 + 否认帧监听。
- 判据出处：645 类单帧 ≤13 条（`READMETER_MAX=13`，`02-并发抄表.md:84`）→ 超限
  否认 **110/6E**；打包格式 §4.15 示例帧（报文长度=多条 645 帧拼接总长）；
  DI 缺省 `03300501` 为长报文口径（`123_国网用例_台账.md:74` 行 36 步骤 25——
  「超过 1024 字节不返回」是业务行为不产否认，否认 6E 只看条数闸门，两者都在
  声明出处注明）；整体帧长 ≤2000B（`02-并发抄表.md:17/:80`），超限=配置问题
  （invalid_param，未下发）。观测=是否收到 00H-F2 否认帧 + 否认码；没收到否认帧
  → 原因码 timeout（unknown），绝不折成结论。

### 3. `gate.concurrent_109`（case / none / v1，observation_only）

- 实现：同 `gate.py`。`frame_count`（缺省 21）个「仅下发」步背靠背压队列（无
  expect 不等待）+ 1 个 recv_only 否认监听步；参数名刻意避开能力注入键 `frames`。
- 判据出处：CCO 并发队列上限 20（台体送检 10，`02-并发抄表.md:69,86,143-156`），
  超限否认 **109/6D**（`expect_rules.json:18`）。观测同上（否认码 6D 归步骤 expect）。

### 4. `meter.read_698`（case / none / v1，observation_only，wait_param=wait_s）

- 实现：`libs/case_runner/blocks/meter_698.py`；声明 `meter_read_698.json`。
  02H-F1 透传、**协议类型=03H（698.45）**，payload=完整 698 链路帧（SECURITY
  明文包 GET-RequestNormal，SA=05+表地址，与 STA 真实模板一致）；链路层构帧走
  facade `build("698.45")`、应答判读走 facade `decode(protocol="698.45")`，
  下发/等待复用 `blocks/meter.py` 既有函数，698 编解码不重写第二份。
- 判据出处（F0-5 路径 1 冒烟档）：`123_国网用例_台账.md:73` 行 35 步骤 11-14
  「正确返回 698 数据」；按 0044 §8.1-4 落到「有值且可解」：帧格式合法（HCS/FCS）
  + GET-Response + OAD 回显 + 数值可解，**不比数值真值**。OAD 缺省 `20000201`
  （`141_分钟级采集_用例蒸馏.md:33-40` 645↔698 两套编号对照）。表侧应答走
  simeter 698 GET（`sim_meter/protocol_698.py:242-274`，REQS-0037 已交付）。

### 5. `param.init_01h_f2`（case / reversible / v1，observation_only）

- 实现：`libs/case_runner/blocks/param_init.py`；声明 `param_init_01h_f2.json`
  （cleanup 节写明收场动作：重建档案 archive.add + 组网复核 nwk.topology）。
- KB 定性 kind=case：`145_国网用例_AFN功能_初始化与查询_台账.md:42`（行 47：下发
  → 判确认帧 → 查档案数=0）+ `156 蒸馏卡:135`「初始化三件套先加再清」；同时是
  协议切换三件套第一步（`150_山西用例_台账.md:49` 行 11 步骤 5：01H-F2 → 重建档
  → STA 复位 → 10H-F21）。帧格式：`03_全帧类型.md` §4.2——**01H-F2 无数据单元**，
  所以协议层零参数、不给任何「清一部分」开关（不设隐式默认，11H-F5 教训）；
  上行确认（00H-F1）/否认（00H-F2+否认码）都是观测，「档案清空」判据用
  archive.query/nwk.archive_count 编排（先加再清）。

### 6. 修复：batch.py 698 构帧分流（sim_concentrator 唯一改动处）

- 问题（§F0-5 发现）：`BatchReadJob._read_one` 不分协议一律构 645 读帧，
  `protocol_type=3` 入参只是给 645 报文贴 698 标签——CCO 按协议类型提表地址
  （`01-事务框架…md:222`），标签贴错则 698 任务发的是 645 帧。
- 修复：仅构帧分支一处——`protocol_type==3` 改调新增 `meter_read_698`（完整 698
  链路帧，SECURITY 明文包 GET-RequestNormal，OAD 为抄读项，addr_mode 词表与 645
  一致）；其余协议照旧走 `meter_read_645` 原样。645 构帧行为有回归锚测试钉死。
- 专项单测：`libs/sim_concentrator/test_batch_698_framing.py`（5 条：逐字节基准、
  parse_698_frame 读回验 service/OAD/SA、bcd 口径、双探针证明确实按协议分流、
  645 回归锚）。

### 7. 配套（最小必要触碰，超出「允许触碰」清单的部分在此报备）

- `libs/case_runner/capabilities/simcon.py`：新增 `batch_read_create` /
  `batch_read_job` 两个薄端点方法（仅 HTTP 透传，零领域逻辑）。原因：分层铁律
  禁止块层出现 HTTP 字面量，F0-2 对块的定位就是「POST /batch_read → 轮询
  GET /batch_read/{id}」的壳，唯一合规路径是能力适配层。
- `libs/case_runner/core/report.py`：BLOCK_ACTIONS 增 5 条业务动作映射。原因：
  `test_report.py` 硬门禁「声明块必须与映射集合相等」，新增声明不补映射即红；
  该表是纯数据翻译（D11-2 人话口径），不涉判定逻辑。`test_report.py`、
  `test_gates_0044.py` 计数 16→21 并把 5 新块纳入 AC-1 观测化扫描（新块天生
  observation_only，理应同受扫描）。

### 8. 验证结果（原文）

- `pytest libs/case_runner -q` → **933 passed, 2 skipped**（基线 896 passed；增量
  = 新块单测 27 + 能力层 5 + report/gates 计数迁移）。全部离线，零真机依赖。
- `case_run check` → `计数：块 21 / 事实 11 / 用例 8 / 方案 2；已注册执行体：块 21
  / 事实 11；未发现问题`（problems==[]，rc=0）——5 个新声明全过 loader 门禁
  （observation_only、三字段、provides 合法性、指纹）。
- `pytest libs/sim_concentrator -q` → 281 passed, 4 skipped, **1 failed**；
  该失败（`test_f230_mclt_report.py::test_f230_698_ingest_and_classify`，
  node_addr 反序断言）**先于本工位存在**：把 batch.py 还原到改动前复跑同样失败
  （属于前序工位未提交成果的既有问题，与构帧分流修复无关，未回退、未代改）。

### 9. 单测覆盖对照（判据 → 测试）

| 判据/口径 | 测试（`blocks/test_meter_expand.py`，27 条） |
|---|---|
| §4.15 打包格式逐字节 | `test_pack_payload_plus_semantic_header_matches_415_frame` |
| 14 条 ≤2000B 且逐条可计 | `test_pack_14_messages_stays_under_frame_limit_and_decodes` |
| 110=6E 观测 + 负向期望归步骤 | `test_gate_110_observes_deny_6e_and_step_expect_decides` |
| 无否认帧=timeout（绝不假结论） | `test_gate_110_without_deny_is_timeout_unknown` / `test_gate_109_without_deny_is_timeout_unknown` |
| 109 编排（仅下发步+监听步） | `test_gate_109_fire_and_forget_steps_then_listener` |
| batch 行明细/否认明细/99s 档位 | `test_batch_read_observes_rows_and_deny_breakdown` |
| batch 等满预算=不完整 | `test_batch_read_unfinished_job_is_incomplete_timeout` |
| 698 请求逐字节基准 | `test_build_698_get_request_is_byte_exact` |
| 698 有值且可解 + OAD 回显 | `test_read_698_observes_get_response_and_oad_echo` |
| OAD 不回显/标签贴错如实报 | `test_read_698_wrong_oad_is_observed_not_judged` / `test_read_698_mislabeled_645_payload_is_reported_not_judged` |
| 01H-F2 确认/否认/无回音三态 | `test_init_01h_f2_observes_confirm` / `..._deny` / `..._silence_is_timeout` |
| 声明侧三字段/observation_only/wait_param | `test_new_block_declarations_pass_loader_gates` |

## F2 新用例声明批次（2026-11-24）

**范围**：CASE-BACKLOG §F0 判据落档后的 5 条 Procedure + 1 个方案，全部声明式
（`data/cases/*.json` + `data/case_suites/*.json`），零真机运行（验收留 F3）。
红线逐条兑现：用例 JSON 零帧 hex、零参数计算（CS-1，含 `109/6D` 这类「数字/数字」
写法都按禁则改写为「否认 109，否认码 6D」）；期望全部声明式（`core.expect` 词表）；
等待型步骤显式预算（loader 门禁 `check_procedure_judgement` 强制）；`category` 四维
+ `protocols` 必填（取值 = CASE-BACKLOG 维度）；不动块实现、不动 core/executor、
不动 apps/。

### 1. `meter_di_multi_item`（regression/抄表/CCO，usage=common，1376.2+645）

- 步骤序（9 步）：建档（11H-F1 幂等）→ 查档（10H-F2，0044 §8.1 第1条必备步）→
  组网判定（10H-F21，wait_s=300 写死，本用例唯一长等待判据）→ 绑表（13H，
  expect_timeout=15、wait_s=60）→ 5 步 `meter.read_645` 逐项点抄。
- DI 清单（CASE-BACKLOG 批次 A 落档清单；台架数据集 `libs/sim_meter/meter.py`
  默认寄存器表同款）：00010000（总电能）、02010100（A 相电压）、02010200（B 相
  电压）、02020100（A 相电流）、0203FF00（三相 0201FF00~0204FF00 系列代表项，
  瞬时总有功功率）。
- 每读步期望 = complete + CS 合法 + 非异常应答 + DI 回显 + 数值有值且可解
  （0044 §8.1 第4条，不比真值）；预算 = 单次上限 15s（0042 D8-3 实测单数据项
  往返 2.3~2.6s 的 6 倍余量）+ wait_s=30 写死。
- 前四步是事实链必备（AC-5）：`meter.read_645` 需要 `sta.addr_obtained`（绑表步
  运行期回写）、`meter.addr_probe` 需要 `nwk.joined`（组网步回写）——只写读步
  真机会整条 blocked；`bench_ready` 类 fix.* 步不进用例（方案层前置）。

### 2. `meter_batch_single`（regression/抄表/CCO，usage=normal，1376.2+645）

- 单步 `meter.batch_read`：mode=batch（F1H-F1 每表一帧）、protocol_type=2、
  抄读项 02010100；期望 = complete + 成功数 ≥1（成功原样回装）+ 否认数=0
  （无意外否认；否认明细/否认码列表/行明细进观测备查）。
- `expect_timeout` 不给 = 任务按 expect_rules 并抄档 99s 下发（60s/90s/99s 三口径
  并存取代码口径 99s，F0-7-4 登记）；等待预算 wait_s=130 写死（99s 业务超时 +
  30s 收尾轮询余量）。
- 『同表在抄 111（6F）』不在判据内：simcon 发帧不带地址域，本台架触发不了
  （F0-1 对照发现）。

### 3. `gate_multi_frame_6e`（regression/协议一致性/CCO，usage=normal）

- 单步 `gate.multi_frame_110`：count=14（>上限 13，READMETER_MAX=13，
  `02-并发抄表.md:84`）、DI=03300501（长报文口径，123 台账 行 36 步骤 25）、
  否认等待 5s（expect_rules default 档）。
- **否定式判据样板**（0044 判据归位后）：期望 = complete + `{"op":"eq",
  "field":"否认码","value":"6E"}`；未收到否认帧 → 原因码 timeout → 步骤落
  说不清，绝不折算结论。

### 4. `gate_concurrent_6d`（regression/协议一致性/CCO，usage=normal）

- 单步 `gate.concurrent_109`：frame_count=21（>队列上限 20，台体送检 10，
  `02-并发抄表.md:86、:143`）、否认等待 5s；期望 = complete + 否认码=6D
  （expect_rules.json:18，109=超过最大并发帧数）。

### 5. `meter_698_smoke`（kind=scenario/抄表/CCO，1376.2+698.45）

- 步骤序（用户拍板口径：645↔698 切换必须复位 STA，运行期禁止直改协议）：
  复位 STA（reset.sta，第一步）→ 参数初始化（01H-F2，期望 应答AFNFn=00H-F1）→
  重建档案（archive.add protocol=3，即 698.45）→ 查档（10H-F2）→ 组网判定
  （10H-F21，wait_s=300 钉死块默认）→ 绑表（13H）→ 698 抄读（read_698，
  OAD=20000201 冒烟项）。
- **等待预算单列（≈10min 恢复已单列）**：STA 复位后链路恢复 ≈10 分钟量级
  （REQS §6 风险；0042 变更 9 实测十几分钟量级）→ 绑表 wait_s=600、抄读
  wait_s=600 写死（各步 note 写明「≈10 分钟量级，单列」）；复位步文本写明整轮
  静态最坏 ≈1540 秒（复位 30 + 初始化 5 + 建档查档 10 + 组网 300 + 绑表 600 +
  抄读 600 量级叠加）、默认整轮 600s 上限不够、真机需 `run --timeout` 放大
  （建议 1800 秒档或 0 关闭）；标题与人读步骤写明「重用例，日常回归不进」。
- 698 判据出处：CASE-BACKLOG §F0-5 路径 1 冒烟档（123 台账 行 35 步骤 11 起
  「CCO 端正确返回 698 数据」）+ 0044 §8.1 第4条补齐到有值且可解（帧格式合法
  HCS/FCS + GET-Response + OAD 回显 + 数值可解）；期望 = complete + 帧格式合法
  + OAD 回显 + 数值有值。
- 生命周期：scenario 不进任何方案（0044 §4.4），登记 `UI_SINGLE_RUN_CASES`
  单跑入口；645 用例与本用例不得同轮无复位连续执行（F0-6 设计约束 3）。

### 6. 方案 `meter_expand_smoke`

- case_ids = [meter_di_multi_item, meter_batch_single, gate_multi_frame_6e,
  gate_concurrent_6d]，**stop_on_fail=false**：闸门类失败不应挡后续观测类，
  四条都出结论才好对账；notes 写明与 698 重用例的关系（不同轮、带复位）与
  台架前置（不内置 bench_ready，真机前先单跑）。

### 7. 声明校验测试（`libs/case_runner/test_cases_declaration.py`）

- 清单钉死：EXPECTED_CASES 8→13、EXPECTED_SUITES 2→3、UI_SINGLE_RUN_CASES
  增 `meter_698_smoke`；`test_suites_are_bench_precondition_then_one_meter_case`
  收窄为两条冒烟主线（stop_on_fail=true + 台架前置 notes）。
- 新增 7 条测试：分类四维 + kind 齐备；`meter_di_multi_item`（9 步事实链顺序、
  5 DI 清单、每读步判据五件套、预算 15s/30s）；`meter_batch_single`（mode/
  protocol_type、expect_timeout 缺省不给、wait_s=130、成功数≥1 + 否认数=0、
  否认明细进观测 note）；gate 两条（count=14 / frame_count=21、否定式
  `否认码=6E/6D`、人读预期含「否定式」）；`meter_698_smoke`（kind=scenario、
  复位第一步、protocol=3、预算 {300,600,600}、OAD 20000201、698 三件套期望、
  重用例口径、不进任何方案）；`meter_expand_smoke`（顺序 + stop_on_fail=false
  + notes 关系）；**负向验证**：用真块声明构造「无期望」与「有期望缺预算」两条
  坏 Procedure，`loader.check_procedure_judgement`（check/run/API 同一门禁）
  逐条点名「无判据不入库」「等待预算」，判据齐备对照流程不报。

### 8. 验证结果（原文）

- `PYTHONPATH=apps:libs python3 tools/scripts/case_run.py check` → `声明校验：通过`
  `计数：块 21 / 事实 11 / 用例 13 / 方案 3；已注册执行体：块 21 / 事实 11；
  步骤实例化：12 条用例用 plan，1 条由 blocks 自动展开；未发现问题。`
- `pytest libs/case_runner -q` → **940 passed, 2 skipped**（基线 933 passed；
  增量 = 声明校验 7 条新测试）。全部离线，零真机依赖。
- `pytest apps/workbench/test_cases_api.py -q` → **74 passed**（API 读模型
  kind/category/protocols 字段带出新用例，测试全绿）。
- `case_run show meter_698_smoke` / `show meter_expand_smoke` / `list --cases`
  → 步骤表（step id / 块 id / 关键参数 / on_fail）与人读步骤说明全部可见；
  分类（kind/category/protocols）随 `/api/cases` 读模型可见（CLI show 文本层
  不渲染 category 为既有渲染口径，cli.py 不在本工位允许触碰清单，未改）。

## F3 新用例真机验收批次（2026-11-24）

**范围**：F2 五条新用例的真机验收 + 验收后台架协议状态恢复（645 日常态）。
执行口径：`case_run` CLI（工作台 8790，`WORKBENCH_LOCAL_FULL_ACCESS=1`；simcon
COM4 / CCO COM12 / STA COM18 / simeter COM20）。全程**零介入**（各轮
`interventions[]` 恒空、`self_healed[]` 恒空）；未改 libs/ 与 apps/ 代码、未改
任何 JSON 声明、未重跑刷绿——非 pass verdict 一律如实落档。

### 1. 轮次一览（run_id / verdict / 耗时）

| # | 对象 | run_id | verdict | 耗时 | 说明 |
|---|---|---|---|---|---|
| 0a | `probe --all --readonly`（就绪确认） | 20261124-191801-679065 | ok=false（只读闸跳过 15 个写块，口径内） | — | simcon/simeter/STA 三事实全 yes；档案表 total=1（129078563412 在档）；probe 轮不落 `data/case_runs`（CLI 口径），stdout JSON 存 `/tmp/f3_probe_readonly.json` |
| 0b | `bench_ready` 单跑 | 20261124-192324-c3129c | pass（2/2 步） | 0.013s | 表口本次 open_port 打开后保持 |
| 1 | `meter_expand_smoke` 方案（整体） | 20261124-192350-df3c54 | **blocked**（聚合；四条各自结论见下） | 54.6s | stop_on_fail=false 生效，四条全部执行到底 |
| 1a | └ meter_di_multi_item | 同上 | **pass**（9/9 步） | 43.2s | 建档→查档→组网（300s 预算内 1 节点）→绑表→5 项点抄全过 |
| 1b | └ meter_batch_single | 同上 | **blocked** | 0.005s | 并发任务创建被拒（409 串口忙），见 §3-3 |
| 1c | └ gate_multi_frame_6e | 同上 | **pass** | 2.8s | 观测到否认 6E（否定式判据成立） |
| 1d | └ gate_concurrent_6d | 同上 | **inconclusive** | 8.6s | 5s 预算内未收到否认，说不清，见 §3-4 |
| 2 | `meter_698_smoke`（--timeout 1800） | 20261124-192633-25b98d | **fail**（断点 s4） | 323.0s | s1-s3b 四步 pass，s4 组网 fail，s5/s6 链式跳过，见 §3-2 |
| 3 | 恢复① `param.init_01h_f2`（probe 单块） | 20261124-193600-9a6283 | 观测：应答 00H-F1（确认） | — | probe 对 observation_only 块恒 unknown 不判 pass；stdout 存 `/tmp/f3_recover_1_init.json` |
| 4 | 恢复② `archive.add`（probe 单块，**protocol=2 默认=645 口径**，块声明 `archive_add.json` action.params 默认值；698 侧当时用 3） | 20261124-193612-ee0055 | 观测：11H-F1 建档确认帧已收到 | — | stdout 存 `/tmp/f3_recover_2_add645.json` |
| 5 | 恢复③ `sta_reset` 用例 | 20261124-193622-0335f2 | pass | 6.7s | reboot 后 4.3s 命中启动指纹（本轮为协议恢复复位，第 2 次 STA 复位，均在授权口径） |
| 6 | 恢复证明 `meter_daily` 复验 | 20261124-194628-fb4666 | **pass（6/6 步）** | 21.5s | 复位后等待 ≈10 分钟（19:36:22 复位 → 19:46:28 起跑），零介入 |

关键 result.json：`data/case_runs/20261124-192324-c3129c`、`…-192350-df3c54`、
`…-192633-25b98d`、`…-193622-0335f2`、`…-194628-fb4666`（各目录下 result.json）。

### 2. 观测明细

- **meter_di_multi_item（pass）**：5 项点抄 DI 回显全对、CS 全合法、无异常应答。
  数值：00010000=1234.56、02010100=220.1（与 simeter 默认寄存器表一致）；
  02010200=2.21（表 221.0）、02020100=123.4（表 001.234）、0203FF00='325400'
  （表 00.5432）——判据只验「有值且可解」（F2 声明口径，不比真值），verdict
  不受影响；解码差异见 §3-5。
- **gate_multi_frame_6e（pass）**：14 条/帧打包下发，收到否认帧
  `681000830000400000010002006E3416`，否认码 6E（超过单个 1376.2 帧最大允许的
  电表协议报文条数），期望 `否认码=6E` 成立——110 闸门真机实证。
- **meter_698_smoke（fail）**：s1 复位 4.2s 命中指纹；s2 01H-F2 → 应答 AFNFn
  00H-F1（确认，应答帧 68150083000040000001000100000000000700CC16）；s3
  建档（protocol=3=698.45）写入确认=确认；s3b 查档在档=true；**s4 组网判定
  等满 303.9s/39 次探测，在网从节点数=0（门槛 1，拓扑只有主节点）→ fail**；
  s5 绑表/s6 698 抄读因 on_fail=stop 链式跳过——**698 抄读本体本轮未观测到**。
  补充（复位后 ≈13 分钟只读复核，不判结论）：`probe nwk.archive_count` 档案数
  1（698 档案在档）；`probe nwk.nodes_online` 双路证据都明确为 0（10H-F4 已抄
  =0、信号品质>0=0，块内等 181.3s/23 次）→ **698 态在 300s 与 ≈13 分钟两道
  窗口内均未重新上网**，原因待专项排查（§3-2）。
- **恢复链**：01H-F2（00H-F1 确认）→ archive.add（protocol=2，11H-F1 确认）→
  reset.sta（4.3s 指纹）→ 等 ≈10 分钟 → meter_daily 6/6 pass（建档确认、查档
  在档、组网 1 节点、绑表地址一致、总电能 1234.56、A 相电压 220.1）——
  **台架已回 645 日常态，恢复判据成立**。

### 3. 发现但未修的问题清单（等裁决，均未动 JSON/代码）

1. **698 态组网未恢复（本轮核心发现）**：698 协议态 + 698 档案下，STA 复位后
   300s（s4 钉死块默认）与复位后 ≈13 分钟（只读复核）两道窗口内在网从节点数
   恒为 0；645 侧同流程 ≈10 分钟内恢复（对照组 meter_daily pass）。是 698 态
   组网更慢、协议未真切换、还是档案协议字段/初始化口径问题，需专项排查。
2. **meter_698_smoke 声明结构取舍**：s4 组网预算 300s 钉死小于已知复位恢复
   量级（≈10 分钟），且 on_fail=stop 使 s5/s6 各自 600s 单列预算在组网 fail 时
   必然不执行——「698 抄读」本体在该结构下最易被 s4 挡住。是否调整（s4
   on_fail、或组网等待口径）属声明变更，待领导裁决，本轮未改。
3. **meter_batch_single blocked**：simcon 并发抄表任务创建被拒（409 闸门/会话
   忙；底层 `Could not configure port: (5, 'Input/output error')`）→ 块按
   serial_busy 报 blocked（产品没被证伪，口径正确）；同轮随后 gate_6e 的 simcon
   下发正常，疑为前用例串口未完全释放的瞬时占用。未重跑（红线：不刷绿）。
4. **gate_concurrent_6d 说不清**：21 帧背靠背下发完成，5s 否认预算内无 00H-F2
   （观测字段：下发帧数 21/每帧条数 1/DI 03300501）。是台架态不符还是 109 闸
   门触发条件与 expect_rules.json:18 口径差异，需专项复核。
5. **meter.read_645 数值解码与寄存器表部分 DI 不符**：02010200 读回 2.21
   （表 221.0）、02020100 读回 123.4（表 001.234）、0203FF00 读回 '325400'
   （表 00.5432，形似原始 hex 串）；00010000/02010100 正确。判值出自上游 645
   数据项解析（case_runner 块层只透传 items），且判据不比真值，故本轮各
   verdict 不受影响；建议专项修解析（不在 F3 允许触碰清单，未动）。

### 4. 红线自查

- `interventions[]` / `self_healed[]` 六轮全空；零手动补参、零改档案、零动串口。
- STA 复位共 2 次（698 轮 s1 + 恢复③），均为 645↔698 切换授权口径内的复位；
  除此之外未复位。
- 未重跑刷绿、未改判据/声明/代码；预算参数全部来自声明写死值（本轮未使用
  `--params` 覆盖任何判据参数）。
- 台架收尾状态：645 协议日常态，meter_daily 复验 pass（20261124-194628-fb4666）。

## H 698 链修复执行批次（2026-11-24）

**范围**：按 §G 排查结论（§G-6 缺块清单 + §G-2 H1/H2）补齐 698 链的块与声明；
simeter 补 STA 探帧应答（改台架态，仅此一处）。全程 fake 单测，不做真机运行。

### 1. 新块 `bench.simeter_protocol`（kind=setup，side_effect=reversible）

- 设/读**模拟电表协议模式**（`sim_meter` 即装即采 `jzjc.protocol_type`：00H 透明 /
  02H 645-2007 / 03H 698.45，`libs/sim_meter/api.py:102` 默认 0x02、`:109` 可传 3）。
  写动作 = `POST /api/simeter/jzjc {protocol_type}`（只带该字段，其余 jzjc 配置
  保留）；读动作 = `GET /api/simeter/config` **写后回读确认**（写返回 ok 不直接
  算数，同 fix.simeter_session 开口复核口径）。
- 能力层 `capabilities/simeter.py` 补 `config()` / `set_jzjc(protocol_type=…)`
  两个方法（既有 HTTP 面的既有端点，能力适配器此前没开这两个口）。
- 观测 = 目标协议/切换前模式/切换动作/协议模式（回读）/协议名称/切换确认（仅写
  路径）；判据（协议模式==期望）归 Step 期望（s0 `eq 协议模式=3`）。
- 出处：§G-6 + 用户权威口径「集中器下发的协议类型与模拟电表的协议类型要保持一致」。

### 2. 新块 `sta.meter_protocol`（kind=probe，side_effect=none）

- 只读判读 STA 表口协议日志指纹 `addrmap success,DLT645_CMD,check_procotol:N`
  （§G-1.1；KB 无现成口径），并观测 `try 698 pro` 探帧尝试行；只调
  `ModuleCapability.logs`，不 ensure 会话、不发激励。
- **候选口径未经真机校准**，声明 notes 与块 detail 均写明：N=2 对应 645 的证据 =
  run 20261124-192633 STA 日志行 532/12686（simeter 帧 seq 59/76 佐证 698 探帧
  OAD 40010200 被不应答）；N=3 对应 698 为预期值，待 §G-4 E2 对照实验固化。
- **不设 provides**（未经校准的事实不回写事实缓存）；观测 = 指纹原文/判读协议
  类型/判读协议名称/698探帧尝试/日志行数；窗口无指纹 → incomplete，日志不可读 →
  bench_unreachable。

### 3. `archive.query` 观测增强（非新块）

- 10H-F2 逐节点信息 **D15~D13 通信协议类型** 透出：`blocks/common.py` 新增
  `node_protocol_type()` / `PROTOCOL_TYPE_NAMES`（0 透明/1 645-1997/2 645-2007/
  3 698.45，deep_app.py 同口径；解析器 `adapter_10376/__init__.py:690` 已写入
  desc，块层只取不重解）；`page_table` 的 nodes 逐节点带 `addr/signal/protocol`
  三元；`block_query` 命中地址时观测多带一个字段 **`在档协议类型`**（取不到 →
  缺字段不猜），不改任何既有判据。声明 `archive_query.json` v3（assert 字段表
  补该字段）。
- 出处：§G-6 + `03_QGDW10376.2_全帧类型.md` 10H-F2 节点信息布局。

### 4. `meter_698_smoke` 声明重构（三方一致口径）

- **s0 新增 `bench.simeter_protocol(698)`**：切 698.45 并回读确认（判据归步骤
  期望 `协议模式=3` + `切换确认=true`；kind=setup 不进产品通过率）。
- **s3c 新增档案协议核验步**（第二个 `archive.query` 实例）：期望 `在档协议
  类型=3`——三方一致性的 CCO 档案侧证据（simeter 侧在 s0，STA 表口侧指纹未
  校准不进判据）。
- **s4 组网预算 300s → 900s 写死本步**：§G-2 H1 实测（run 20261124-192633，
  复位+清参后 ≈11 分钟入网、71 次关联全败），900s ≈1.5 倍余量；出处随声明落
  notes。
- 复位步预算（块内 30s）与绑表/抄读预算（各 600s）复核不动；绑表步 notes 登记
  已知风险：STA 表口 698 化后 13H 645 绑表可能失效（山西行 11【12】），缺
  「698 绑表」块已在 §G-6 登记，fail 时用 sta.meter_protocol 留证再下结论。
- 整轮预算文本更新：静态最坏 ≈2150 秒 → 建议 `run --timeout 2400` 档或 0 关闭
  （原 1800 档按旧预算算的）。kind=scenario、不进任何回归方案不变。

### 5. simeter 补 OAD 40010200 应答（`libs/sim_meter` 唯一改动点，动前已读 protocol_698.py 应答结构）

- **改动点**：`libs/sim_meter/meter.py` 的 `_handle_698` 在 43000200（即装即采）
  分支后新增一个 `PROBE_OAD = 40010200` 分支——STA 表口协议探测（addrmap 判 645
  后的 `try 698 pro` 探帧，实测原文 `6817004345AAAAAAAAAAAA10DA5F05011340010200
  00204D16`，GET-RequestNormal 明文）此前被当「未知 OAD」不应答（§G-2 H2 证据
  simeter 帧 seq 59/76），现回 GET-ResponseNormal 带本表通信地址（octet-string，
  6B 线序与 645 13H 读地址应答同源 `brain._addr`），使 STA 探测能识别 698 态
  电表。语义依据：OI 4001=通信地址（698.45 表 A.5 IC8，属性 2 octet-string，
  仓库 OAD 覆盖复核报告 §4001 系列）。应答构造复用既有
  `build_get_normal_response`（SECURITY 包装自动跟随请求），**未动** 645 引擎、
  数据集匹配、未知 OAD 不应答等其余任何分支；`protocol_698.py` 未改（常量落
  meter.py，把 simeter 触碰面压到一个文件）。
- 专项单测 `libs/sim_meter/test_probe_oad.py`（7 条，逐字节基准）：台架实收探帧
  → 黄金帧逐字节一致（黄金帧一次性离线计算后钉死，不用同一构造函数回算）、
  应答结构（85 01 PIID 40010200 01 <addr>）、set_address 跟变、无数据集也答
  （STA 探测发生在建档前——正是 seq 59 的现场条件）、广播/单播同答、SECURITY
  包装跟随、其余 OAD 保持「未知 OAD」不应答。

### 6. 验证

- `pytest libs/case_runner libs/sim_meter -q`：**1109 passed, 2 skipped**（新增：
  blocks/test_protocol.py 21 条 + sim_meter/test_probe_oad.py 7 条 + archive
  协议观测 3 条；门禁同步：test_gates_0044 AC-1 纳入两新块/AC-4 计数 23、
  test_report 翻译表 23 条（F1 先例）、test_cases_declaration 的 698 冒烟声明
  断言按新步骤序重写、无帧 hex/算术门禁通过）。
- `PYTHONPATH=apps:libs python3 tools/scripts/case_run.py check`：**未发现问题**
  （计数：**块 23 / 事实 11 / 用例 13 / 方案 3**；已注册执行体块 23/事实 11）。
- 附带回归：`apps/workbench/test_cases_api.py` 74 passed。

### 7. 红线自查与遗留

- 两个新块 observation_only 零判定、三字段+observation_fields 指纹齐备；等待型
  步骤预算写死在声明（900/600/600，无 CLI 覆盖依赖）；分层铁律未动
  （test_layering.py / test_gates_0044.py 全绿）；未 commit；未做真机运行。
- **遗留（未校准项）**：①`sta.meter_protocol` 的 `check_procotol:N` 判读口径
  未经真机校准（N=3 对应 698 是预期值）——§G-4 E2（simeter 补齐探帧应答后复位
  STA 读 addrmap 行）通过后把指纹固化进 `经验索引.md`；②「698 绑表」块缺失
  （13H 645 口径在 STA 表口 698 化后可能失效）已在 §G-6 登记，属后续派工；
  ③ s4 900s 是按单次 ≈11 分钟证据定的 1.5 倍余量，E1 对照实验可进一步校准。

---

## §E3 真机验收（工位 E3，2026-11-25 08:26-08:5x，只追加）

**范围**：三方一致修复后的 `meter_698_smoke` 真机重跑验收 + `sta.meter_protocol` 指纹校准
+ 台架恢复 645 日常态。执行口径：`case_run` CLI（工作台 8790，`WORKBENCH_LOCAL_FULL_ACCESS=1`）。
全程**零介入**（各轮 `interventions[]` 恒空）；未改 libs/ 与 apps/ 代码、未改任何 JSON 声明、
未重跑刷绿——fail verdict 如实落档。STA 复位共 2 次（主轮 s1 + 恢复 r3），均在本轮已批准口径内。

### 1. 轮次一览（run_id / verdict / 耗时）

| # | 对象 | run_id | verdict | 耗时 | 说明 |
|---|---|---|---|---|---|
| 0a | `probe --all --readonly`（就绪确认） | —（CLI probe 轮不落 `data/case_runs`） | 3 pass / 4 unknown / 16 写块 skipped；事实 6 yes / 5 unknown / 0 no | — | 三端在线；档案表 total=1（129078563412）；`sta.meter_protocol` 窗口内无指纹（未复位，符合预期） |
| 0b | `bench_ready` 单跑 | 20261125-082628-995195 | pass | 0.01s | 预检通过 |
| 1 | `meter_698_smoke`（--timeout 2400） | **20261125-082646-8cff48** | **fail**（断点 s3c，s4 起链式 blocked/skipped） | 20.5s | s0~s3b 五步 pass，s3c fail，s4/s5/s6 skipped；见 §E3-2 |
| 2 | 恢复链（`--case-file` 临时用例 e3_restore_645_daily，kind=setup：simeter 切回 645 → 01H-F2 → 建档 645 → reset.sta） | 20261125-084038-09c515 | pass（4/4 步） | 17.2s | r0 回读协议模式=2（698.45→645-2007）；r1 00H-F1 确认；r2 11H-F1 确认；r3 reboot 后 3.3s 命中启动指纹（08:40:38） |
| 3 | 恢复证明 `meter_daily` 复验 | （见 §E3-4） | | | 复位后等 ≈10 分钟再跑（F2 先例口径） |

### 2. E3 主轮逐步观测（run 20261125-082646-8cff48）

| 步 | 块 | 结论 | 关键观测 |
|---|---|---|---|
| s0 | bench.simeter_protocol(698) | **pass** | 切换前模式=2 → 协议模式=3（698.45）回读确认、切换确认=true（HTTP `/api/simeter/config` 同证 protocol_type:3） |
| s1 | reset.sta | **pass** | reboot 实发 8 字节，3.2s 命中「Run image0, wait a moment, please!」（08:26:52） |
| s2 | param.init_01h_f2 | **pass** | 应答 00H-F1（`68150083000040000001000100000000000700CC16`） |
| s3 | archive.add(protocol=3) | **pass** | 11H-F1 实发帧带 **从节点1通信协议类型=0x03**（`6817004300000000000111010001123456789012031016`，simcon 会话 sc-20261125-082700），CCO 回 00H-F1 确认；幂等预查 10H-F2 total=0（01H-F2 清参生效证据） |
| s3b | archive.query | **pass** | 在档=true，总数=1 已查全 |
| s3c | archive.query（协议核验） | **fail** | 观测「在档协议类型=1」（期望 3）。**定性：测试侧假 fail**——10H-F2 应答节点信息词 0x180F，按仓库安徽报文文档 §5 位段 **D13~D11=通信协议类型=3（698.45，与实发 0x03 一致）**；解码器用 `(w>>12)&0x7`（实读 D12~D10）才得 1。真机两点对照：昨日 645 日常态词 0x50F1（D13~D11=2=645-2007）。**CCO 档案协议存报正确**（CASE-BACKLOG §G-7 已落注记） |
| s4 | nwk.topology(900s) | skipped | s3c fail 链式跳过——900s 预算口径本轮未获观测 |
| s5 | meter.addr_probe(600s) | skipped | 同上 |
| s6 | meter.read_698(600s) | skipped | 同上 |

聚合：pass 5 / fail 1 / skipped 3；`interventions[]=[]`；证据 `data/case_runs/20261125-082646-8cff48/`。

### 3. sta.meter_protocol 指纹校准：**未达成 N=3**（详见 CASE-BACKLOG §G-7）

- simeter 698 态 + 复位重探条件下，STA 指纹仍 `addrmap success,DLT645_CMD,check_procotol:2,addr:129078563412`
  （08:26:58）→ `try 698 pro` 探帧发出 → simeter 实收但按旧代码「未知 OAD 不应答」→ `addrmap affair over`（08:27:00）。
- 根因：**台架进程态旧代码**——持有 8790 与 simeter 引擎的工作台 pid 88119 启动于 2026-11-24 18:11:38，
  早于 `libs/sim_meter/meter.py`（PROBE_OAD 应答）修改时刻 2026-11-24 20:31:34。H 批次修复在文件里、未在进程里。
- N=2 证据链再加一环（698 表态下仍 N=2），但因探帧无应答，**不构成**「698 指纹=N=2」的定性；N=3 校准待工作台
  重启加载新代码后另行执行。

### 4. 台架恢复 645 日常态

- 恢复链 pass（run 20261125-084038-09c515，08:40:38 复位命中指纹）；simeter 回读协议模式=2。
- meter_daily 复验：**pass（6/6 步）**，run 20261125-085126-44445e，40.3s（08:51:26 起跑，复位后 10.8 分钟）：
  档案已存在跳过下发（确认）→ 查档在档=true → 组网在网从节点数=1（探测 1 次、等待 12.2s）→
  绑表地址一致（探测 1 次）→ 645 点抄两路出数（00010000=1234.56kWh / 02010100=220.1V）。
  `interventions[]=[]`。旁证：645 口径档案协议在解码器现位段下读 5、按 D13~D11 正解=2（与 645 建档一致），
  再次佐证 §E3-2 的位段结论。

### 5. E3 收尾判定与未修问题清单

- **主轮 verdict=fail（s3c）维持原判不刷绿**；但根因定性为**测试侧解析器位段偏移**（`adapter_10376/__init__.py:690`
  与 `sim_concentrator/deep_app.py` `_node_from_record` 用 `(w>>12)&0x7`，仓库权威口径
  `docs/_references/protocols/安徽集中器交互报文.md` §5 为 D13~D11），**产品侧（CCO 档案协议存报）正确**：
  11H-F1 实发 0x03 ↔ 10H-F2 词 0x180F 按 D13~D11=3 闭环。
- **`sta.meter_protocol` 指纹：N=3 校准未达成**（台架 simeter 引擎旧代码未加载 H 修复，探帧无应答，
  指纹仍 N=2）——校准注记已落 CASE-BACKLOG §G-7。
- **未修问题清单（全部另行派工）**：
  1. 解码器协议类型位段偏移（D12~D10 → D13~D11），连带 s3c 判据与 simcon 解析 desc、deep_app 口径同步修正；
  2. 工作台进程 pid 88119（2026-11-24 18:11:38 启动）持有 8790，未加载 H 批次 `libs/sim_meter` 修复——
     需重启工作台后重做 E2 校准（另见：`data/runtime/workbench.json` 记 pid 271064/08:20:30，与本机 88119
     不一致，工作台实例管理/看护有第二处待查）；
  3. `meter_698_smoke` s4 组网 900s 预算、s5 绑表、s6 698 抄读三项真机观测仍缺（本轮 s3c 假 fail 链式跳过）；
  4. 「698 绑表」块缺失（§G-6 原登记，未动）。
- 台架收尾状态：**645 协议日常态**（simeter 回读=2、645 档案在档、组网 1 节点、meter_daily 6/6 pass，
  run 20261125-085126-44445e）。未 commit；未改任何代码与声明；run_id 清单：
  20261125-082628-995195（bench_ready pass）/ 20261125-082646-8cff48（主轮 fail@s3c）/
  20261125-084038-09c515（恢复链 pass）/ 20261125-085126-44445e（meter_daily pass）。

---

## I1 位段修复 + 进程刷新批次（工位 I1，2026-11-25 09:2x，只追加）

**范围（三件）**：①10H-F2 协议类型位段修复（解码 desc + 构帧 + metadata desc + 连带注释）；
②`data/runtime/workbench.json` pid 记录核查与写侧小修；③工作台进程刷新（加载 H 批 simeter/meter.py）
+ 三点验证。不做 STA 复位、不跑用例、不发真机激励帧（simeter 注入口属进程内验证）。

### 1. 位段修复（D14~D12 → D13~D11；权威口径：`docs/_references/protocols/安徽集中器交互报文.md` §5）

- **解码 desc**：`libs/parser_lib/adapters/adapter_10376/__init__.py`（10H F2/F5/F6 从节点信息字）：
  `协议类型=(w>>12)&0x7` → `(w>>11)&0x7`；连带 `相线=(w>>8)&0xF` → `(w>>8)&0x7`
  （D11 原与协议位段重叠，按 §5 相位=D10~D8 归位；desc 字段名/键名未动，向后兼容）。
- **构帧（encode）**：同文件 `encode_app_data(0x10, 2/5/6, info{proto,phase})` 上行字拼装：
  `proto<<12` → `<<11`、`phase` 合法域 0~15 → 0~7（与解码位段对称，此前 encode/decode 自相矛盾）。
- **sim_concentrator**：`deep_app.py::_node_from_record` `protocol=(v>>12)&0x7` → `(v>>11)&0x7`、
  `phase=(v>>8)&0xF` → `(v>>8)&0x7` + docstring 位段更正（D15 备用/D14 分钟采集补全）。
- **blocks/common.py**：`node_protocol_type` 本就读解析器 desc、无自算位段；仅更正
  `ITEM_PROTOCOL_TYPE`/docstring/`page_table` 里三处「D15~D13」注释为 D13~D11；
  `blocks/archive.py` 两处注释、`libs/case_runner/data/blocks/archive_query.json` note、
  `test_cases_declaration.py` 注释同步。`meter_698_smoke.json` 声明按红线未动
  （其 s3c 期望=3 在修后解码下成立；其中「D15~D13」字样系注释性文本，留给后续工位顺手更正）。
- **metadata**：`libs/parser_lib/adapters/adapter_10376/metadata/afn_fn.json` 10H-F2/F5/F6
  三条「从节点信息」record desc 更正为「高字节 D15 备用 / D14 支持分钟采集 /
  D13~D11 通信协议类型(3bit) / D10~D8 相位(3bit)」。

### 2. 黄金断言（真帧词逐字节样本）

- 新增 `libs/parser_lib/adapters/adapter_10376/tests/test_10f2_node_info_protocol.py`（4 条）：
  完整 10H-F2 上行帧 hex 一次性离线构造后钉死（不用同一构造函数回算）——
  词 0x180F→协议 3（E3 真帧，run 20261125-082646-8cff48 s3c）、0x50F1→2（E3 真帧 645 日常态）、
  全 0→0（透明）、0x0800→1（645-1997；旧解码会误读 0，判新旧分界样本）；
  逐条断言 desc 的 协议类型/信号品质/中继级别/相线 四值。
- `libs/sim_concentrator/test_deep_app.py`：`test_protocol_phase` 改喂真帧词 0x180F/0x50F1
  （原合成词 0x3000 按 D13~D11 实读=6，语义已不是 698.45）；0x2334→0x1334（保持「协议2/相位3/信号3/中继4」
  语义）；`test_excel_export_protocol_column` 0x3000→0x1800（期望仍 3）。
- `libs/case_runner/blocks/test_archive_nwk.py`：`_raw_archive_table_with_protocols` 编码
  `<<12` → `<<11`（H 批 §G-6 观测增强 3 条测试的期望值 3/2/0 不变即过，覆盖行为不变）。
- 全量回归：`pytest libs/parser_lib libs/sim_concentrator libs/case_runner -q` →
  **1609 passed, 72 skipped, 2 failed**。已知红两条均为既有（先于本工位）：
  ①`test_f230_mclt_report.py::test_f230_698_ingest_and_classify`（DONE §8 已登记 node_addr 反序断言）；
  ②`test_10376_full_coverage.py::test_10f104_up_unsupported`（**新发现既有红**：commit 5c8de1f 同一提交里
  `(0x10,104)` 下行被编入「起始序号2B+数量1B」分支返回 3B，测试却断言 `b""`——提交自带矛盾，
  与未提交成果和本工位改动均无关，安徽报文文档未定义 F104 格式，不在本工位裁决，建议另行派工）。
  其余：`apps/workbench` 全部 33 个测试文件逐一全绿（`test_run_runtime_guard.py` 3 条新增；
  test_cases_api/test_cases_page/test_serial_profile_applier 118 passed；全目录单进程跑超 300s
  前台预算属耗时累积——`test_app` 单文件即 36s——逐文件全绿）；`libs/sim_meter` 146 passed（含探针黄金帧）。

### 3. workbench.json pid 成因与写侧小修

- **成因定性**：`run.py::write_runtime_file` 在 uvicorn 绑定**之前**执行——旧实例 88119 持有 8790 期间，
  两次新启动（pid 271064@08:20:30、293294@08:55:37）都先覆写运行时文件、随后绑定失败退出，
  文件里留下死 pid（E3 观察的「271064≠88119」即此机制；08:55 那次同样如此，293294 稍纵即逝）。
  属「多实例双起 + 先写后绑」的组合，非旧文件未更新一类。
- **小修（写侧，两入口同口径）**：`run.py`/`desktop.py` 各加 `_port_already_served()` 探针——
  8790 已有活监听则拒绝双起（run.py 退出码 2）且**不碰运行时文件**（desktop.py 跳过起服与覆写）；
  新增 `apps/workbench/test_run_runtime_guard.py` 3 条单测（空闲端口 False/有监听 True/拒连 False）。
  重启现场实测：本工位自己的补启因探针命中被拒（exit 2）、运行时文件保持指向真实监听进程——修复行为现场生效。

### 4. 工作台进程刷新与三点验证（全部通过）

- 旧实例 88119（2026-11-24 18:11:38 启动，H 批修复未加载）TERM 停止、端口释放；当前监听进程
  pid **307826**（2026-11-25 09:14:30 起，`WORKBENCH_LOCAL_FULL_ACCESS=1` 等全套环境变量同旧实例，
  0044 DONE 运维备注同款启动方式）。启动瞬间有并发启动方先占端口（同一工作区环境变量），
  本工位补启被新探针拒绝——即当前实例为并发方所起，但加载的正是本工位修后工作区代码
  （探针行为本身即为新代码证据）。
- ① 8790 健康：`GET /api/health` → `{"status":"ok"}`；`ss` 监听 pid=307826 ==
  `data/runtime/workbench.json` pid=307826（started_at 2026-11-25T09:14:31+08:00）。
- ② simeter 探帧（`POST /api/simeter/handle_hex`，0035 交付端点，进程内验证）：
  注入 E3 报告探帧原文 `6817004345AAAAAAAAAAAA10DA5F0501134001020000204D16` →
  应答 `FEFEFEFE681D00C345AAAAAAAAAAAA10858C8501134001020001123456789012CD9716`
  （GET-ResponseNormal 85 01 13 40010200 01 + 表地址 123456789012，note.func=
  「698 表口协议探测读通信地址(40010200)」）。与 H 批钉死的默认地址黄金帧
  `...00010000000000013BC716` 逐字节比对：差异仅 6B 地址域（123456789012）+FCS，
  FCS 经 PPP FCS-16 校验通过——**H 批 PROBE_OAD 应答分支已在进程内生效**。
- ③ `case_run check`：声明校验通过、计数 块23/事实11/用例13/方案3、已注册执行体 块23/事实11、
  **未发现问题**（problems==[]，rc=0；archive_query.json note 更正过 loader 门禁无恙）。
- 台架未动：STA 未复位、无真机激励帧；simeter 引擎回读 `GET /api/simeter/config`：
  addr=123456789012、protocol_type=2（645 日常态，E3 收尾态保持）。

### 5. I1 收尾判定与遗留

- 位段修复闭环：解码/构帧/metadata/注释四处同口径 D13~D11，真帧黄金断言在位；
  E3 §E3-2 未修问题清单第 1 项**完成**。pid 成因已定性并小修（第 2 项完成一半：
  进程已刷新、探帧应答已验证；**N=3 指纹校准仍待真机复位重探**，属下一工位）。
- 遗留（另行派工）：①重跑 `meter_698_smoke` 完整九步（E3 清单第 3 项）；
  ②`test_10f104_up_unsupported` 既有红裁决（见 §2）；③`meter_698_smoke.json` 声明注释里
  「D15~D13」字样更正；④「698 绑表」块缺失（E3 清单第 4 项，未动）。
- 未 commit。

---

## I2 E3 补跑执行批次（工位 I2，2026-11-25 09:44-10:34，只追加）

**范围**：①`meter_698_smoke` 完整九步重跑验收（E3 清单第 3 项）；②`sta.meter_protocol` 指纹 N=3 校准
（E3 清单第 2 项收尾）；③台架恢复 645 日常态。执行口径：`case_run` CLI（工作台 8790 / pid 307826，
`WORKBENCH_LOCAL_FULL_ACCESS=1`）。全程**零介入**（各轮 `interventions[]` 恒空）；未改代码与声明、
未重跑刷绿。STA 复位共 2 次（主轮 s1 + 恢复 r3），均在本轮已批准 E3 口径内。**按红线（复位后 30 分钟
无入网即停手）于 10:33:46 停手**。未 commit。

### 1. 轮次一览（run_id / verdict / 耗时）

| # | 对象 | run_id | verdict | 耗时 | 说明 |
|---|---|---|---|---|---|
| 0a | `probe --all --readonly` | —（probe 轮不落盘） | 块 3 pass / 4 unknown / 16 写块 skipped；事实 6 yes / 0 no / 5 unknown；problems=[] | — | 台架就绪口径与 E3 轮一致 |
| 0b | `bench_ready` 单跑 | 20261125-094438-3873dc | pass（2/2 步） | 0.01s | 预检通过 |
| 1 | `meter_698_smoke`（--timeout 2400） | **20261125-094500-0415f8** | **fail**（断点 s4，s5/s6 链式 skipped） | 928.5s | **s3c 修复后首次真机 pass**；s4 组网 900s 真失败；见 §I2-2 |
| 2 | 恢复链（--case-file e3_restore_645_daily_i2：simeter 切 645 → 01H-F2 → 建档 645 → reset.sta） | 20261125-100346-95c8d1 | pass（4/4 步） | 14.0s | r0 回读=2；r1 00H-F1；r2 确认；r3 reboot 后 3.2s 命中启动指纹（10:03:46） |
| 3 | 恢复证明 `meter_daily` 复验（复位后 10.9 分钟起跑） | 20261125-101450-80324d | **fail**（断点 s2） | 307.8s | s1/s1b pass（645 档案在档）；s2 组网等满 300s 在网从节点数=0；s3~s5 链式 skipped。10:31 查仍无入网 → 10:33:46 红线停手 |

### 2. 主轮逐步观测（run 20261125-094500-0415f8，09:45:00-10:02:20）

| 步 | 块 | 结论 | 关键观测 |
|---|---|---|---|
| s0 | bench.simeter_protocol(698) | pass | 切换前=2 → 协议模式=3（698.45）回读确认、切换确认=true |
| s1 | reset.sta | pass | reboot 后 3.0s 命中「Run image0, wait a moment, please!」（09:45:02） |
| s2 | param.init_01h_f2 | pass | 应答 00H-F1（`68150083000040000001000100000000000700CC16`） |
| s3 | archive.add(protocol=3) | pass | 11H-F1 建档确认，档案地址 129078563412 |
| s3b | archive.query | pass | 在档=true、总数=1 已查全 |
| s3c | archive.query（协议核验） | **pass** | **在档协议类型=3（698.45）**——位段修复（D12~D10→D13~D11）真机生效，上轮假 fail 根因闭环；E3 清单第 1 项就此验收通过 |
| s4 | nwk.topology(900s) | **fail** | 等满 908.7s、只读探测 115 次，在网从节点数=0（节点总数=1 仅主节点）。**真 fail**：STA 日志 09:45:58-10:02:20 共 17 个 `start nwk assoc` 周期，6 次 `nwk assoc failed!`（失败点 `nwk_assoc.c(366) not find proxy!`），0 次成功；STA 全程持续收到本 CCO 信标（nid 39f2ae/src TEI 0001，HPLC+RF 双路，rssi 57~73）。10:01:46 一次试图关联**异网** nid 766b1f（cco_mac 111111222222，5 节点，非本台架）。对照 G 轮（≈11 分钟/71 次后期关联失败后成功）：本轮失败阶段**前移到「找不到代理」**且 900s+ 未恢复 |
| s5 | meter.addr_probe(600s) | skipped | s4 fail 链式跳过——「STA 表口 698 态下 645 13H 绑表是否失效」本轮未获真机观测（§G-6 已登记项维持） |
| s6 | meter.read_698(600s) | skipped | 同上——**698 抄读仍是从未真机观测到的环节**（E3 起连续三轮未达） |

聚合：pass 6 / fail 1 / skipped 2；`interventions[]=[]`、`self_healed[]=[]`；证据 `data/case_runs/20261125-094500-0415f8/`。

### 3. sta.meter_protocol 指纹校准：**N=3 达成**（已落 CASE-BACKLOG §G-7，两条注记）

- 主轮 s1 复位后（09:45:02，simeter 698 态）：09:45:12 `addrmap success,DLT645_CMD,check_procotol:2` →
  `try 698 pro` 探帧 → 09:45:13 simeter 应答（H 批 PROBE_OAD 分支 pid 307826 进程内真机生效）→
  09:45:17 **`check_procotol:3`**（DLT698_CMD）→ `addrmap affair over`。日志
  `data/logs/模块/sta/20261125-094501_*.log` 行 486/490/528-534。
- **语义修正（重要）**：恢复链 r3 复位（10:03:46，simeter 已回 645-2007）后 STA 未走 645 探测、直接以
  DLT698_CMD 发同一探帧（上会话 698 绑表结果驻留），simeter 双协议仍应答 → 10:04:06 再次 N=3（同日志行 14694）。
  即 **simeter 两态都应答探帧，N=3 的语义=「STA 表口侧 698 绑表成立」，不能反推 simeter jzjc 模式**；
  simeter 侧证据以 bench.simeter_protocol 回读为准。校准注记与语义修正在 CASE-BACKLOG §G-7（只追加）。

### 4. 台架恢复 645：配置侧完成、链路侧未恢复（红线停手，如实报告）

- 恢复链 pass（run 20261125-100346-95c8d1）：simeter 回读=2（645-2007）、01H-F2 确认、645 档案重建确认、
  STA 复位指纹命中。收尾回读 `GET /api/simeter/config`：addr=123456789012、version=2007、jzjc.protocol_type=2；
  `GET /api/health` ok。**配置侧 645 日常态成立**。
- `meter_daily` 复验 **fail@s2**（run 20261125-101450-80324d）：复位后 10.9 分钟起跑（对齐 F2/E3 先例），
  s2 等满 300s 在网从节点数=0。STA 日志：10:31:22 出现阶段性进展（`proxy link_HPLC & path succ` →
  `select proxy [num=1]` → 关联请求送达 `nwk send done ind succ`），但 CCO 侧拒绝：
  **`nwk_mmsg.c(198) assoc failed!, result=0x1` → `nwk_assoc.c(408/246) nwk assoc2/assoc not allow`**。
  10:33:46（复位后整 30 分钟）仍无入网 → **按红线停手报告**。
- **与 G 轮的关键差异（有效发现，未修）**：昨夜 G 轮同流程 71 次失败发生在「找到代理之后」的后期关联阶段
  （日志 `proxy link_HPLC & path succ` 反复出现，19:37:52 成功）；今日两次复位（698 态 09:45、645 态 10:03）
  均先卡「not find proxy!」≈25-28 分钟才找到代理、随即被 CCO「not allow」拒绝。**入网阻断点前移 + CCO 拒绝
  关联**，且 645/698 两态同现象——指向 CCO 侧关联准入态（与今日多次 01H-F2 清参/建档/复位的 CCO 状态机，
  或台架环境异网 nid 766b1f 干扰有关），非 simeter/用例侧问题。建议下一工位优先排查 CCO 侧（例如 CCO 重启
  或核查其关联白名单/准入标志）后再跑 meter_daily。

### 5. I2 收尾判定与未修问题清单

- **主轮 verdict=fail（s4 组网）如实维持**；s3c=3 修复闭环为本轮正向成果（E3 清单第 1 项验收通过）。
- **指纹校准完成**（N=3 达成 + 语义修正落 §G-7；E3 清单第 2 项收尾）。
- **未修问题清单（全部如实登记，另行派工）**：
  1. **CCO 拒绝 STA 关联（not allow, result=0x1）+ 前置「找不到代理」≈25-28 分钟**：今日两次复位均未入网，
     恢复证明缺失（meter_daily fail@s2）；与 G 轮失败阶段不同，建议先查 CCO 侧准入态/异网干扰；
  2. `meter_698_smoke` s5（645 13H 绑表在 STA 表口 698 态下是否失效）与 s6（**698 抄读**——项目至今唯一
     未真机观测环节）仍无真机观测：s4 组网连续两轮（G 轮 300s 预算型、本轮 900s 真失败）阻断，需先解问题 1；
  3. 「698 绑表」块缺失（§G-6 原登记，未动）；`meter_698_smoke.json` 声明注释「D15~D13」字样（I1 遗留③）未动；
  4. 异网 nid 766b1f（cco_mac 111111222222，5 节点）在台架 HPLC 环境出现并被 STA 尝试关联——环境因素待查。
- 台架收尾状态：**配置侧 645 日常态**（simeter=645-2007/645 档案在档），**STA 离网**（CCO not allow）——
  链路恢复待 CCO 侧问题 1 处理后由 meter_daily 复验。未 commit。run_id 清单：
  20261125-094438-3873dc（bench_ready pass）/ 20261125-094500-0415f8（主轮 fail@s4，s3c pass）/
  20261125-100346-95c8d1（恢复链 pass）/ 20261125-101450-80324d（meter_daily fail@s2）。

## J 台架恢复执行批次（工位 J，2026-11-25 10:46-11:16，只追加）

**任务**：恢复台架 STA 入网并复验 `meter_daily`（先取证后动作；01H-F1 软复位一次，
20 分钟不入网即停手——禁止断电/二次复位/改档案）。全程零介入（`interventions[]` 恒空）。

### 1. 复位前取证（只读，10:46-10:54）

- **CCO 拒绝时间线**（今日 CCO 侧串口日志缺失：`data/logs/模块/cco/` 最新文件止于
  20261123-162141，当日无新文件；以下均取自 STA 侧日志
  `data/logs/模块/sta/20261125-094501_[sta]_[sta-main-COM18--run-hplc-serial-sta-main].log`——
  拒绝帧是 STA 收到的 CCO 应答，证据方向有效）：
  `nwk_mmsg.c(198) assoc failed!, result=0x1` → `nwk_assoc.c(408/246) nwk assoc2/assoc not allow`
  全日共出现于：09:48:42、09:51:54、10:05:47、10:06:44、10:07:34、10:09:51、10:15:01 …
  （分时统计：09 时 15 次尝试/4 次 not allow；10 时 35 次/18 次；其余为「找不到代理」失败）。
  完整链路上下文（09:48:42 帧序）：找到代理（`proxy link_HPLC/RF & path succ`）→
  关联请求送达（`nwk send done ind succ`）→ CCO 回 `assoc failed!, result=0x1` →
  `not allow` → STA `no net info!`（nid=ffffff）→ `assoc is stop`。
- **异网 nid 766b1f（cco_mac 111111222222）**：本日 STA 日志共 109 次提及（09 时 7 次、
  10 时 101 次，含 bmp 位图值同值撞车）；真实信标接收（`nwk_bcn.c(217) recv bcn from RF,
  NID=0x766b1f`）40 次，全部集中于 **10:01:37**（TEI 0003/0032/0035/0036/0037 共 5 节点，
  RF rssi -72~-82）；昨日日志 20261124-192634 中 730 次、20261124-174538 中 36 次——
  异网至少自 11-24 傍晚起持续在台架 RF 环境存在；复位后（11 时起）接收数=0。
- **simcon 只读快照（10:50:54，run job-48467fcdac734ae4，5 步全部发出）**：
  10H-F1 档案从节点数量=**1**（645 档案在档）；10H-F2/F4 正常应答；
  **10H-F21 节点总数量=1（仅主节点 444444444444，在网从节点=0）**；
  **03H-F1 版本=厂商 `CF` / 芯片 `8F` / 2026-06-16**（与 0041 §10.16 基线一致）；
  补充 10H-F6 主动注册从节点=**0**（无残留注册）。方法注记：5 步背靠背快照中
  F21/03H-F1 首轮无应答（帧发完即收尾），逐条带 `expect` 重发后均正常应答——
  **simcon 连发多查询时末尾帧易丢应答，语义化步骤应逐条带 expect**（本轮实证）。
- **STA 侧**：持续收到本 CCO（nid 39f2ae/TEI 1）HPLC+RF 双路信标（rssi 57~73），
  关联尝试循环「找到代理→被拒→no net info→stop」。
- **对照**：今日 **08:27:38 / 08:43:29** 两次 `nwk assoc ok` 成功入网（日志同文件），
  成功时 STA 带有效 net info（信标括号 nid=39f2ae 而非 ffffff）；10:03 恢复链复位 STA 后
  net info 被清（nid=ffffff），叠加 CCO not allow → 无法重建。

### 2. 01H-F1 软复位 CCO（一次，10:55）

- 判据出处：0041 `RESEARCH-reset.md`（`01H-F1` 复位的是 CCO 自身；帧
  `68 0F 00 43 00 00 00 00 00 01 01 01 00 46 16`；CCO 回 `00H-F1` 确认 + 等待时间，约 7s 重启）。
- 10:55:10.5 经 simcon（mapping_id=simcon，v2 `verification-runs`，job-3ecb6f694fdf4a39）
  下发，10:55:11.815 收到 **00H-F1 确认帧（等待时间=7s）**。
- 复位后回读：03H-F1 前两次超时（10:56:24 / 10:57:14，期间 CCO 上行 AFN=14H 帧——重启后
  bpsCheck 阶段），**10:59:48 第三次成功：厂商 CF / 芯片 8F / 2026-06-16，与复位前一致**；
  10H-F1 档案数=1（硬件初始化不清档案，符合 01H-F1≠01H-F2 语义）。
- **beacon 恢复：11:00:07**（复位后 ≈4min56s）STA 重新收到本 CCO HPLC 信标。

### 3. 复验结果：未入网，按红线停手（11:00-11:15 轮询）

- 轮询（只读 10H-F21，29 次 / 每 ≈31s，j-poll-f21-1..29 + 会话 `sc-20261125-1100*`~`1115*`）：
  **拓扑全程 节点总数=1（仅主节点），在网从节点=0**；`nwk assoc ok` 出现次数=0。
- STA 复位后 11:00-11:16：18 次 `start nwk assoc`，**10 次 not allow**（首次 11:01:15、
  末次 11:15:17——均为找到代理、请求送达后被 CCO `result=0x1` 拒绝，与复位前同码），
  4 次找不到代理，其余在重试节奏中。
- **结论：01H-F1 软复位未解除 CCO 关联准入拒绝**（not allow 跨复位持续）；beacon/本地总线/
  版本/档案均正常恢复，唯独入网准入仍拒。20 分钟窗口（至 11:15:11）内未入网 → 停手，
  **未跑 meter_daily**（无新 case run_id；本轮为 simcon v2 job 面，无 case 运行）。

### 4. 现场处理建议（需用户现场处理，超出台架软件权限）

1. **CCO 断电重上电**（软复位已证不动准入态；断电是唯一未试的 CCO 复位深度）；
2. 排查 **CCO 准入/白名单类配置**（黑名单不随 01H-F2 清除——157 蒸馏卡:35/43；
   本轮 01H-F1 后仍 not allow，指向更深层准入标志或注册表残留）；
3. 排查 **异网 nid 766b1f（cco_mac 111111222222，5 节点）射频干扰源**（11-24 傍晚起存在，
   10:01:37 曾被 STA 尝试关联；复位后 STA 侧接收归零，但源仍在环境里）；
4. 若上述处理后入网，直接复跑 `POST /api/cases/run {"case_ids":["meter_daily"]}` 复验即可
   （台架配置侧 645 日常态未动：simeter=645-2007、645 档案在档、版本 CF/8F/2026-06-16）。

本轮 run_id/job 清单：快照 job-48467fcdac734ae4（5 步发出）/ F21 复核 job-a6cf23e1cb654d68 /
版本复核 job-5161585a174f4a61 / **复位 job-3ecb6f694fdf4a39** / 复位后版本回读
job-785bff6ecd5347e1（通过）+ 两次超时 job-3def8d82286b49d7、job-dfb6509b1e714d05 /
档案数 job-3a558090aceb4dcf / 10H-F6 job-916600dc0d354e1d / 轮询 j-poll-f21-1..29；
simcon 会话 `sc-20261125-105054`~`sc-20261125-111549`（帧证据全量在档）。
未 commit；未改代码；未改档案/配置；`interventions[]=[]`。

## L simeter 探帧应答修复批次（工位 L，2026-11-25，只追加；单一交付物：sim_meter 两处修复 + 黄金帧单测）

对应 CASE-BACKLOG §K 根因①（实锤）：simeter 698 探帧应答 SA=通配回显 → STA 绑表 MAC=AA×6 →
CCO 白名单查无 `ASSOC_RSLT_OUTOFLIST(0x1)`。本批次只动 `libs/sim_meter/`，不 commit、不碰台架。

### 1. 改动点（两处修复）

1. **SA=真实表地址（根因①闭环）**：`libs/sim_meter/protocol_698.py:242` `build_get_normal_response`
   新增可选形参 `sa`（含首字节；缺省 None 时回显 `parsed.addr`，**采集 OAD 应答路径行为不变**）；
   `libs/sim_meter/meter.py:649-663` 探帧（OAD 40010200）应答分支传 `sa=b"\x05" + self._addr`——
   应答帧 SA 由 `45+AA×6`（通配回显）改为 `05+6B 表地址`（单地址头，KB `02_DLT69845_帧格式.md` §2.5：
   bit6~7=00 单地址、bit0~3=长度 nibble=5 → 7B SA；645 线序即人读 hex 直排，与档案/白名单同口径，
   §K.1 已证 645 绑表所得 129078563412 就是这个字序）。数据域仍为 6B 同源地址，布局不变。
2. **协议一致闸（用户口径：三方一致，除非特定测试用例）**：`libs/sim_meter/meter.py:641-648` 探帧
   分支入口加闸——`self.jzjc.protocol_type == 0x02`（645-2007，即 `POST /api/simeter/jzjc`
   protocol_type 落在 `brain.jzjc.protocol_type` 的值）时不答 698 探帧，行为=未知 OAD 同款
   （`HandleResult(None, note)` 空 wire，`note["reason"]="协议不一致(jzjc=645)"`）；03H（698 态）
   应答行为不变；00H（透明）态维持历史应答不动。

### 2. 黄金帧（离线逐字节计算后钉死，一字不动进 test_probe_oad.py）

- 请求（台架实收探帧 seq 59 原文，不变）：`6817004345AAAAAAAAAAAA10DA5F0501134001020000204D16`
- 698 态（jzjc protocol_type=3）、表地址 123456789012 应答黄金帧：
  `FEFEFEFE681D00C305123456789012109D318501134001020001123456789012CD9716`
- 与修复前旧黄金帧 `FEFEFEFE681D00C345AAAAAAAAAAAA10858C85011340010200010000000000013BC716`
  （默认地址 000000000001+SA 回显，工位 H 钉死版，已随行为更新删除）的字段差异：
  **SA 域 `45 AA×6`→`05 12 34 56 78 90 12`（唯一语义差异）；L(1D)/C(C3)/CA(10)/APDU 布局与长度
  不变**（APDU 仍 `85 01 PIID 40010200 01 <6B 地址>`）；HCS/FCS 因 SA 变化重算。
- **FCS 计算方式**：PPP FCS-16（生成多项式 0x8408 反射态、初值 0xFFFF、结果异或 0xFFFF、低字节在
  前），HCS 覆盖 L..CA（`1D 00 C3 05 12 34 56 78 90 12 10` → `9D 31`），FCS 覆盖 L..APDU（→
  `CD 97`），离线经 `parser_lib.adapters.adapter_698._fcs_bytes` 算出；计算口径以台架实收帧
  （§K.4 simeter 应答帧 SA=45+AA×6 版，帧内 FCS=CD97、HCS=858C）同法验算命中做双重校准，并以
  `_check_fcs`（余式 0xF0B8）自校验。注：新帧 FCS=CD97 与 §K.4 台架帧 FCS=CD97 同值为 1/65536
  巧合（两帧字节序列不同——差异位 4-10/12-13——且各自独立通过 FCS 校验），已复核非算错。

### 3. 单测（libs/sim_meter/test_probe_oad.py 重写，7→11 条）

原工位 I1 的 7 条按新行为改写（每条 docstring 标注覆盖行为）：黄金帧逐字节（修复①）、应答结构
SA=`05+表地址`、set_address 后 SA/数据域跟变、数据集空仍应答、45/05 两种请求 SA 同答真实地址、
SECURITY 包装路径 SA 同修、其余 OAD 历史不应答不变。新增协议闸 4 条：jzjc=2 空 wire / jzjc=3 新
黄金帧（同帧两态对照）、闸只作用于探帧分支（43000200 与未知 OAD 语义不变）、jzjc=0 透明态维持
应答（待议项锁定现状）、645 13H 两协议态下应答回归（地址域=原始字节不加 33H）。

### 4. 验证（pytest 数字原文）

- `pytest libs/sim_meter libs/case_runner -q`：**1113 passed, 2 skipped, 1 warning**
  （修复前基线 1109 passed, 2 skipped——净增 = test_probe_oad 7→11 条）。既有已知红两条
  （f230、test_10f104_up_unsupported）位于 sim_concentrator/parser_lib 面，不在本范围命令内，
  本轮未触碰、数量无变化。
- `case_run check`：退出码 0，「声明校验：通过 / 块 23 / 事实 11 / 用例 13 / 方案 3 /
  未发现问题」。
- 复核面：apps/workbench（import simeter 服务）单域全绿（OOM 前已跑到段内无 F；全仓一次跑在
  apps 段被 WSL OOM 杀，属环境内存问题，与本批次无关；sim_meter/case_runner/workbench 三域
  独立跑均绿）。

### 5. 遗留（待议/待台架）

1. **13H 在 698 态的交叉行为本轮未动**（低风险待议）：645 13H 读地址在 jzjc=3 时照常应答
   （回归测试钉住现状）；若将来要"698 态不答 645 帧"属同一三方一致口径的另一半，另立工位。
2. jzjc protocol_type=00H（透明）时探帧仍应答——本轮锁定历史行为，是否也要纳入协议一致闸待议。
3. 真机复跑 meter_698_smoke 九步（§K 遗留动作①，验证 698 态 assoc ok）待台架窗口；
   645 日常态驻留 698 绑表结论影响面（§I2）继续关注。
未 commit；改动仅 `libs/sim_meter/meter.py`、`libs/sim_meter/protocol_698.py`、
`libs/sim_meter/test_probe_oad.py` 及本目录两份落档文档。

## M 台架恢复 + meter_698_smoke 第三轮验收批次（工位 M，2026-11-25 14:02-14:47，只追加）

**单一交付物**：修复后台架恢复 645 + `meter_698_smoke` 第三轮完整验收 + 收尾恢复。STA 复位恰 3 次
（恢复 14:20:20 / 主轮 14:26:07 / 收尾 14:43:40），全部命中组网，未触发 30 分钟红线。全程零介入
（各轮 `interventions[]=[]`）；未改代码与声明、未重跑刷绿（E3 verdict=inconclusive 如实维持）。
未 commit。工作台 pid 517505（`WORKBENCH_LOCAL_FULL_ACCESS=1`，14:07:09 起）。

### 0. 运维备注（先记，不属于产品结论）

1. 开工清理：上一工位遗留的 `pytest apps/workbench` 僵进程（pid 480591，sleep 1.5h、RSS 3.2GB）kill 释放内存。
2. **workbench.json pid 覆写竞态（E3/I1 同款坑再现）**：重启工作台时后台 spawn 重试造成双进程竞速 bind——
   两轮都是「绑定失败方」在 bind 前覆写 runtime 文件留死 pid（511918 / 517516），实听者为新代码进程
   （517505，env 齐全）。M 手工把 `workbench.json.pid` 订正为实听 517505（运行时发现文件，非代码/声明）。
   机制面改进（`write_runtime_file` 移到 bind 成功之后）未动，建议另立工位。
3. 旧 stdout 日志残留「Application shutdown complete」为 511918 绑定失败现场，与 517505 无关。

### 1. 步骤 1：重启加载 L 修复——三点验证全过（14:02-14:12）

1. **pid 一致**：`data/runtime/workbench.json` pid=517505 == `ss` 实听 517505（订正后）。
2. **进程内探帧（L 黄金帧）**：`POST /api/simeter/jzjc {protocol_type:3}` → `POST /api/simeter/handle_hex`
   注入探帧 `6817004345AAAAAAAAAAAA10DA5F0501134001020000204D16` → 应答
   `FEFEFEFE681D00C305123456789012109D318501134001020001123456789012CD9716`
   ——**与 L 黄金帧逐字节一致（SA=`05 123456789012`）**，note.probe_addr=123456789012。
3. **645 闸**：jzjc 切回 2 后同帧注入 → `reply_wire_hex=null`、note.reason=`协议不一致(jzjc=645)`；
   回读 protocol_type=2。台架态净变化为零。

### 2. 步骤 2：恢复 645 日常态（STA 复位 #1）——全绿

- `probe --all --readonly`：bench.simcon/simeter/sta=yes、archive.contains=yes（129078563412，total=1）、
  nwk.nodes_online=0（离网态与 J 轮收尾一致）；`bench_ready` run 20261125-142015-0a2f5f pass。
- `sta_reset` run 20261125-142020-e37018 pass（复位 #1，重启指纹 14:20:27）。
- **L 修复真机生效直接证据（STA 日志 20261125-142021_*.log）**：14:20:33/35/38 STA 发 698 探帧 3 次，
  simeter journal seq 1/2/5 全收、全部**「协议不一致(jzjc=645)，不应答」零 tx**（闸在串口真机生效）→
  14:20:38 `check_procotol:2`（行 558）→ **14:20:46 `set mac addr:129078563412`（行 616，不再是 AA×6）**。
- 关联：14:21:19 start assoc → 14:21:24 proxy link_HPLC succ（找到代理未前移）→ 14:22:34 一次超时型
  `nwk assoc failed`（**全程 0 次 result=0x1 / not allow**）→ **14:23:35 `nwk assoc ok`（行 3400）**，
  复位→入网 **3m08s**。
- `meter_daily` run 20261125-142358-bd26f8 **pass 6/6**（25.8s）：在网从节点数=1、地址一致=true、
  1234.56 kWh / 220.1 V。**K 根因①修复后 645 日常态完整救活**（上轮 I2 同链 fail@s2）。

### 3. 步骤 3：meter_698_smoke 第三轮（STA 复位 #2）——8 pass / 0 fail / 1 unknown

run **20261125-142602-d33ab2**（--timeout 2400，实测 919.9s，verdict=**inconclusive**，
interventions=[]，未重跑）。逐步：

| 步 | 块 | 结论 | 关键观测 |
|---|---|---|---|
| s0 | bench.simeter_protocol(698) | pass | 2→3 回读确认、切换确认=true |
| s1 | reset.sta | pass | 复位 #2，重启指纹 14:26:07（行 7946） |
| s2 | param.init_01h_f2 | pass | 应答 00H-F1 |
| s3 | archive.add(protocol=3) | pass | 11H-F1 确认，档案地址 129078563412 |
| s3b | archive.query | pass | 在档=true、总数=1 查全 |
| s3c | archive.query（协议核验） | pass | **在档协议类型=3（698.45）** |
| s4 | nwk.topology(900s) | **pass** | **在网从节点数=1（等 51.7s / 探测 6 次即达标）**——上轮 900s 等满全败的环节本轮根本性翻转。机理（STA 日志行 8412-8460 + simeter journal seq 24-27）：14:26:14 `try 698 pro` → simeter **实发 L 黄金帧 2 次**（seq 25/27，14:26:14.144/14:26:19.711，串口线上逐字节=修复后黄金帧）→ 14:26:19 STA `check_procotol:3,addr:129078563412`（行 8452-8454，`dlt698.c(642) addrType 0(unicast)`）→ **`set mac addr:129078563412`（行 8460）** → **14:27:09 `nwk assoc ok`（行 9796）**，复位→入网 **62s**，白名单放行（全程 0 次 not allow）。§K 根因①两态（645/698）真机闭环 |
| s5 | meter.addr_probe(600s) | **pass** | 地址一致=true（等 233.0s / 11 次）——**首次真机证明：STA 表口 698 态下 13H 645 绑表仍可用**（上行 02H-F1 内嵌 645 地址域=123456789012，通信协议类型=0x02）；山西台账行 11【12】担心的「STA 不抄另一协议虚拟表」在本台架未发生，§G-6 已登记缺口风险下降但仍未建「698 绑表」块 |
| s6 | meter.read_698(600s) | **unknown（rc=incomplete）** | **698 抄读首次被真机观测**：CCO 02H-F1 透传 698 读 OAD 20000201 请求**经 STA 表口到达 simeter 共 47 帧**（journal 14:31:21.497→14:41:20.204，帧内 SA=`05 123456789012` 寻址正确——产品 698 转发链路 CCO→STA→表口全线打通，历史首次）；但 simeter 698 引擎只实现探帧 OAD 43000200 应答，数据 OAD 20000201 一律「非 43000200，不应答」→ 上行无转发报文 → 观测不完整。与声明 expect 预设一致（「表侧数据集未配置该 OAD 时无应答 → 说不清（timeout）」），**缺口在台架模拟表侧（simeter 未实现数据 OAD GET 应答），非产品链路失败**；698 抄读「有值」观测仍未获得 |

### 4. 步骤 4：收尾恢复 645（STA 复位 #3）——全绿

- 恢复链 `--case-file /tmp/m_restore_645_daily.json`（复刻 I2 恢复链）run **20261125-144324-835e18**
  pass 4/4：r0 切回 645-2007 回读确认（切换前=3）→ r1 01H-F2 00H-F1 → r2 645 建档确认 →
  r3 复位 #3（指纹 14:43:40，行 48528）。
- STA 日志：14:43:51 `check_procotol:2`（行 49108）→ 14:43:58 `set mac addr:129078563412`（行 49166，
  恢复链探帧 journal seq 149-153 同样被闸拦截零 tx）→ **14:44:36 `nwk assoc ok`（行 50432）**，
  复位→入网 **56s**。
- `meter_daily` run **20261125-144459-7ec4d3** **pass 6/6**（61.2s）。终态回读：simeter
  jzjc.protocol_type=2 / addr=123456789012 / version=2007，645 档案在档（在档协议类型=2），STA 在网。
  **无论 E3 结果如何的收尾义务已履行，台架回到 645 日常态。**

### 5. run_id 清单与未修问题清单

run_id：20261125-142015-0a2f5f（bench_ready pass）/ 20261125-142020-e37018（sta_reset，复位#1）/
20261125-142358-bd26f8（meter_daily pass 6/6）/ **20261125-142602-d33ab2（meter_698_smoke 第三轮，
inconclusive 8+0+1）** / 20261125-144324-835e18（恢复链 pass 4/4，复位#3）/
20261125-144459-7ec4d3（meter_daily pass 6/6）。证据目录 `data/case_runs/<run_id>/`。

未修问题（如实登记，另行派工）：
1. **simeter 698 数据 OAD 未实现**（698 引擎仅探帧 43000200 应答）：meter_698_smoke s6 无法获得
   「有值」观测——需补 simeter GET-Response 数据应答（OAD 20000201 等）后台架方可闭环第 9 步；
2. `meter_698_smoke.json` s6 note「表侧 698 GET 应答 simeter 已交付」与运行时不符（实际只交付探帧
   OAD 应答）——声明未动，待声明修订工位核正；
3. simeter 13H 在 698 态照常应答（L 遗留①，本轮 s5 期间实测发生）——本轮恰为 s5 pass 的助力，
   对「三方一致」口径仍是待议另一半；
4. workbench.json pid 覆写竞态（双进程 bind 竞速，绑定失败方覆写 runtime 文件留死 pid）——本轮手工
   订正 2 次，机制面改进未动；
5. STA 复位后首轮关联偶发一次超时型失败（14:22:34，无 result=0x1）——重试节奏内自愈，非阻断。

## N simeter 698 数据 OAD GET 应答批次（工位 N，2026-11-25，只追加；单一交付物：数据 OAD 应答实现 + 黄金帧单测）

对应 §M 未修问题 1（simeter 698 引擎无数据 OAD 应答，s6 47 帧请求全部「未知 OAD」不应答）
与未修问题 ②（meter_698_smoke.json s6 note 不实描述）。只动 `libs/sim_meter/` +
`meter_698_smoke.json`（s6 note/expect 文案）+ 相关测试；不动 case_runner 执行逻辑/apps；
不 commit；不碰台架。

### 1. 改动点（`libs/sim_meter/meter.py` 一处分支 + `protocol_698.py` 仅 docstring）

1. **数据 OAD 注册表**：模块级 `_OAD_DATA_MAP`（4 条，出处逐条留痕）+ A-XDR 标签常量 +
   `_encode_oad_data` 纯函数；`_handle_698` 的 GET-RequestNormal 路径——数据集**无显式条目**
   时查注册表，命中且 jzjc=03H → `build_get_normal_response` 应答 GET-ResponseNormal
   （SECURITY 包装跟随请求；SA=本表真实地址）。值不落静态表：**实时取自 645 寄存器数据集**
   （`_DEFAULT_REGISTERS`/`set_register`，与 645 抄读同源同值，改值应答跟变）。
   数据集显式条目优先于注册表（含 `missing=true` 显式不应答，REQS-0037 注入口径不变）；
   WithList 路径不动（注册表不进 WithList，REQS-0037 逐条 omitted 语义原样）；
   注册表之外 OAD 维持「未知 OAD」不应答。数据集 Normal 命中路径同样改传
   `sa=b"\x05"+self._addr`（SA 纪律与探帧修复同款；WithList 构帧不涉及）。
2. **协议一致闸延伸**（M 交付清单第 2 条「与探帧闸同款」）：注册表分支**只在
   jzjc.protocol_type==0x03（698.45）应答**；645 态（02H）空 wire，note.reason=
   「协议不一致(jzjc=645)」；透明态（00H）同样不应答（reason=协议不一致(jzjc=0)）——
   数据 OAD 历史上从不应答（注册表为新增），无「历史行为可保留」问题，故按
   「只在 698 态生效」字面口径实现（与探帧闸 00H 维持应答的差异已在代码注释与单测说明）。
   既有数据集路径与 43000200 免闸行为不变（test_probe_oad 原语义钉死）。
3. **SECURITY 跟随**：复用 `build_get_normal_response` 既有机制（`parsed.security` 非
   None → `90 00 <len>` 明文包装），无需新代码；M 轮实收请求（SECURITY-Request 明文包
   GET-RequestNormal）与去包装明文请求两条黄金帧对照钉死该行为。

### 2. OAD 映射表（语义/编码/出处）

| OAD | 语义 | 645 同源寄存器 | 编码（A-XDR，大端） | 出处 |
|---|---|---|---|---|
| 20000201 | A相计量电压（OI 2000=电压 IC3，属性 2+索引 01） | 02010100=220.1 V | long-unsigned(0x12) 2B，V 换算-1：2201→`08 99` | KB `用例库/STA/能力域/141_分钟级采集_用例蒸馏.md:33-40`（645 02010100↔698 20000201）+ 仓库 `docs/_archive/2026-08-28/698.45-OAD覆盖/复核报告.md` §3.1 + parser_lib `adapter_698/metadata/oad.json` |
| 20010201 | A相计量电流（OI 2001=电流，属性 2+索引 01） | 02020100=001.234 A | double-long(0x05) 4B，A 换算-3：1234→`00 00 04 D2` | 同上（141 卡 + 复核报告 §3.1/P13 标量口径 + metadata） |
| 20000200 | 电压分相数值组（属性 2=分相数值组 array A/B/C） | 02010100/02010200/02010300 | array(0x01,n=3)×long-unsigned：`01 03 12 08 99 12 08 A2 12 08 96` | 141 卡（三相 645 0201FF00↔698 20000200）+ 复核报告 §3.1 备注 1 |
| 20010200 | 电流分相数值组（属性 2 array） | 02020100/02020200/02020300 | array×double-long：`01 03 05 000004D2 05 000004C7 05 000004DD` | 141 卡 + 复核报告 §3.1（2001=电流 A 换算-3） |

注：20000201/20010201 取标量编码=「属性 2+索引 01 按索引回单值」读法（复核报告 P13：
按索引返回单值则 data_type 应为标量，真机样本待对照——单测黄金帧留待真机留证）。
facade 对 array 值不套标度（`_add_oad_item` 只对 int/float 除 scale）→ 20000200/20010200
经 facade 解出 `[2201,…]` 原值（可解、不折算），属 parser 展示层现状，parser_lib 不在
本工位触碰面（见遗留①）。

### 3. 黄金帧（离线一次性计算后钉死，`libs/sim_meter/test_oad_data_get.py`，测试内不回算）

- 请求=M 轮 simeter journal `sm-20261125-142015--run-hplc-serial-simeter.jsonl` seq 102
  原文（47 帧同款；SECURITY-Request 明文包 GET-RequestNormal：PIID=01、OAD 20000201、
  SA=05 123456789012、CA=00、C=43）：
  `682C0043051234567890120095231000080501012000020100011000000000000000000000000000000000C4CC16`
- 应答黄金帧（jzjc=3、地址 123456789012；90 00 0B 包装 + 85 01 01 20000201 01 + 12 08 99，
  HCS=1C21/FCS=5BD4 经 `_check_fcs` 与 parser_lib facade 双重复核）：
  `FEFEFEFE681D00C305123456789012001C2190000B85010120000201011208995BD416`
- 明文请求（同 OAD 去 SECURITY 包装）应答黄金帧（无 90 包装，SECURITY 跟随请求两态对照）：
  `FEFEFEFE681A00C30512345678901200FA818501012000020101120899126916`
- 两帧经 facade `decode(protocol="698.45")`（read_698 块同一判读路径）解得
  「A相计量电压 220.1 V」——「有值且可解」判据闭环（不比真值，0044 §8.1-4）。

### 4. 单测（`test_oad_data_get.py` 新建 11 条 + `test_probe_oad.py` 2 处适配）

- TestOadDataGetResponses（7 条）：M 轮原文请求黄金帧逐字节（含 note func/value/unit）、
  明文请求黄金帧（包装跟随）、应答结构+facade 判读可解、注册表四 OAD 值/编码逐条钉死、
  set_register 跟变（225.5→08 CF）、set_address 跟变（SA=05+新地址）、数据集条目
  优先级（data_hex 覆盖注册表值；missing=true 显式压制，reason=OAD 缺项）。
- TestOadDataProtocolGate（4 条）：同帧两态对照（jzjc=2 空 wire reason=协议不一致(jzjc=645)
  → jzjc=3 黄金帧）、透明态 00H 不应答、WithList 数据集口径不变（注册表不进 WithList）、
  未注册 OAD（20990201）维持「未知 OAD」不应答。
- `test_probe_oad.py`：`test_other_oads_keep_historic_no_reply` 的 20000201 子断言改为
  20990201（20000201 在 698 态已由注册表应答，属本批次交付行为，黄金断言移交新文件）；
  `test_gate_scoped_to_probe_only` docstring 更新（闸延伸到注册表分支，43000200 免闸与
  未知 OAD 原语义断言不变）。

### 5. 验证结果（pytest 数字原文）

- `pytest libs/sim_meter libs/case_runner -q` → **1124 passed, 2 skipped, 1 warning**
  （L 收尾基线 1113 passed, 2 skipped，净增 = test_oad_data_get.py 11 条）。已知红两条
  （f230、test_10f104_up_unsupported）位于 sim_concentrator/parser_lib 面，不在本命令
  范围，未触碰、数量无变化。
- `PYTHONPATH=apps:libs python3 tools/scripts/case_run.py check` → rc=0，「声明校验：通过 /
  块 23 / 事实 11 / 用例 13 / 方案 3 / 未发现问题」。
- `pytest libs/sim_meter -q` → 158 passed（本域单独全绿；本批次前为 147）。
- 落坑记录：s6 note 初稿把四个 OAD 写成斜杠连排（20000201/…），命中声明门禁
  ARITHMETIC_RE「数字/数字」禁则（F2 红线同款），已改顿号连排——与 0043 F2「109/6D 须改写
  为『否认 109，否认码 6D』」同一禁则的再现。

### 6. meter_698_smoke.json 修正（§M 未修问题 ②）

- s6 note：「表侧 698 GET 应答 simeter 已交付」→ 与本实现一致：注册表四项
  （20000201、20010201、20000200、20010200，值取自 645 寄存器数据集同源、仅 simeter
  jzjc=698 态应答，REQS-0043 工位 N 交付，并注明 M 轮实际只有探帧 OAD 应答）。
- steps[8].expect 同句修正：「表侧数据集未配置该 OAD 时无应答 → 说不清」→「表侧 20000201
  已由 simeter 数据 OAD 注册表默认应答，无应答归因链路/转发，不再是『数据集未配置』」
  （同一不实描述根，随 ② 一并修正）。声明校验测试未钉 note/expect 文案，零改动；
  test_cases_carry_no_frame_hex_or_arithmetic 门禁复跑通过。

### 7. 遗留（另行派工/待台架）

1. facade 对 array 数据不套标度（20000200/20010200 解出原值不折算）——parser_lib
   `adapter_698._add_oad_item` 展示层，不在本工位触碰面；s6 用 20000201 标量不受影响。
2. 20000201/20010201 标量编码的复核报告 P13 疑点（data_type array vs 标量）——真机复跑
   meter_698_smoke s6 时以真实应答样本对照留证（黄金帧可按需换版）。
3. 数据集路径（REQS-0037）在 jzjc=645/透明态仍应答（显式配置面免闸，与 43000200 同理）——
   若要纳入协议一致闸属 L 遗留②同族，另立工位裁决。
4. 真机复跑 meter_698_smoke 九步验证 s6「有值」观测（§M 未修问题 1 的台架侧闭环），待台架窗口。
未 commit；改动仅 `libs/sim_meter/meter.py`、`libs/sim_meter/protocol_698.py`（docstring）、
`libs/sim_meter/test_oad_data_get.py`（新）、`libs/sim_meter/test_probe_oad.py`（2 处适配）、
`libs/case_runner/data/cases/meter_698_smoke.json`（s6 文案）及本文档。

## O 698 抄读出数验证（meter_698_smoke 第四轮）+ 台架恢复 645 收尾批次（工位 O，2026-11-25 15:17-16:05，只追加）

**单一交付物**：N 批 simeter 数据 OAD GET 应答加载后台架的 meter_698_smoke 完整重跑（698 抄读出数验证）+ 台架恢复 645 收尾。STA 复位恰 2 次（主轮 s1 15:25:32 Run image0 / 收尾 r3 15:33:42 Run image0），均命中组网（214s / 594s），未触发 30 分钟红线（红线时刻 16:03:42/16:03:45）。全程零介入（各轮 `interventions[]=[]`，无一次补参/改判/盯日志改判）；未改代码与声明（唯一新建物=`/tmp/o_restore_645_daily.json` 临时用例文件，复刻 M/I2 恢复链）；未重跑刷绿（唯一 fail 轮 20261125-153433-792657 如实留档）；未 commit。工作台 pid 576835（`WORKBENCH_LOCAL_FULL_ACCESS=1`，15:23:43 起，经 systemd 单元 `zzt-workbench.service` 重启）。

### 1. 步骤 1：重启加载 N 代码——四点验证全过（15:17-15:24）

1. **pid 一致**：旧 pid 517505（14:07 起，N 前代码）经 `systemctl restart zzt-workbench.service` 一次到位 → 新 pid **576835**；`data/runtime/workbench.json` pid=576835 == `ss` 实听 576835——**本批无 pid 覆写竞态再现**（M 未修问题 4 的机制面改进仍未动，systemd 单参重启路径天然无双进程竞速窗口）。
2. **N 黄金帧进（jzjc=3）**：`POST /api/simeter/jzjc {protocol_type:3}` → `POST /api/simeter/handle_hex` 注入 N 请求黄金帧 → 应答 `FEFEFEFE681D00C305123456789012001C2190000B85010120000201011208995BD416`，**与 N 黄金帧逐字节一致**（`90 00 0B` SECURITY 包装 + `85 01 01 20000201 01` + `12 08 99`=220.1；note.value=220.1 / unit=V / func=「698 数据读点(A相计量电压 20000201)」/ security=true）。
3. **协议闸拦截（jzjc=2）**：jzjc 切回 2 后同帧注入 → `reply_hex=null`、`reply_wire_hex=null`（空 wire）、note.reason=`协议不一致(jzjc=645)`。
4. **态净变化为零**：jzjc protocol_type=2（seq=1 不变）、oad effective={} 不变、addr=123456789012 / version=2007 / registers 全部不变。bench_ready run 20261125-152457-7c1aad pass 重开 simcon/simeter 会话（session sm-20261125-152457）。

### 2. 步骤 2：meter_698_smoke 第四轮（STA 复位 #1）——9 pass / 0 fail / 0 unknown，**verdict=pass（历史首次全绿）**

run **20261125-152524-6e0b55**（--timeout 2400，实测 278.3s，interventions=[]，一次跑完未重跑）。逐步：

| 步 | 块 | 结论 | 关键观测 |
|---|---|---|---|
| s0 | bench.simeter_protocol(698) | pass | 2→3 回读确认（切换前=2） |
| s1 | reset.sta | pass | 复位 #1：15:25:28 TX reboot → 15:25:32 Run image0（行 100） |
| s2 | param.init_01h_f2 | pass | 00H-F1 确认 |
| s3 | archive.add(protocol=3) | pass | 11H-F1 确认，档案地址 129078563412 |
| s3b | archive.query | pass | 在档=true、total=1 查全 |
| s3c | archive.query（协议核验） | pass | **在档协议类型=3（698.45）** |
| s4 | nwk.topology(900s) | pass | 在网从节点数=1（等 201.3s / 探测 20 次）。复位→入网 **214s**（15:25:32 → 15:29:06 `nwk assoc ok` 行 3732）——对照 M 轮 51.7s 等待/62s 入网：同属「CCO 网络重建期」抖动带（M 三轮 3m08s/62s/56s、本批 214s），900s 预算内达标；15:27:38 一次超时型 `nwk assoc failed` 重试自愈，全程 0 次 result=0x1 / not allow |
| s5 | meter.addr_probe(600s) | pass | 地址一致=true（等 35.1s / 3 次；M 轮 233.0s/11 次）——698 表口态 13H 645 绑表仍可用（上行 02H-F1 通信协议类型=0x02） |
| s6 | meter.read_698(600s) | **pass（历史首次出数）** | **698 抄读真机闭环**：等 5.1s / 1 次探测即得。帧格式合法=true（HCS/FCS 校验通过）、应答控制域 C=C3、应答 APDU=90（SECURITY-Response，块 detail 如实报「不是 GET-Response(85)」——GET-ResponseNormal `85 01` 在 `90 00 0B` 包装内层；块判据 帧格式合法/OAD回显/数值 三项全真故 pass）、OAD回显=true、**数值=220.1 / 单位=V / 数据项=A相计量电压**。内层 698 帧 `681D00C305123456789012001C2190000B85010120000201011208995BD416` 与 N 黄金帧逐字节一致 |

**s6 全链路逐字节闭环（simeter journal `sm-20261125-152457--run-hplc-serial-simeter.jsonl` seq 19/20 + STA 日志 20261125-152524_*.log）**：
- 15:30:00.235 seq 19 rx：请求帧 `682C0043…C4CC16` 与 M 轮 journal seq 102（47 帧同款）逐字节相同（SECURITY-Request 包装 GET-RequestNormal OAD 20000201，SA=`05 123456789012`）——产品 698 转发链路 CCO→STA→表口再次原样送达；
- 15:30:00.303 seq 20 tx（间隔 68ms）：**simeter 串口实发 N 黄金帧全帧**（含 FE×4 前导 `FEFEFEFE681D00C3…5BD416`），note.value=220.1 V——**N 批黄金帧在真机线上复现**；
- 上行经 CCO 02H-F1 → case_runner facade 判读「A相计量电压 220.1 V」→ has_value=true。**§M 未修问题 1（simeter 698 数据 OAD 未实现）就此台架闭环；「698 抄读有值」观测历史首次获得**（前三轮 s6 均无数据：第二/三轮 47 帧请求零应答，首轮 s3c 链断）。
- 探帧闸 698 态侧证（N/L 修复真机链复现）：15:25:38.475/15:25:44.120 simeter 收探帧（seq 3/5）→ seq 4/6 实发 L 黄金帧 → STA 15:25:44 `check_procotol:3` + `set mac addr:129078563412`（行 584/592，unicast）。

### 3. 步骤 3：收尾恢复 645（STA 复位 #2）——恢复链 pass 4/4；meter_daily 首验 fail@s2（预算窗口落在恢复期内，如实留档）；组网后复验 pass 6/6

- 恢复链 run **20261125-153316-987fd9**（--case-file /tmp/o_restore_645_daily.json）pass 4/4：r0 切回 645-2007 回读确认（切换前=3）→ r1 01H-F2 00H-F1 → r2 645 建档确认 → r3 复位 #2（15:33:38 TX reboot → 15:33:42 Run image0 行 11292）。
- **协议闸 645 态真机侧证**：15:33:47-15:33:53 STA `try 698 pro` 探帧 3 次（journal seq 21/22/25）全部 reason=`协议不一致(jzjc=645)` **零 tx** → STA 终态 645（15:34:01 `set mac addr:129078563412` 行 11936，645 探测路径；与 M 收尾轮同构）。
- meter_daily run **20261125-153433-792657** **fail@s2**（317.0s，pass 2 + fail 1 + skipped 3，interventions=[]）：档案/协议侧全对（已存在跳过下发、在档协议类型=2），但 nwk.topology 等 304.7s / 26 次在网从节点数=0——**复位+清参后本轮组网实测 594s（15:33:42 → 15:43:36 `nwk assoc ok` 行 24234），meter_daily 的 300s 预算窗口落在恢复期内**（G-1 H1 同族新样本：≈11 分钟量级恢复带，本轮抽到慢端；M 收尾轮 56s 抽到快端）。fail 如实留档未重跑刷绿；期间仅只读轮询 STA 日志等入网，零介入。
- STA 组网由自身重试节奏达成（≈8s 节拍 assoc req，15:42:16 一次超时型失败行 23770，全程 0 次 result=0x1 / not allow），**复位→入网 594s**，30 分钟红线未触发。
- 入网后 meter_daily 复验 run **20261125-160211-d1c542 pass 6/6**（31.5s）：已存在跳过下发 → 在档协议类型=2 → 在网从节点数=1（8.4s 首探即中）→ 地址一致=true → 1234.56 kWh → 220.1 V。终态回读：simeter jzjc.protocol_type=2 / addr=123456789012 / version=2007，STA 在网（10H-F21 双节点含主节点）。**无论 E3 结果如何的收尾义务已履行，台架回到 645 日常态。**

### 4. run_id 清单与未修问题清单

run_id：20261125-152457-7c1aad（bench_ready pass）/ **20261125-152524-6e0b55（meter_698_smoke 第四轮，pass 9/9，历史首次全绿）** / 20261125-153316-987fd9（恢复链 pass 4/4，复位#2）/ 20261125-153433-792657（meter_daily fail@s2，如实留档）/ 20261125-160211-d1c542（meter_daily 复验 pass 6/6）。证据目录 `data/case_runs/<run_id>/`；simeter journal `data/logs/simeter/sm-20261125-152457--run-hplc-serial-simeter.jsonl`（seq 3-6 探帧、19/20 数据读、21-25 闸拦截）；STA 日志 `data/logs/模块/sta/20261125-152524_[sta]_[sta-main-COM18--run-hplc-serial-sta-main].log`。

未修问题（如实登记，另行派工）：
1. **meter_daily 300s 组网预算 < 复位+清参恢复实测 594s**（本轮 fail@s2 实锤新增样本；G-1 H1 同族。meter_daily 声明口径「不复位，日常」，其 300s 预算在「恢复链复位 → 紧跟 meter_daily」组合下会落在恢复窗口内——恢复链与 meter_daily 的预算衔接口径待裁决；声明未动）；
2. s6 块「应答APDU」观测只报外层 90（SECURITY-Response），未标明 `85 01` GET-ResponseNormal 在 `90 00 0B` 包装内层（detail 有如实提示但字段语义偏外层；判据三项全真判 pass 正确，纯展示层小瑕疵，块/parser 展示面另行派工）；
3. N 遗留顺延：facade 对 array 值不套标度（20000200/20010200）；数据集路径（REQS-0037）在 645/透明态免闸裁决。**N 遗留②（20000201 标量编码 P13 疑点）已获真机对照素材**：本轮 seq 20 实发帧=黄金帧且经 CCO→STA→facade 全链解出 220.1 V——标量编码被产品链路正常消费，P13 疑点按「真机样本可解」收窄（黄金帧暂无需换版）；
4. L 遗留①/「三方一致」另一半（simeter 13H 在 698 态照常应答）未动——本轮 s5 复证 698 态 13H 645 绑表可用（35.1s），影响面继续收窄；E3 第三轮 M 未修问题 1/2（simeter 数据 OAD 未实现、s6 note 不实）已由 N+O 两批闭环，不再列。

## P 技能同步批次（工位 P，2026-11-25，只追加）

**单一交付物**：ai-control-plane 技能包（入口 + wb-* 子技能）黑盒测试相关内容与 2026-11-25 真实状态对齐，
`tools/scripts/verify_skills.py` 校验通过。技能库在仓库外（`/root/.agents/skills/` 为规范源
`/home/02-skill-fc/skills/shared/hardware-in-the-loop/` 的符号链接，**单一副本无同步问题**），不经 git 提交；
工作区代码零改动，事实源 = REQS-0044 v3/v4 + 本文件 F1/F2/H/L/M/N/O 段 + CASE-BACKLOG §F0/§G/§K。

### 1. 四件更新

1. **`wb-casetest/SKILL.md` 1.1.0 → 2.0.0（主体重写）**：
   - 模型正名：Run→Suite→**Procedure**→Step→Block（旧「Case」正名 Procedure）；**判据归位**（块只观测、期望在 Step、
     Procedure 聚合四态、无判据不入库）；kind=setup/regression/scenario、场景=Procedure 第二生命周期；
     期望值口径=「有值且可解」不比真值。
   - 现状清单**指索引不抄死数字**：一切以 `case_run list --json` 输出为准（当前参考 块23/事实11/用例13/方案3 随批次漂移）。
   - 用例地图：13 条 id + 一句话用途（bench_ready setup 前置 / meter_daily 6 步 / meter_first_join 7 步 /
     meter_di_multi_item / meter_batch_single / gate_multi_frame_6e / gate_concurrent_6d /
     **meter_698_smoke（scenario 重用例，--timeout 2400）** / archive_ready / archive_absent（同一块两种期望的判据归位样板）/
     archive_multi / nwk_join / sta_reset）。
   - 报表契约：run.report.rows[] 九字段（group/subgroup/name/kind/steps_text/verdict/reason/evidence_paths/
     counts_as_product）+ summary（分母不含 setup 行）；用户层零块名。
   - API 增量：GET/PUT `/api/cases/defaults`（键白名单改默认表地址）、`GET /api/cases/runs?limit=`（历史索引）、
     CaseView.kind。
   - **协议切换业务规则（用户拍板口径，重点节）**：645↔698 必须复位 STA（运行期禁直改协议，唯一例外=专用协议切换用例）；
     三方一致=CCO 档案 protocol / 电表 jzjc / STA 表口探帧三者同态；698 组网预算 900s（恢复抖动带实测 62s~11 分钟）
     + 整轮 timeout 2400；复位后 30 分钟不入网停手；同一 STA 的 645/698 用例不得同轮无复位连续执行。
   - **经验教训节（新增四条，真机踩过勿回退）**：①探帧应答 SA 必须真实表地址（AA×6 回显→STA MAC=AA×6→白名单
     OUTOFLIST(0x1)，已修+黄金帧钉死）；②档案地址口径=reverse6(电表 645 人读)、645 链与 698 链同口径；
     ③meter_daily 300s 组网预算 vs 复位恢复期实测 594s 的衔接缺口（预算型假 FAIL 样本已留档）；
     ④109 闸门单表台架不可复刻（KB 需 100 个不同地址堆队列，同表去重堆不起来 → 说不清不判结论）。
   - `scripts/` 兜底框架（原 §8）保留为 §11；front-matter description 触发纪律一字未动。
2. **`wb-simeter/SKILL.md` 1.2.0 → 1.3.0（增量）**：新增 §4.3「698 探帧与数据 OAD 应答（工位 L/N）」——
   jzjc 协议一致闸（645 态不答 698 探帧与数据 OAD）、探帧应答 SA=`05`+6B 真实地址（AA×6 教训与黄金帧）、
   数据 OAD 注册表 4 项（20000201↔02010100 220.1 V 等，值实时取 645 寄存器数据集）、set_register/config 跟变；
   `/jzjc` 行补 protocol_type 三态语义（00H 透明/02H 645-2007/03H 698.45）；§4.2 补「改 libs 必须重启工作台」。
3. **`ai-control-plane/references/api-contract.md` §7.1 + `features.md` §6.1（新增 `/api/cases/*` 段）**：
   全端点面 + 新端点契约——GET/PUT `/api/cases/defaults`（白名单=config 覆盖文件已存在键，未知键 422，
   表地址 12 位校验）、`GET /api/cases/runs?limit=N`（缺省 10 上限 50 超界夹取、坏 result.json 轮次跳过）、
   CaseView.kind 字段、run.report 九字段契约（语义只加不改）、POST /run 202/200 双态（accepted/blocked 不建 job）。
   两文件头部按既有格式追加更新行（日期+出处）。
4. **`ai-control-plane/使用经验/698态STA入网被拒排查.md`（新增）**：《698 态 STA 入网被拒（AA×6 SA 回显）排查全过程》——
   现象（result=0x1 反复 not allow）/ 取证路径（STA 日志 addrmap/set mac addr → simeter journal seq 应答帧 SA=
   45+AA×6 → KB 码表 ASSOC_RSLT_OUTOFLIST）/ 三方比对与根因实锤 / 修复（L 工位两处+黄金帧）/ 预防六条
   （含「先看 set mac addr 是不是 AA×6」口诀与「改 libs 必须重启工作台」）。

### 2. 入口与校验

- `ai-control-plane/SKILL.md` version 4.0.0 → **4.1.0**；§1 表微调两行一句话能力：
  wb-casetest「…四态结论 · 用例库盘点」→「…四态结论 · Procedure 判据归位」；
  wb-simeter「模拟电表 645/698 从站调测」→「模拟电表 645/698 从站调测（含 698 探帧/数据 OAD 应答）」。
- `python3 tools/scripts/verify_skills.py`（工作区内）结果原文（默认与 --strict 同）：
  `成员 9 个（ai-control-plane、wb-appframe、wb-base、wb-casetest、wb-flash、wb-listener、wb-serial、wb-simcon、wb-simeter）；错误 0，警告 0。结果：PASS`（退出码 0）。
  该脚本未定义多副本同步规则；实查技能索引为符号链接单副本，无同步动作。

### 3. 红线自查

- wb-casetest front-matter description 触发纪律一字未动；入口 §1 表「何时加载」列未动。
- 计数类数字全部「指索引」或标注「以 list 输出为准」；版本历史行按两文件既有头部格式追加。
- 未改工作区任何代码/声明；本批次唯一落档 = 本节（只追加）+ 仓库外技能包四件 + 入口微调。
