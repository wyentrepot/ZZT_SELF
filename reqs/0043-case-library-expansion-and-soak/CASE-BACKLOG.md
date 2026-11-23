# 0043 候选用例清单与可测性标注

> 依据：REQS-0043 v1（CL-1/CL-2）+ CASE-LIBRARY-taxonomy.md（0042）
> 可测性判定日期：2026-11-23；台架：单 STA + 单表
> 分类维度（`usage/domain/scene/region/protocols`）**一律取 0042 taxonomy §1 的取值域**，本文件不另立体系；只把 taxonomy 的 `usage`（smoke/daily/acceptance/probe/forbidden）落到 0043 用的三档「常用度」（common/normal/rare）。
> 列值口径：`设备域` 用 taxonomy §1.2 小写取值 **cco / sta / ecu / bench**（= 需求里写的 CCO / STA / ECU / bench）；`常用度` 三档 **全部是我方定义**（taxonomy §1.0 实测：库内无「常用/优先级」字段，STA 339 行 I 列 0 行有值、CCO 241 行 H/I/J/K 全空、`grep 常用` 零命中）；`可测性` 只取 `可测 / 不可测`。
> 出处列写法：`文件 §小节` 或 `文件:行`；知识库路径相对 `/01-workfile-ai/04-obsidian/obsidian`，仓库路径相对 `/01-workfile-ai/01-zzt/ZZT_SELF`。**每条都有出处；查不到的一律进 §4，不编。**

---

## 1. 判定标尺（本环境能力清单）

### 1.1 现有块（16）

判定基准 = `libs/case_runner/data/blocks/*.json`（16 个声明文件）+ `libs/case_runner/blocks/*.py` 实现。「是否需要写动作」= 该块会不会改被测对象/台架状态（只读块可以随时重跑，写块要考虑顺序与幂等）。

| # | 块 id | 一句话能力（判据） | 写动作 |
|---|---|---|---|
| 1 | `archive.add` | `11H-F1` 建档 + `10H-F2` 回查双判据；已存在 → pass（幂等） | **是**（改档案） |
| 2 | `archive.del` | `11H-F2` 删档 + `10H-F2` 反证查不到；不存在 → pass | **是**（改档案） |
| 3 | `archive.query` | `10H-F2` 查档案表，按 `meter_addr` / `expect_total` / `expect_empty` 判（三者都不给 → unknown） | 否（只读） |
| 4 | `fix.simcon_session` | 台架前置：`/api/simcon/status` 可达即台架在位（不开会话，只影响 frames 读取） | 否（探测） |
| 5 | `fix.simeter_session` | 台架前置：模拟电表表口**必须真打开**（没开就 `POST /api/simeter/open`，开完不关） | **是**（开串口） |
| 6 | `meter.addr_probe` | `02H-F1` 透传 645 `13H` 读表地址，比内层应答地址域；`addr_mode` = `meter`（本台可用）/`broadcast`（全 FE，本台 CCO 回空单元） | 否（只读透传） |
| 7 | `meter.brain_local` | 表端脑本地自检（simeter `handle_hex`，不占 CCO 通路）：正帧判 `reply_hex`+DI 回显+`note.value` 非空；反帧（地址不符）**判不应答即 pass** | 半（向表端脑发帧，不改 CCO 状态） |
| 8 | `meter.read_645` | `02H-F1` 透传 645 `11H`，判 CS 合法 + DI 回显 + 数值可解；可给 `expect_value`；`wait_s>0` 才重发 | 否（只读透传） |
| 9 | `nwk.active_registration` | `11H-F5` 激活从节点主动注册（搜表），判 `00H-F1` 确认 —— **`usage=forbidden`，禁止进用例** | **是**（临时关白名单过滤） |
| 10 | `nwk.archive_count` | `10H-F1` 查档案从节点数量，判 `≥ min`（**档案数 ≠ 真在网数**） | 否（只读） |
| 11 | `nwk.nodes_online` | `10H-F4` 已抄从节点数量 + `10H-F2` 逐节点信号品质两路交叉，判「两路任一 ≥ min」；等满 180s 且两路都 0 → fail | 否（只读） |
| 12 | `nwk.onnet_count` | `nwk.archive_count` 的**兼容别名**（同 `10H-F1`/同断言） | 否（只读） |
| 13 | `nwk.route_restart` | `12H-F1` 启动路由重新组网，判 `00H-F1` 确认（组网前应先有档案） | **是**（重启路由） |
| 14 | `nwk.route_status` | `10H-F4` 路由运行状态，判「工作状态=抄表」；学习态等满 300s → unknown（不是 fail）；用例可给 `params.expect` | 否（只读） |
| 15 | `nwk.topology` | `10H-F21` 网络拓扑：入网从节点数 `≥ ceil(档案数 × min_ratio)`，默认 0.98 = 98%；`master_included` 默认 false | 否（只读） |
| 16 | `reset.sta` | 经 `sta-main` 会话发 `reboot`，判据是启动日志指纹（`reset reason:` / `SFT_GLB_RST` / `Unicorn Bootloader` / `Run image0…`），**不是返回码** | **是**（复位 STA） |

### 1.2 现有事实（8）

判定基准 = `libs/case_runner/data/facts/*.json`。事实是「不需要下发的现存状态」，可直接进 `requires`。

| # | 事实 id | 含义（判据） |
|---|---|---|
| 1 | `archive.contains` | 档案表含指定从节点地址（`10H-F2` 只读探测） |
| 2 | `bench.simcon_online` | 模拟集中器台架在线（simcon 子应用可达） |
| 3 | `bench.simeter_online` | 模拟电表台架在线（simeter 子应用可达） |
| 4 | `bench.sta_online` | STA 控制台口（`sta-main`）可用（串口桥只读 preflight） |
| 5 | `nwk.nodes_online_min` | **真实**在网/可达节点数 ≥ 下限（`10H-F4` + `10H-F2` 信号品质两路交叉；**绝不用 `10H-F1` 档案数推断**） |
| 6 | `nwk.onnet_count_min` | 档案从节点数 ≥ 下限（`10H-F1` 只读；兼容别名） |
| 7 | `nwk.route_running` | 路由在运行（`10H-F4` 工作状态=抄表；学习态按路由完成标志分层 → unknown，不是 fail） |
| 8 | `sta.meter_addr_known` | STA 是否已知该表地址（`02H-F1` 内嵌 645 地址域；探不到 = unknown，**绝不判 no**） |

### 1.3 台架与外部条件

| 条件 | 事实 | 出处 |
|---|---|---|
| 拓扑：`simcon(COM4) → CCO(COM12) → HPLC → STA(COM18) → 表口 → 模拟电表(COM20)` | 0042 已实测链路 | `reqs/0042-…/REQS.md:51`、`:61-65`（mapping_id ↔ Windows COM 表） |
| 侦听台 `listener(COM27)` | **本环境不接** | `reqs/0042-…/REQS.md:65`「侦听台（本需求不接）」 |
| 单 STA + **单块真表/单只模拟表** | 多表并发、180/400 节点台架、双 CCO、三相电源、II 采、水表全部不成立 | `reqs/0042-…/PLAN.md:220`「台架只有 1 块真表，真机不跑」 |
| Windows 侧串口桥必须先起（`serial_bridge.py windows`），否则全口不可用 → 一律 `blocked` | 硬前置 | `reqs/0043-…/REQS.md:63` |
| 复位后链路恢复 **≈10 分钟** | 决定复位类用例预算 | `reqs/0043-…/REQS.md:75`「复位带来的长恢复（实测≈10 分钟）」 |
| 一轮入网实测 ≈240s（`nwk.topology` wait_s=300 的依据） | 组网等待预算 | `libs/case_runner/data/blocks/nwk_topology.json:3`；`libs/case_runner/test_case_meter.py:507` |
| 模拟电表可实现：645 多 DI 数据集（含费率 1~4、三相电压/电流/功率）、698 从站与 698 采集 OAD 数据集、698 事件上报构造、改表址/改时钟 | 台架侧能力存在，但**多数没有对应块** | `libs/sim_meter/meter.py:72-89`（默认数据集）、`:449`（`build_event_report`）、`data/oad_dataset.example.json` |

### 1.4 可测性判据

一条候选用例判 **可测**，必须同时满足下列三条 + 一条硬约束：

1. **判据能表达**：判定要点可以只用 §1.1 的 16 块 + §1.2 的 8 事实组合出来（含块参数级差异，如 `DI`、`addr_mode`、`min`、`expect`、`expect_total`、`expect_empty`、`master_included`、`wait_s`）；不需要新增块、不需要新增断言类型。
2. **激励能产生**：台架（单 STA + 单表 + 模拟集中器/模拟电表）能造出该判据要求的**输入条件**（帧能发、表能应答、状态能进入）。
3. **等待预算可接受**：单轮耗时在 CL-5/CL-6 的预算内（分钟级以内；复位类 ≤ 10 min 可接受但要单列）。

**第四条（硬约束，0042 已固化成测试）**：**一条用例只允许一个等待型判据**（`nwk.topology` / `nwk.route_status` / `nwk.nodes_online` 三者互斥；多块默认等待值叠加会顶穿整轮 600s 上限），且 `nwk.topology` 的 `wait_s=300s` **不得被用例覆盖**。出处：`libs/case_runner/test_case_meter.py:508-514`（`wait_s` 必须仍是块声明默认 300s；`used_waiting == ["nwk.topology"]`）。本清单里凡涉及两个等待块的候选，都已在「判定要点/缺什么」注明拆步或降级。

否则判 **不可测**，并在「缺什么」写明四类之一：
- **缺块**：要发的帧（AFN/Fn）没有块，或要等的上行帧没有块/没有断言类型；
- **缺能力**：块在但判据表达不了（如 698 分帧/值解析、否认帧、空帧、负向期望、跨步骤算术）；
- **缺硬件**：单 STA/单表之外的设备（第二 STA、多表台架 180+、双 CCO、三相电源/继电器、II 采、水表、侦听台、升级台）；
- **缺省份环境**：省份专有 AFN 要求该省集中器/主站行为或该省固件分支（北京/浙江/福建/湖南/黑龙江/重庆/山西…）。

> 另：判据强度按 taxonomy §2.2 三档标注于「判定要点」末（`P1 必测` / `带回显对账` / `仅帧格式` / `我方定义`），供 0043 P2 排优先级时区分「能不能当送检判据」。

---

## 2. 候选清单（按批次排序）

### 批次 A：可测 + 常用（第一批，建议 ≤8 条）

> 全部只用现有 16 块/8 事实；A 批的目的就是**今晚能直接写成用例**（`plan` 步骤实例化 + `category/protocols` + `on_fail` + 等待预算）。与 0042 已交付 7 条用例（`bench_ready`/`archive_ready`/`nwk_join`/`sta_reset`/`meter_daily`/`meter_first_join`/`archive_multi`）**不重复**：这里是它们的**扩展项**。

| 建议 id | 场景 | 设备域 | 常用度 | 省份/区域 | 协议 | 判定要点（用什么帧/事实判） | 出处 | 可测性 | 缺什么 |
|---|---|---|---|---|---|---|---|---|---|
| `meter_di_multi_item` | `meter_read` | cco | common | sgcc | `1376.2`+`645` | 5 步 `meter.read_645`（protocol=2）：发 `02H-F1` 逐项透传 645 `11H`，DI=00010000 / 02010100 / 02010200 / 02020100 / 0203FF00；每步等上行 `02H-F1` 内嵌 `C=91`，比「DI+33H 回显」+ 数值可解 + `expect_value`（1234.56 / 220.1 / 221.0 / 1.234 / 0.5432）。判据档：带回显对账 | `用例库/STA/台账/123_国网用例_台账.md:63`（行 25 步骤 5：单相抄 02010100、02020100，三相抄 0201FF00、0202FF00，含 00010000）；同卡 `:56`（行 18 步骤 3）；台架数据 `libs/sim_meter/meter.py:72-89` | **可测** | — |
| `meter_rate_energy_account` | `meter_read` | cco | normal | sgcc | `1376.2`+`645` | 5 步：DI=00010000（总电能）+ 00010001/00010002/00010003/00010004（费率 1~4），每步判 DI 回显 + `expect_value` 精确匹配（1234.56 / 123.45 / 345.67 / 456.78 / 308.66）；detail 记录「Σ四费率 = 总电能」对账结论。判据档：带回显对账 | 台架数据集 `libs/sim_meter/meter.py:72-79`（注释即「费率电能之和 = 总电能，便于对账测试」）；DI 口径 `reqs/0042-…/REQS.md:517` | **可测** | 库内**无费率 DI 编码表**（见 §4-1）；「Σ=总」只作为 detail 记录，无跨步算术断言 |
| `archive_count_detail_chain` | `archive` | cco | common | sgcc | `1376.2` | `archive.add`×3（档案址 = reverse6(电表 645 址)）→ `nwk.archive_count(min=3)` 判 `10H-F1` ≥3 → `archive.query(meter_addr=A)` 判 `10H-F2` 含 A → `archive.del(A)` → `nwk.archive_count(min=2)` + `archive.query(A, expect_empty)` 反证 → `archive.del(B)` → `nwk.archive_count(min=1)`。判据档：带回显对账 | `用例库/CCO/台账/144_国网用例_流程与业务场景_台账.md:40`（行 4：加 5 查 5 → 删 1 查 4 → 删 5 查 3，每步「`10H-F2` 本次应答从节点总数量 + 地址」） | **可测** | 缺「路由支持最大从节点 1152」上限断言（见 D 批 `nwk_archive_max_1152`） |
| `nwk_route_restart_recount` | `route_ctrl` | cco | common | sgcc | `1376.2` | `archive.add`→`nwk.topology`（首次判达 98%）→`nwk.route_restart`（`12H-F1`，判 `00H-F1`）→`nwk.archive_count`（`10H-F1` 前后一致）→**第二次** `nwk.topology` 复判（若受「一用例一个等待判据」约束则拆成两条用例）。判据档：业务闭环 | `用例库/CCO/能力域/156_国网AFN功能回归清单_用例蒸馏.md:140`（「`12H-F1` 重启/F2 暂停/F3 恢复——都要求从第一个档案重新请求抄读」）；`用例库/CCO/台账/144…md:47`（行 11 步骤 3 重启路由） | **可测** | 「一用例一个等待型判据」→ 拓扑复判需拆第二条用例（`test_case_meter.py:511-514`）；红线：不得当入网手段，`reqs/0042-…/REQS.md:493` D6-2 |
| `nwk_offline_after_del` | `nwk` | cco | normal | sgcc | `1376.2`+`645` | `archive.del(A)` 后：`nwk.nodes_online(min=N-1)` 判「`10H-F4` 已抄数 + `10H-F2` 信号品质」两路下降；`meter.read_645` 抄**未删**地址仍成功（DI=00010000）。判据档：业务闭环（反向一半靠 detail） | `用例库/CCO/台账/144…md:42`（行 6 步骤 4-6：「已删除从节点正常离网…抄读已离网从节点 CCO 端返回空帧，抄读失败」）；`用例库/STA/台账/123…md:72`（行 34 步骤 4-6 同判据） | **可测** | 「抄已删址返回**空帧**」的负向断言缺（现块只判成功/失败，见 C 批 `meter_read_absent_addr`） |
| `meter_addr_probe_addr_mode` | `meter_read` | cco | normal | sgcc | `1376.2`+`645` | 同一表两种 `addr_mode`：①`meter`（`13H` 请求地址域 = 表 645 线序）→ 判内层应答地址域 = 表址（pass 为预期）；②`broadcast`（地址域 = `FE FE FE FE FE FE`）→ 记录「本台 CCO 对 `02H-F1`+FE 不转发、回空数据单元」，**只登记不判 fail**。判据档：带回显对账（①）/ 我方定义登记（②） | `01_DLT645_帧格式.md:326-340`（§6.3 读通信地址 C=13H，硬性「地址域全部为 FEH（广播方式读取）」）；我方实现与该实测差异 `libs/case_runner/blocks/meter.py`（`ADDR_MODES` 常量，`:191-193`） | **可测** | ②无负向断言类型（只能 detail 记录） |
| `bench_meter_brain_selftest` | `bench_prep` | bench | normal | sgcc | `645` | `fix.simeter_session` 后两步 `meter.brain_local`：①正帧（按 `meter_addr` + DI=00010000 动态构 645 `11H`）→ 判 `expect_reply=true` + `di_echo=33333433` + `note_value=non_empty`；②反帧 `68111111111111681104333334331816`（地址不符但 CS 合法）→ 判 `expect_reply=false`（不应答即 pass）。判据档：业务闭环 | `用例库/STA/台账/123…md:87`（行 49 电表异常响应：模块只负责数据转发，地址错误也转发 → 表端脑侧必须自证不应答）；块声明 `libs/case_runner/data/blocks/meter_brain_local.json:1-37` | **可测** | — |
| `nwk_archive_vs_online_cross` | `nwk` | cco | common | sgcc | `1376.2` | 同一档案下两路各自断言：`nwk.archive_count(min=N)`（`10H-F1` 档案数）+ `nwk.nodes_online(min=N)`（`10H-F4` 已抄数 / `10H-F2` 信号品质交叉）；detail 记两者差值，用于抓「档案数 ≠ 真在网数」的假通过。判据档：带回显对账 | `用例库/CCO/能力域/156…md:66`（`10H-F1` 从节点数量）+ `:69`（`10H-F4` 工作状态/当前状态）；事实分工见 `libs/case_runner/data/facts/nwk_nodes_online_min.json:3`（明写「绝不用 `10H-F1` 档案数推断」） | **可测** | 差值本身无断言（只记录），不单独判 fail |

### 批次 B：可测 + 一般（8–15 条，等 A 批跑稳）

| 建议 id | 场景 | 设备域 | 常用度 | 省份/区域 | 协议 | 判定要点（用什么帧/事实判） | 出处 | 可测性 | 缺什么 |
|---|---|---|---|---|---|---|---|---|---|
| `archive_query_paging_total` | `archive` | cco | normal | sgcc | `1376.2` | 两轮 `archive.query`：`start=0,count=1` 与 `count=16`，判「节点总数量」与「本次应答从节点总数量」一致且 = `expect_total`。判据档：带回显对账 | `用例库/CCO/台账/144…md:40`（步骤 6「从节点总数量=5，本次应答从节点总数量=5，地址为 1-5」） | **可测** | 缺「地址区间逐条比对」（块只比单址/总数） |
| `archive_add_idempotent` | `archive` | cco | normal | sgcc | `1376.2` | 同址 `11H-F1` 连发两次：两次都 pass，且 `nwk.archive_count` 读数不增（第二次 `10H-F1` 与第一次相同）。判据档：带回显对账（幂等为我方定义） | `用例库/CCO/台账/144…md:40`（步骤 4-5：加 5 → 查 5）；幂等口径 `reqs/0042-…/REQS.md:163` | **可测** | 库内无「重复加档」负例（判据为我方定义） |
| `nwk_route_status_learning` | `route_ctrl` | cco | normal | sgcc | `1376.2` | 复位/重启后立即 `nwk.route_status`，`params.expect=[学习…]`：等满 300s 仍是学习态 → verdict **unknown**（不得误判 fail）。判据档：我方定义（区分说不清与真失败） | `用例库/CCO/台账/144…md:41`（行 5 步骤 4「工作状态=1（正在工作），当前状态=1（搜表）」）；学习态坑 `reqs/0042-…/REQS.md:100` | **可测** | 「工作状态/当前状态」字段语义库内存疑（§4-5） |
| `meter_read_wait_budget_unknown` | `meter_read` | cco | normal | sgcc | `1376.2`+`645` | 不执行 `fix.simeter_session`（表口关）/或表不应答 → `meter.read_645` 给 `wait_s>0`，等满预算全程无应答 → **unknown**（说清「说不清」），不是 fail。判据档：我方定义 | `用例库/CCO/台账/144…md:62`（行 26 步骤 6-8：超时无数据则重抄，最大 10 轮）；块语义 `libs/case_runner/data/blocks/meter_read_645.json:3` | **可测** | 台架顺序敏感（`fix.simeter_session` 开完不自动关，需后续步骤保证复位） |
| `archive_query_empty` | `archive` | cco | normal | sgcc | `1376.2` | `archive.query(expect_empty=true)` → 判 `10H-F2` 节点总数量=0 且无地址（空档案 / 删净后）。判据档：带回显对账 | `用例库/CCO/台账/144…md:40`（步骤 3「从节点总数量=0，本次应答从节点总数量=0」） | **可测** | — |
| `nwk_online_zero_before_archive` | `nwk` | cco | normal | sgcc | `1376.2` | 建档前：`nwk.archive_count(min=0)` 判 `10H-F1` =0 + `nwk.nodes_online(min=0)` 判 `10H-F4` 无在网节点。判据档：带回显对账 | `用例库/CCO/能力域/156…md:66`（`10H-F1` 无档案=0）；`用例库/STA/台账/123…md:78`（行 40 步骤 2 返回从节点数量=0） | **可测** | `10H-F4` 无「==0」专用断言，只靠 `min=0` 弱判 |
| `bench_meter_brain_standalone` | `bench_prep` | bench | rare | sgcc | `645` | 只 `fix.simeter_session` + `meter.brain_local`（不依赖 simcon/CCO 会话）：证明「表端脑通路独立于 CCO 抄表通路」，CCO 侧挂掉时仍能判台架在位。判据档：带回显对账 | `reqs/0042-…/REQS.md` §4.2（`meter.brain_local` 归属 bench，「不依赖 CCO」）；块声明 `libs/case_runner/data/blocks/meter_brain_local.json:1` | **可测** | — |
| `archive_del_all_count_zero` | `archive` | cco | normal | sgcc | `1376.2` | `archive.del` 逐条删光 → `nwk.archive_count(min=0)` 判 =0 + `archive_query_empty` 复核。判据档：业务闭环 | `用例库/CCO/台账/144…md:40`（步骤 13-15：库内用 `01H-F2` 参数初始化清空） | **可测** | 缺 `01H-F2`「参数初始化」块（只能用 del 逐条等价替代，语义略弱） |
| `nwk_topology_no_archive` | `nwk` | cco | normal | sgcc | `1376.2` | 无档案时 `nwk.topology`（`master_included=true`，唯一等待块）判「节点总数量 ≥ 1」+ `nwk.archive_count(min=0)` 判档案=0；detail 记录「本次应答节点总数量=1、只含主节点」即证明不是从节点。判据档：带回显对账 | `用例库/CCO/能力域/156…md:74`（`10H-F21` 步骤 1「节点总数量=1，本次应答节点总数量=1，只包含主节点」） | **可测** | 「只含主节点」只能靠 detail 记录（无「地址集合」断言） |
| `nwk_route_restart_no_archive` | `route_ctrl` | cco | rare | sgcc | `1376.2` | 无档案下 `12H-F1`：判 `00H-F1` 确认，且随后 `nwk.topology`（`master_included=true`）判节点总数量=1（只含主节点，不得凭空生出从节点）。判据档：我方定义 | 块声明 `libs/case_runner/data/blocks/nwk_route_restart.json:3`（tags `precondition-archive`「组网前应先有档案」）；`用例库/CCO/能力域/156…md:85`（`12H-F1` 3 步判据） | **可测** | 库内无「无档案重启路由」判据（我方定义） |
| `nwk_route_restart_status` | `route_ctrl` | cco | normal | sgcc | `1376.2` | `archive.add`→`nwk.route_restart`→`nwk.route_status`（唯一等待块）判「工作状态=抄表」；与 A4 的区别：A4 判**拓扑**，本条判**状态机**。判据档：带回显对账 | `用例库/CCO/能力域/156…md:140`（重启后从第一个档案重新请求）+ `:69`（`10H-F4` 工作状态=1/当前状态）；`用例库/CCO/台账/144…md:41`（行 5 步骤 4） | **可测** | 状态字段口径存疑（§4.2-5）；不得当入网手段（`reqs/0042-…/REQS.md:493` D6-2） |
| `bench_meter_brain_di_variants` | `bench_prep` | bench | normal | sgcc | `645` | 表端脑对 3 个 DI 各发一帧（00010000 / 02010100 / 0203FF00）：判每帧 `di_echo` 与 DI 匹配（+33H 传输序）、`note.value` 非空；不同 DI 不得串帧。判据档：带回显对账 | `用例库/STA/台账/123…md:63`（行 25 步骤 5 多数据项组合）+ 块声明 `libs/case_runner/data/blocks/meter_brain_local.json:12-26` | **可测** | — |

### 批次 C：可测 + 少见（≤10 条）

| 建议 id | 场景 | 设备域 | 常用度 | 省份/区域 | 协议 | 判定要点（用什么帧/事实判） | 出处 | 可测性 | 缺什么 |
|---|---|---|---|---|---|---|---|---|---|
| `meter_read_absent_addr` | `meter_read` | cco | rare | sgcc | `1376.2`+`645` | 档案里有、台上未挂表的地址点抄 `13H-F1`：期望「抄读不成功」；现只能靠 `verdict=fail` 表达负向（判据方向与常规相反，须在 `on_fail` 显式说明「fail 即预期」）。判据档：我方定义（弱） | `用例库/CCO/台账/144…md:42`（步骤 6「抄读已离网从节点，CCO 端返回空帧，抄读失败」） | **可测** | 缺**负向断言**（`expect=empty_frame` / `expect_fail`），现靠 verdict 反向解读 |
| `nwk_topology_master_ratio` | `nwk` | cco | rare | sgcc | `1376.2` | 同档案两轮 `10H-F21`：`master_included=false` 判「从节点 ≥ ceil(档×0.98)」，`true` 判「节点总数量 ≥ 同上」，两读数差 = 1（主节点）。判据档：带回显对账 | `用例库/CCO/能力域/156…md:74`（`10H-F21` 含主节点口径）；块参数 `libs/case_runner/data/blocks/nwk_topology.json:3` | **可测** | 两轮 `nwk.topology` 与「一用例一个等待型判据」冲突（`libs/case_runner/test_case_meter.py:511-514`）→ 必须拆成两条用例跑，靠人工/detail 拼差值 |
| `nwk_route_status_budget` | `route_ctrl` | cco | rare | sgcc | `1376.2` | 复位后测「学习态 → 抄表态」切换耗时是否 ≤ 300s（块默认预算）；> 300s → unknown 并记实际耗时。判据档：我方定义（预算标定） | `libs/case_runner/data/blocks/nwk_route_status.json:3`（wait_s=300 依据台架实测一轮入网≈240s）；`用例库/CCO/台账/144…md:42`（步骤 2 记录入网时间） | **可测** | 单 STA 单表，耗时统计说服力弱（结论只能当趋势） |
| `bench_brain_write_di` | `bench_prep` | bench | rare | sgcc | `645` | 表端脑发 645 写数据帧（C=14，DI=04000103 写日期）→ 只判「有应答 + CS 合法」（无写后回读判据）。判据档：仅帧格式（弱，且改台架状态） | `01_DLT645_帧格式.md:515`（写表地址 000000000001 的当前日期 DI=04000103H）；`用例库/STA/台账/123…md:73`（行 35 步骤 5：C=14 设置电表时钟） | **可测** | 缺「写后回读」判据（模拟表默认数据集无 04000103）；写操作脏台架，需复位/重设 |
| `archive_del_absent` | `archive` | cco | rare | sgcc | `1376.2` | `archive.del` 删不存在地址 → 块幂等口径 pass + `nwk.archive_count` 数量不变。判据档：我方定义 | 块声明 `libs/case_runner/data/blocks/archive_del.json:3`（「不存在 → pass」）；`用例库/CCO/台账/144…md:40`（步骤 7-12 删非末位档案） | **可测** | 库内无此负例（我方定义） |
| `bench_simeter_close_reopen` | `bench_prep` | bench | rare | sgcc | `645` | 关表口后 `meter.brain_local` → 期望 `skipped`/`unknown`（不假 pass）；重开后 `meter.brain_local` → pass。验证台架块「开口不自动关」的语义边界。判据档：我方定义 | `libs/case_runner/data/blocks/fix_simeter_session.json:3`（「开完不关」）；`libs/case_runner/capabilities/simeter.py:226`（`open_port`） | **可测** | 无「关表口」块（需走 capability/人工），只能在台架侧预置状态 |

### 批次 D：不可测（缺块/能力/硬件/省份环境）

> 只登记不实施（REQS §3 非目标：「不为省份用例去造不存在的省份环境」）。「缺什么」已具体到块名/AFN/硬件项。

| 建议 id | 场景 | 设备域 | 常用度 | 省份/区域 | 协议 | 判定要点（用什么帧/事实判） | 出处 | 可测性 | 缺什么 |
|---|---|---|---|---|---|---|---|---|---|
| `meter_13h_698` | `meter_read` | sta | common | sgcc | `1376.2`+`698.45` | `13H-F1` 透传 698：读取请求类 05 / 设置 06 / 操作 07 / 安全 16，判「CCO 端正确返回 698 数据」 | `用例库/STA/台账/123…md:73`（行 35 步骤 11-14）；`用例库/CCO/能力域/156…md:88`（`13H-F1` 37 步含 698） | **不可测** | 缺块（`meter.read_698`：断言要判 698 响应类型 + OAD + 值解析）+ 缺能力（698 分帧/多帧重组不在块层） |
| `meter_13h_97` | `meter_read` | sta | normal | sgcc | `1376.2`+`645` | `13H-F1` 透传 97 版 645（协议类型=1），判「CCO 端正确返回 97 表数据」 | `用例库/STA/台账/123…md:73`（行 35 步骤 16） | **不可测** | 缺块（97 版判据：2 字节 DI，现 `inner_645_read` 只认 4 字节 DI 回显） |
| `meter_f1f1_batch` | `meter_read` | sta | common | sgcc | `1376.2`+`645`/`698.45` | `F1H-F1` 并发抄表：每帧 1 条/3 条 97·07·698 报文，判「正确返回 N 条抄表数据」 | `用例库/STA/台账/123…md:74`（行 36 步骤 1-6）；判据弱化成因 `用例库/CCO/能力域/156…md:128`（两例交叉「判据全是同一句正确返回，只能当冒烟」） | **不可测** | 缺块（`meter.read_batch`：`F1H-F1` + 逐条报文回显比对） |
| `meter_f1f1_fuzz` | `meter_read` | sta | rare | sgcc | `1376.2` | `F1H-F1` 报文含乱码/粘帧/超长：判「超过最大字节数 1024 不返回」「max 字节 STA 不复位」 | `用例库/STA/台账/123…md:74`（行 36 步骤 7-29） | **不可测** | 缺块（乱码构造 + 长度边界断言）+ 缺能力（复位检测需日志指纹组合） |
| `meter_route_14h` | `meter_read` | cco | common | sgcc | `1376.2` | `14H-F1` 路由抄表状态机：判「询问 3 次后停止」「抄读成功=1 → 切下一块表」「最后一块不再询问」 | `用例库/CCO/台账/144…md:47`（行 11 步骤 4-11）；`用例库/CCO/能力域/156…md:89`（`14H-F1` 13 步） | **不可测** | 缺块（`14H-F1` 请求/应答交互 + 顺序状态机） |
| `meter_delay_14h3` | `meter_read` | cco | rare | sgcc | `1376.2` | 带通信延时抄表：判「CCO 发 `14H-F3` 请求修复通信延时」→「正确返回 `13H-F1`」→「重启后发 `14H-F1` 问询」 | `用例库/CCO/台账/144…md:79`（行 43 步骤 3-8）；`用例库/CCO/能力域/156…md:91` | **不可测** | 缺块（`14H-F3`）+ 缺块（`12H-F2` 暂停路由，本环境连暂停都没有） |
| `meter_13f1_f1f1_cross` | `meter_read` | cco | rare | sgcc | `1376.2` | 点抄 × 并发交叉：`13H-F1` 后 500ms 发 `F1H-F1`，判两路都正常返回；表不回复时判「正常返回空帧」 | `用例库/CCO/台账/144…md:78`（行 42 步骤 2-5） | **不可测** | 缺块（并发双通道 + 500ms 时序）+ 缺能力（跨通道时序断言） |
| `meter_seq_filter` | `meter_read` | cco | rare | sgcc | `1376.2`+`645` | 广播抄表序号过滤：判「帧序号一一对应」「未入网 STA 回数据域为空帧」「上行并发帧源地址 = 645 上报表地址」 | `用例库/CCO/台账/144…md:74`（行 38 步骤 3-7） | **不可测** | 缺块（帧序号比对）+ 缺硬件（需要第二 STA 构造未入网/入网对比） |
| `meter_longrun_success_rate` | `meter_read` | sta | normal | sgcc | `1376.2`+`645` | 48h 循环抄读：判「每块表抄表成功率 >99%，每轮时间基本一致，无复位」 | `用例库/STA/台账/123…md:75`（行 37 步骤 1-6） | **不可测** | 缺硬件（多表台架 ≥180）+ 缺能力（复位检测与成功率统计不在块层）；本环境只能用 0043 P3 的 `meter_daily` 长跑近似 |
| `sta_reset_recovery_budget` | `reset`+`nwk` | sta | normal | sgcc | `1376.2` | 复位后链路恢复时限：判「`reset.sta` 指纹 → `nwk.topology` 在 N 秒内达 ≥ ceil(档案×0.98)」，N = 实测恢复时间（≈10 min） | `reqs/0043-…/REQS.md:75`（复位恢复实测≈10 分钟）；`业务层/37_双模测试用例_业务.md:42-45`（98%–100% 判据）；台架入网实测 240s `libs/case_runner/data/blocks/nwk_topology.json:3` | **不可测** | 缺能力（`nwk.topology` 的 `wait_s=300s` **被钉死、用例不得覆盖**（`libs/case_runner/test_case_meter.py:508-511`），300s < 恢复 10 min → 只能产出「明确不足」的**假 FAIL**）+ 缺块（需要一个「复位恢复」专用等待块/长预算块） |
| `phase_single_three` | `phase` | sta | common | sgcc | `1376.2`+`645` | `10H-F2` 相位信息（D8/D9/D10）= 1/2/4/7；`10H-F31` 相序类型=0~6、线路异常=0/1；`F1H-F1` 实测相线标识 | `用例库/CCO/台账/144…md:67-71`（行 31-35 三相相位/相序/零火反接）；`用例库/STA/台账/123…md:69`（行 31 单相相位） | **不可测** | 缺块（`10H-F31` 相线信息查询块）+ 缺硬件（三相电源、单相/三相表各一只、屏蔽箱接线切换） |
| `district_identify` | `district` | cco | normal | sgcc | `1376.2` | `05H-F6` 启动台区识别 → `10H-F111` 查多网络 → 判 `06H-F5`「台区标志位=1、所属台区=CCO1 地址或 FF」+ `06H-F3` 变动类型=3（台区识别结束） | `用例库/CCO/台账/144…md:64`（行 28）；`用例库/STA/台账/123…md:79`（行 41 步骤 3-6） | **不可测** | 缺块（`05H-F6` / `10H-F111` / `06H-F5` 监听 / `06H-F3` 监听）+ 缺硬件（双 CCO、双台区表架） |
| `event_report_06hf5` | `event` | sta | common | sgcc | `1376.2`+`645`+`698.45` | 表端产生事件 → 判 CCO 上报 `06H-F5`（设备类型=2/5、通信协议类型=4、事件类型=1 停电/2 复电），且与表端事件帧逐字段一致 | `用例库/CCO/台账/144…md:43`（行 7 步骤 4-13，含 698 掉电/开表盖/多事件 3 条注入帧）；`用例库/STA/台账/123…md:76`（行 38） | **不可测** | 缺块（`06H-F5` 事件监听块 + 触发模拟表主动上报的块）+ 缺硬件（继电器断电/开表盖触发；台架无断路开关） |
| `power_event_edge` | `power_event` | cco | normal | sgcc | `1376.2` | 停复电边缘：1s/5s/20s/30s/1min 停复电、仅零/火线断电，判 `06H-F5` 上报个数与实际一致、`PowerOnSTACnt` 日志 20 次 | `用例库/CCO/台账/144…md:52`（行 16 步骤 3-22）；`用例库/STA/台账/123…md:66`（行 28） | **不可测** | 缺块（同上）+ 缺硬件（可编程继电器、分相断电装置、模块日志分析器） |
| `clock_broadcast_05hf3` | `clock` | cco | common | sgcc | `1376.2`+`645`/`698.45` | `05H-F3` 广播校时 → 判「所有从节点收到且只收一帧」「表端时间与校时时间一致」 | `用例库/CCO/台账/144…md:44`（行 8 步骤 3-4）；`用例库/STA/台账/123…md:77`（行 39）；`用例库/CCO/能力域/156…md:56`（`05H-F3` 20 步业务闭环） | **不可测** | 缺块（`05H-F3` 广播校时 + 表端时钟回读判据）+ 缺能力（模拟表默认数据集无时钟 DI） |
| `clock_manage_hlj` | `clock` | cco | rare | hlj | `1376.2` | 黑龙江时钟管理：`05H-F90` 时钟管理开关 / `F92` 精准校时 / `F93` 时钟维护 / `F94` 超差阈值 / `10H-F90` 查询 | `用例库/CCO/能力域/156…md:115`（黑龙江扩展表）；`用例库/STA/00_STA用例库总览.md:167-168`（黑龙江 23 例全压时钟管理） | **不可测** | **缺省份环境**（黑龙江固件分支）+ 缺块（5 个 AFN） |
| `minute_collect_plan1` | `minute_collect` | cco | normal | sgcc | `1376.2`+`645`/`698.45` | 方案 1（CCO 召测）：`11H-F231/F232` 配任务与关联档案 → `10H-F230` 任务数/位图 → `10H-F231` 任务配置回显 → `06H-F230` 采集数据上报 | `用例库/STA/能力域/141_分钟级采集_用例蒸馏.md:21`（方案表）、`:31-45`（报文对与位图对账）；逐例 `用例库/STA/台账/123…md:58-62` | **不可测** | 缺块（`11H-F231/F232`、`10H-F230/F231`、`06H-F230` 共 5 个） |
| `minute_collect_plan2` | `minute_collect` | sta | normal | sgcc | `1376.2`+`698.45` | 方案 2（STA 上报）：`F2H-F2` 配任务（目的地址/间隔/主数据项 50020200）→ `F2H-F3` 回显 → `F2H-F5` 上报（规约类型=3，内容为 698 报文） | `用例库/STA/能力域/141…md:22`、`:47-54`；逐例 `用例库/STA/台账/123…md:55-57` | **不可测** | 缺块（`F2H-F1/F2/F3/F5`）+ 缺能力（698 分帧解析，`F2H-F5` 内嵌多帧） |
| `curve_collect` | `curve` | sta | rare | hn | `1376.2` | 湖南曲线采集：CCO 侧 `03H-F102` 读 CCO 曲线数据对象 / STA 侧按任务采集曲线 | `用例库/CCO/00_CCO用例库总览.md:56`（湖南 12 例）；`用例库/STA/00_STA用例库总览.md:143`（湖南 18 例）；台账 `用例库/CCO/台账/153_湖南用例_台账.md` | **不可测** | **缺省份环境**（湖南分支）+ 缺块（`03H-F102`）+ 缺硬件（真实负荷曲线需要负载装置） |
| `upgrade_15hf1` | `upgrade` | cco | normal | sgcc | `1376.2`+`376.2_local` | `15H-F1` 升级（文件类型/分段 256/512/1024）→ 版本查询 → 组网/抄表复核；异常分支含拔模块、路由暂停、`10H-F104` 版本同步 | `用例库/CCO/台账/144…md:46-48`（行 10 CCO 升级 / 行 12 文件升级）；`用例库/STA/台账/123…md:88-93`（行 50-55） | **不可测** | 缺块（`15H-F1`）+ 缺硬件（陪测版本 .dat/.bin）+ **违反 0043 非目标**（REQS:40「不做真固件烧录/激活」） |
| `fee_control_1c` | `fee_control` | cco | common | sgcc | `1376.2`+`645` | `13H-F1` 透传 645 `C=1C` 跳闸/合闸（命令截止时间 >/< 当前时间），判 `C=9C`/`DC` 对应应答 | `用例库/STA/台账/123…md:73`（行 35 步骤 7-8：「C=1C 跳闸或者合闸，命令截止时间>当前时间 / <当前时间」，预期 C=9C / C=DC）；标准层 `业务层/37_双模测试用例_业务.md:75`（5.9 实时费控，必测 11 项之一） | **不可测** | 缺块（拉合闸判据：现 `inner_645_read` 只认读数据 DI 回显）+ 缺硬件（台架表侧无断路器/继电器执行机构，拉闸无法验证实际动作） |
| `water_meter` | `water_meter` | cco | rare | bj | `1376.2`+`645` | 双向水表实时抄读 / 单向水表主动上报（北京 9 例） | `用例库/CCO/00_CCO用例库总览.md:57`（北京 9 例）；`用例库/STA/00_STA用例库总览.md:150` | **不可测** | 缺硬件（水表/水表底座）+ **缺省份环境**（北京 II 型设备接入） |
| `ii_collect` | `meter_read` | cco | rare | bj | `1376.2` | 抄读 II 采下挂表：判「CCO 仅能抄读成功入网表」「一帧上报所有下挂表信息」（`F1H-F1`） | `用例库/CCO/台账/144…md:49`（行 13 步骤 2-7）；`用例库/CCO/能力域/156…md:72`（`10H-F7` 模块类型：STA=0、II 采=1） | **不可测** | 缺硬件（II 采 + 20 只下挂表）+ 缺块（`F1H-F1`、`10H-F7`） |
| `feature_current` | `feature_current` | cco | rare | zj | `1376.2` | 浙江特征电流：`13H-F1` / `F1H-F1` 双通道下发特征电流，180 s 倒计时 | `用例库/CCO/00_CCO用例库总览.md:58`（浙江 8 例）、`:84`（「CCO 下发，645/F1H-F1 双通道，180 s 倒计时」）；`用例库/STA/00_STA用例库总览.md:149` | **不可测** | **缺省份环境**（浙江分支）+ 缺块 + 缺硬件（特征电流检测装置） |
| `access_control_bj_zj` | `access_control` | cco | normal | bj/zj | `1376.2` | 白名单 `03H-F131`/`05H-F131`、黑名单 `11H-F11`（上限 255）、网络锁定 `11H-F126`（180 min 自动解锁）、台区归属 `11H-F127`、锁定时长 `03H-F30`/`05H-F30`、拒绝节点上报 `05H-F200` | `用例库/CCO/能力域/157_黑白名单与网络锁定台区归属_用例蒸馏.md:19-26`；省扩展清单 `用例库/CCO/能力域/156…md:101-104`（北京）、`:110-111`（浙江） | **不可测** | **缺省份环境**（北京/浙江固件分支）+ 缺块（6+ 个 AFN）+ 缺硬件（需要第二网络/第二 CCO 才能验「拒绝入网/钉住网络」） |
| `data_freeze_fj` | `data_freeze` | sta | normal | fj | `698_ext` | 福建数据冻结任务：`EBE0E0xx`/`EBE00xxx`/`EB03xxxx` OAD 配冻结任务，判冻结数据上报 | `用例库/STA/00_STA用例库总览.md:156`（福建 57 例，698 扩展 OAD）；台账 `用例库/STA/台账/133_福建用例_数据冻结任务配置_台账.md`；能力域 `用例库/STA/能力域/142_福建数据冻结任务与对象模型_用例蒸馏.md` | **不可测** | **缺省份环境**（福建 V1.3 任务配置）+ 缺块（EB 系列 OAD）+ 缺能力（698 扩展 OAD 编解码未落在块层） |
| `device_info_fj_oad` | `device_info` | sta | normal | fj | `698_ext` | 福建设备基础信息/事件 OAD（EA/EB 系列），判设备信息与事件配置读写 | `用例库/STA/00_STA用例库总览.md:144`（福建 16 例）；台账 `用例库/STA/台账/136_福建用例_设备基础信息与事件配置_台账.md`；能力域 `用例库/STA/能力域/143_福建设备基础信息与事件OAD_用例蒸馏.md` | **不可测** | **缺省份环境**（福建）+ 缺块（OAD 读写 + 判据） |
| `io_lamp` | `io_lamp` | sta | rare | sgcc | `none` | 灯语与 IO 口功能：判指示灯的亮/灭/闪组合与 IO 电平（19 例） | `用例库/STA/00_STA用例库总览.md:141`（灯语与 IO 口 19 例）；`用例库/STA/00_STA用例库总览.md:169`（P1 空白：「灯语」全库零命中） | **不可测** | 缺硬件（灯语需目视/摄像头取证，台架无光学采集）+ 缺能力（无块可下发 IO 激励） |
| `fusion_terminal_et` | `fusion_terminal` | cco | rare | non_province | `376.2_local`（`68 … 42 … 16`） | 融合终端管理通道：能源控制器功能模块接口协议，串口直连帧 | `用例库/CCO/00_CCO用例库总览.md:45`（§二第 2 条：内容不是 1376.2，属另一个协议域）；台账 `用例库/CCO/台账/155_融合终端通道管理用例_台账.md` | **不可测** | 缺块（整层协议域不在 16 块内，simcon 不产生 `68…42…16` 帧）+ 缺硬件（串口直连被测终端） |
| `ecu_mgmt_channel` | `device_info` | ecu | rare | non_province | `ecu_mgmt` | ECU/模组管理通道（智能融合终端模组接口协议）链路请求/GET/SET/上报 | `协议层/162_智能融合终端模组接口协议_帧格式.md:182`（§六 与 ECU 管理通道实现的对照要点）；**用例库 0 命中**（taxonomy §6） | **不可测** | 缺块 + **库中未找到 ECU 用例**（taxonomy §1.2/§6：「扩展单元」全库零命中）+ 缺硬件（ECU 扩展单元） |
| `nwk_beacon_broadcast_limit` | `nwk` | cco | rare | sgcc | `mac_phy` | 信标机制（无档案不发信标 / 中央信标正常）；并发广播条数上限（17 个未入网节点只发前 16 条；有广播任务时新任务不生效） | `用例库/CCO/台账/144…md:58`（行 22 信标发送机制）；`用例库/CCO/台账/144…md:77`（行 41 并发广播限制） | **不可测** | 缺块（侦听台/载波观测块）+ 缺硬件（侦听台 COM27 本环境不接，`reqs/0042-…/REQS.md:65`）+ 需 17 个未入网 STA |
| `nwk_band_switch` | `nwk` | cco | normal | sgcc | `1376.2`+`mac_phy` | `05H-F16` 设频段（0/1/3）→ `03H-F16` 查询回显 → 等 150 s 生效 → 组网复测；判「变更次数 < 400」 | `用例库/CCO/台账/144…md:65`（行 29 步骤 4-10）；组网复测 `用例库/CCO/台账/144…md:42`（步骤 7-12） | **不可测** | 缺块（`05H-F16`/`03H-F16`）+ 缺块（网络变动统计） |
| `nwk_hrf_only` | `nwk` | cco | rare | sgcc | `mac_phy` | 仅 HRF 组网复测（断开电力线、typeC 供电）后与载波组网输出一致 | `用例库/CCO/台账/144…md:42`（行 6 步骤 13-15）；`用例库/STA/台账/123…md:72`（步骤 14-15） | **不可测** | 缺硬件（需物理断开电力线、可选电源）→ 环境类，只能人工 |
| `nwk_capacity_180` | `nwk` | cco | normal | sgcc | `1376.2` | 大环境组网：判「所有电表正常入网」+ 台体 ≤400 节点 | `用例库/STA/台账/123…md:72`（行 34 前置「表架测试（电表数量>=180）」）；`用例库/CCO/台账/144…md:42`（行 6 前置「STA 数量>=180」） | **不可测** | 缺硬件（180/400 节点台架，本台单 STA 单表） |
| `nwk_archive_max_1152` | `archive` | cco | rare | sgcc | `1376.2` | `10H-F1` 判「路由支持最大从节点数量 = 1152」（每步应答都带该字段） | `用例库/CCO/台账/144…md:40`（行 4 各步预期「路由支持最大从节点数量=1152」）；`用例库/CCO/能力域/156…md:136`（路由最大从节点 **1152**） | **不可测** | 缺块/缺能力（`nwk.archive_count` 只断言 `min`，不解析「路由支持最大从节点数量」字段） |
| `module_id_10hf7` | `nwk` | cco | normal | sgcc | `1376.2` | `10H-F7` 查从节点模块 ID / `10H-F40` 流水线查 ID / `10H-F112` 查芯片 ID；换模块后判「ID 变更为更换后的模块信息」 | `用例库/CCO/台账/144…md:66`（行 30 验证模块 ID 变更）；`用例库/CCO/能力域/156…md:72`、`:81` | **不可测** | 缺块（`10H-F7`/`10H-F40`/`10H-F112`）+ 缺硬件（不同模块 ID 的第二只 STA） |
| `nwk_offline_noresp` | `nwk` | sta | normal | sgcc | `1376.2` | 未入网 STA：判「不响应抄表」「不下发抄表帧到表端」「不响应校时」 | `用例库/CCO/台账/144…md:57`（行 21）；`用例库/STA/台账/123…md:82`（行 44） | **不可测** | 缺块（构造「未入网但物理在位」的 STA：需删档后不断电 + 负向断言）+ 缺硬件（第二 STA） |
| `search_meter_11hf5` | `search_meter` | cco | normal | sgcc | `1376.2` | `11H-F5` → `06H-F4` 上报 → `06H-F3`（变动类型 2 = 搜表结束） | `用例库/CCO/台账/144…md:41`（行 5）；`用例库/CCO/能力域/156…md:138` | **不可测** | **已裁决 `usage=forbidden`**（`reqs/0042-…/REQS.md:493-496`）：块存在但禁止进用例；且会临时关白名单、打断抄表主线 |

---

## 3. 每批的建议动作

| 批次 | 动作 | 门禁 |
|---|---|---|
| **A 批（8 条）** | **直接进 0043 P2**：按 `plan` 步骤实例化写成 `libs/case_runner/data/cases/*.json`，挂 `category`/`protocols`/`on_fail`/等待预算；先 A1/A3/A8 三条（纯只读或幂等写），再 A4/A5（写块，需顺序） | `case_run check` problems==[]；`list --cases` 分类可见（CL-3） |
| **B 批（12 条）** | **等 A 批跑稳再上**：B 批多为「负向语义/台架语义边界/弱判据」，建议 A 批连跑 ≥20 轮稳定后再逐条落地 | 每新增一条先单独真机跑出 pass/fail/blocked（CL-4） |
| **C 批（6 条）** | **登记备选，按需挑 1–2 条**：判据普遍偏弱（负向靠 verdict 反向、无跨步断言），只在 A/B 批覆盖不足时补 | 落地前确认「弱判据不入送检结论」（taxonomy §2.2） |
| **D 批（本表 38 条）** | **只登记不实施**：写进 0043 P4 收敛报告的「因环境不可测」清单；每条都已给「缺什么」，后续若要测就按缺项立新需求（缺块优先，缺硬件另立项） | 不为 D 批造环境（REQS §3 非目标） |

**A 批落地顺序建议**：`meter_di_multi_item` → `nwk_archive_vs_online_cross` → `archive_count_detail_chain`（三条只读/幂等，可当晚连跑）→ `meter_rate_energy_account` → `meter_addr_probe_addr_mode` → `bench_meter_brain_selftest` → `nwk_offline_after_del` → `nwk_route_restart_recount`（写块放最后，避免打断前三条的长跑基线）。

---

## 4. 库中未找到 / 可疑项

### 4.1 库中未找到（检索结论，不编）

| # | 想要的东西 | 检索方式 | 结论与影响 |
|---|---|---|---|
| 1 | **645 费率/冻结量 DI 编码表**（`00010001~04` 费率、`0506xxxx` 冻结量） | `grep -rn "00010001\|00010002" /01-workfile-ai/04-obsidian/obsidian --include=*.md` → **0 命中** | 库中未找到。A2 `meter_rate_energy_account` 的 DI 值只能引**台架模拟表数据集**（`libs/sim_meter/meter.py:72-79`）与 `01_DLT645_帧格式.md:224`（只举 `00010000` 一例），不能引库当送检判据 |
| 2 | **「冻结量」判据的可测数据项** | 台账内只有 `05060001/05060101/05060201`（`用例库/STA/台账/123…md:73` 行 35 步骤 1）与「日冻结 3 项」（`用例库/CCO/台账/144…md:62` 行 26 步骤 8） | 库中有「读冻结量」的**步骤**，**没有 DI 编码与期望值**；且模拟表默认数据集无这些 DI → 列 D 批（缺能力：无「设置表内数据」块） |
| 3 | **「常用度/优先级」的库内权威口径** | taxonomy §1.0 已实测：STA 339 行 I 列 0 行有值、CCO 241 行 H/I/J/K 全空、`grep 常用` 零命中 | 本文件三档「常用度」= **我方定义**（taxonomy §2.2 三级判定），不是库字段 |
| 4 | **ECU / 扩展单元用例** | taxonomy §6：`grep ECU\|扩展单元` 全库 0 命中（唯一相关为协议卡 `协议层/162…md:182`） | 库中未找到 → D 批 `ecu_mgmt_channel` 只能按协议卡登记，不能按用例登记 |
| 5 | **南网用例** | taxonomy §6：`grep 南网`（用例库）仅 2 处非用例命中 | 库中未找到 → 本清单无 `csg` 行 |
| 6 | **单帧多 DI 一次读多数据项** 的 1376.2 判据 | 台账中「多数据项」全部是**多步**（`123…md:63` 行 25、`:58` 行 20） | 库中未找到「一帧多 DI」口径 → 本清单只登记多步逐项（A1），不编单帧多项 |
| 7 | **`10H-F20` 与 `10H-F21` 哪个是拓扑查询** | taxonomy §6：156 卡自述「以 `10H-F21` 为拓扑查询，`10H-F20` 疑为笔误，实现前需核对 1376.2 原文」 | **库内存疑未定**。本清单一律取 `10H-F21`（与现块一致），`10H-F20` 标注「待核」不上用例 |
| 8 | **省份逐例判据**（湖南 `153`、浙江 `151`、黑龙江 `154`、北京 `147` 等台账） | 本轮只核对到「总览计数 + CCO 能力域卡」层，未逐例读 9 张省台账全文 | **未逐例核对**：D 批中 `curve_collect`/`access_control_bj_zj`/`clock_manage_hlj` 的判据来自 156 §三 + 157 + 总览，**不是**逐例原文；若后续要落地必须先逐例核（登记为遗留风险） |

### 4.2 可疑项（库内自相矛盾，用时须先核）

| # | 可疑点 | 冲突两处 | 影响 |
|---|---|---|---|
| 1 | **「实测相线标识」取值冲突** | `用例库/CCO/台账/144…md:67`（行 31 步骤 6）三相表判「实测相线标识=**3**」；`用例库/STA/台账/123…md:69`（行 31 步骤 4）单相 A 相判「实测相线标识=**1（A相）**」 | 同一字段两种取值口径；`phase_single_three` 若落地必须先核 91 号协议卡再定判据 |
| 2 | **事件类型文案错位** | `用例库/CCO/台账/144…md:43`（行 7 步骤 12「开表盖事件」）预期写「正确上报 `06H-F5` **掉电事件**」；`用例库/STA/台账/123…md:76`（行 38 步骤 6-7）同错 | `event_report_06hf5` 判据不能照抄预期列，要按注入帧里的 OAD/DI 判 |
| 3 | **`03H-F7`「从节点监控最大超时时间」vs `05H-F3` 双语义** | `用例库/CCO/能力域/156…md:43`（`03H-F7` 设置/查询超时）与 `:56`（`05H-F3` 广播校时 20 步）在源表里混用了「`05H-F3` 设功率 / 校时 / 设超时」三种语义（`用例库/STA/台账/123…md:81` 行 43 用 `05H-F3` 设功率） | 涉及 `05H-F3` 的 D 批行（`clock_broadcast_05hf3`）落地前必须按 1376.2 原文定 Fn |
| 4 | **浙江黑名单用例步骤与预期序号错位** | taxonomy §2.2 已登记（`156…md:131`「按内容而不是编号对齐」） | `access_control_bj_zj` 若落地需重排步骤 |
| 5 | **「工作状态/当前状态」取值口径** | `用例库/CCO/台账/144…md:41`（步骤 4「工作状态=1、当前状态=1 搜表」）vs `156…md:69`（`10H-F4` 「工作状态=1、当前状态=00 抄表」） | 现块按「工作状态=抄表」判（`nwk_route_status.json`），与台账字面不完全一致；B4 落地时以块口径为准并记差异 |

---

## 附：与 0042 taxonomy 的对齐说明

1. 本文件的 `场景/省份/协议/设备域` 取值**全部**来自 taxonomy §1.3/§1.4/§1.5/§1.2 的枚举，未新增取值（唯一例外是表格里为了排序把 `reset` 与 `bench_prep` 并写，二者 taxonomy §4.3 已收为 `scene` 取值）。
2. taxonomy §3 已把「今日范围（组网 + 抄表）」28 条逐条判过覆盖度，本清单**不重复**那些条目，只在其上加「抄表扩展项」（多 DI / 费率 / 多表地址 / 地址域口径）与「档案扩展项」（数量链 / 分页 / 幂等 / 空表）。
3. taxonomy §3.2 缺块汇总里的 6 类缺块（`nwk.archive_detail`、`meter.read_all`/`read_batch`、`nwk.band_set`/`band_query`、`meter.delay_fix`/`route_14h`、`meter.read_698`、`nwk.listener_frame`）与本文件 D 批的「缺什么」逐条对应（本文件把它们放进了具体候选行）。
