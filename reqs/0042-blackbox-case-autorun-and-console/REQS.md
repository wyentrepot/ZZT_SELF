# REQS.md — 需求基线（需求 ID：0042，标题：黑盒用例自动运行与控制台）

> 本文件遵循「需求只追加、不覆盖」ADR 模式。
> 立项日期：2026-11-23；基线版本：**v1**。
> 依据：**REQS-0041**（原子块 + 事实 + 编排四层模型，已交付 15 块 / 8 事实 / 5 用例 / 3 方案）。
> 本需求是 0041 的**收口需求**：把「块编排」变成真正**零参与的黑盒执行**，并补上 API + UI。
> 全部现状与缺口均由 2026-11-23 真实台架联调实测确认，证据见 §1.5 与 §8。

---

## 1. 背景与现状

### 1.1 用户诉求（原话口径）

> 「测试用例，本质就是这些块的一个编排，一个块就是一个动作，比如复位，比如下发档案，下发抄表。
> 我要的是一个测试用例是一项，背后是一串编排动作。我希望不论谁来调用这串编排动作，只需要做一件事，
> 就是**入参运行**即可。点击运行或者脚本运行之后，一个测试用例，从开始执行到结束或者失败，
> **全程不需要任何 AI、人工参与，也不允许参与**。这是人工与 AI 的**黑盒**。」

拆成四条硬性判据（本需求的验收根）：

| 编号 | 判据 | 含义 |
|---|---|---|
| **D-1** | **单入口** | 调用方只做一件事：给入参 + 触发运行。没有第二步。 |
| **D-2** | **零参与** | 从触发到出结论，全程不需要人看屏、不需要 AI 判断、不需要人工补参数或解冲突。 |
| **D-3** | **结论可分** | 结论必须能区分「被测对象真失败」/「台架不具备条件」/「说不清」，三者不可混淆。 |
| **D-4** | **可复现** | 同入参 + 同台架状态 → 同结论；每次运行落盘足够证据供事后复现。 |

### 1.2 前序资产（REQS-0041 已交付，本需求直接复用，不重写）

```
BLOCKS(15)  archive.add / archive.del / archive.query
            fix.simcon_session / fix.simeter_session
            meter.addr_probe / meter.brain_local / meter.read_645
            nwk.active_registration / nwk.archive_count / nwk.nodes_online
            nwk.onnet_count(兼容别名) / nwk.route_restart / nwk.route_status / reset.sta
FACTS(8)    archive.contains / bench.simcon_online / bench.simeter_online / bench.sta_online
            nwk.nodes_online_min / nwk.onnet_count_min / nwk.route_running / sta.meter_addr_known
CASES(5)    bench_ready / sta_reset / archive_ready / nwk_join / meter_read
SUITES(3)   smoke_meter(5) / smoke_meter_noreset(4) / meter_only(3)
TEST        444 passed / 2 skipped；loader problems == []
CLI         tools/scripts/case_run.py（list/show/run/probe/check，退出码 0/1/2/3/4）
```

四层模型（0041 §10.2）：`Case（编排）→ Block（原子块）→ Fact（事实）→ Capability（能力适配）`。
**本需求不改这四层的边界与契约**，只在其上补「零参与」所需的能力。

### 1.3 真实台架已验证的链路（2026-11-23 实测）

```
simcon(COM4) → CCO(COM12) → HPLC → STA(COM18) → 表口 → 模拟电表(COM20)
```

`meter_read` 用例实测 **3/3 PASS**，读到「(当前)正向有功总电能 = **1234.56 kWh**」。
`smoke_meter_noreset` 方案实测：`bench_ready PASS / archive_ready PASS / meter_read PASS / nwk_join INCOMPLETE`。

### 1.4 台架端口（配置已就绪，`config_stale=false`）

| mapping_id | Windows COM | 角色 |
|---|---|---|
| `simcon` | COM4 | 模拟集中器（发帧） |
| `simeter` | COM20 | 模拟电表（STA 表口上的自动应答从站） |
| `sta-main` | COM18 | STA 日志 + 控制台（`reset.sta` 走此口发 `reboot`） |
| `cco-main` | COM12 | CCO 日志 + 控制台 |
| `listener` | COM27 | 侦听台（本需求不接） |

### 1.5 距离「零参与」的五个缺口（**本需求必须全部消除**，均有实测证据）

#### G-1【最严重】地址角色混淆 —— 调用方必须自己知道传哪种地址

实测事实（0041 §10.16）：**电表的 645 地址与 CCO 档案地址互为 6 字节整体反转**。

```
电表 645 地址（人读 "123456789012"，645 地址域低字节在前、不反转）  线序 12 34 56 78 90 12
CCO  档案地址（人读 "129078563412"，1376.2 编码器会整体反转）       线序 12 34 56 78 90 12
                    ↓
        档案人读地址 = reverse6(电表 645 人读地址)
```

现状缺陷：

| 块 | 参数 `addr` 语义 | 默认值 |
|---|---|---|
| `archive.add` / `archive.del` / `archive.query` | **档案显示地址** | `000000000001` |
| `meter.addr_probe` / `meter.read_645` / `meter.brain_local` | **电表 645 地址** | `000000000001` |

两者字符串相同但语义相反。对电表 `000000000001`，正确的档案地址应是 `010000000000`。
**历史实证**：联调前 CCO 档案里同时存在 `010000000000` 与 `000000000001` 两条，说明前人只能靠手工补两条来试。

→ **这是调用方（人或 AI）必须介入才能跑通的东西，直接违反 D-2。**
→ 修法见 §3.1。

#### G-2 重复运行不幂等

`archive.add` 不做「已存在」判断。日常回归第二次跑时档案已在表里，CCO 可能回否认帧 → 用例判 FAIL。
**日常开发后的验证必须可重复跑**，否则 D-4（可复现）不成立。

#### G-3 学习态不自动等待

`nwk.route_status` 在 CCO「工作状态=学习」时判 `inconclusive`。实测该状态可持续 >1 分钟。
「组网是否完成」本应**等待进入抄表态**（有超时），而不是跑一次就下结论。
→ 现在的行为是诚实的，但不是**自动化**的：需要人来判断「要不要再等等」。违反 D-2。

#### G-4 台架异常不自愈

端口被僵尸会话占用、上一轮用例异常退出留下的会话 → 块返回 `skipped`，不自愈。
0041 之前的 `autotest` 有自愈先例（检测到僵尸会话占口 → close 后重试一次）。
→ 需要人重启服务才能继续，违反 D-2。

#### G-5 缺 suite 级 preflight 与失败三分类

台架没起时，现有实现是**逐用例 skipped**，调用方要自己读懂「5 条用例 4 条 skipped」意味着什么。
且 `skipped`（台架不具备）与 `unknown`（说不清）目前都折算成 `inconclusive`，**无法区分**。
→ 违反 D-3。

---

## 2. 目标

一句话：**让一条用例/一个方案，变成「给入参 → 触发 → 拿到可信结论」的单一黑盒操作，
全程零 AI / 零人工参与，且结论能区分「被测对象失败」/「台架不具备」/「说不清」。**

并补齐 API 与 UI（0041 的 API-* / UI-* 需求整体移入本需求）。

---

## 3. 框架设计

### 3.1 地址角色化（消除 G-1，本需求技术核心）

**原则：调用方只认识一个地址 —— 电表的 645 地址。其余全部由系统推导。**

```
调用方给：meter_addr（电表 645 人读地址，如 "123456789012"）
                │
     ┌──────────┴───────────────────────────────────┐
     │ common.py 提供唯一换算函数（只此一份实现）：   │
     │   archive_addr_of(meter_addr) -> 12 位串      │  = reverse6(meter_addr)
     │   meter_addr_wire(meter_addr) -> 6 字节        │  = bytes.fromhex(meter_addr)（不反转）
     └──────────┬───────────────────────────────────┘
                │
   ┌────────────┴─────────────┬──────────────────────┐
   │ 11H-F1/F2 档案           │ 02H-F1 内层 645       │ 10H-F2 判据
   │ 用 archive_addr_of()     │ 用 meter_addr_wire()  │ 用 archive_addr_of()
   └──────────────────────────┴──────────────────────┘
```

**块参数契约变更**：

| 块 | 旧参数 | 新参数（**唯一权威**） | 说明 |
|---|---|---|---|
| `archive.add` / `archive.del` | `addr` / `meters` | **`meter_addr`**（单）/ `meter_addrs[]`（多） | 内部换算成档案地址再构帧 |
| `archive.query` | `addr` | **`meter_addr`** | 回查判据同样按 meter_addr 比对 |
| `meter.addr_probe` / `meter.read_645` / `meter.brain_local` | `addr` | **`meter_addr`** | 构 645 帧用它 |
| 事实 `archive.contains` / `sta.meter_addr_known` | `addr` | **`meter_addr`** | — |

- 旧参数 `addr` **保留为兼容别名**（映射到 `meter_addr`，并打 warning），不得破坏 0041 已交付的声明。
- **禁止**在块内各自实现反转逻辑：只允许 `common.py` 一处（identity 断言守）。
- 换算函数必须有单测覆盖：`123456789012 ↔ 129078563412`、`000000000001 ↔ 010000000000` 双向。

### 3.2 幂等（消除 G-2）

- `archive.add`：先 `10H-F2` 查档；**已存在** → 回 `pass` + detail「已存在，跳过下发」（不是 fail）。
  不存在才下发 `11H-F1`。下发后仍做「确认 + 回查」双判据。
- `archive.del`：不存在 → `pass` + detail「本就不存在」。
- `reset.sta`：可重复跑（本身幂等）。
- **验收**：同一方案连跑两次，两次结论一致（不得第二次因「档案已存在」而 FAIL）。

### 3.3 等待与超时（消除 G-3）

新增块参数 `wait_s`（等待上限，默认按块语义给）：

| 块 | 等待语义 | 默认 `wait_s` |
|---|---|---|
| `nwk.route_status` | 轮询直到「工作状态=抄表」，或超时 | 60 |
| `nwk.nodes_online` | 轮询直到「已抄数 ≥ min 或 信号品质>0 的节点 ≥ min」 | 90 |
| `reset.sta` | 已有（等启动指纹，30s） | 30 |

- 轮询间隔 2s；**超时仍不满足** → 按 §3.5 分类（台架不具备 → `blocked`；说不清 → `inconclusive`）。
- 等待期间**不得产生额外激励**（只重复只读查询）。
- 学习态持续超过 `wait_s` → `inconclusive`，detail 写明「等了 N 秒仍处学习态」。

### 3.4 自愈（消除 G-4）

定义「可自愈前置」清单，块执行前若命中则尝试自愈一次（**仅一次**，不自愈成死循环）：

| 症状 | 自愈动作 | 失败后 |
|---|---|---|
| simcon 串口被占（409 / "已被后端会话占用"） | `POST /api/simcon/close` 释放后重试一次 | `blocked` + 明确原因 |
| simeter 端口被占 | `close_port()` 后重试一次 | `blocked` |
| module 会话残留 | 重新 `ensure` 一次 | `blocked` |

- 自愈必须**留痕**：结果里记 `self_healed: [{action, result}]`，便于 D-4 复现。
- **禁止**：自愈去 kill 进程、重启工作台、断电 —— 那些属人工运维。

### 3.5 失败三分类（消除 G-5，D-3 的落点）

| 块状态 | 含义 | 聚合到用例 verdict | 聚合到方案 verdict |
|---|---|---|---|
| `pass` | 判据成立 | `pass` | `pass` |
| `fail` | **被测对象真失败**（收到应答但判据不成立） | `fail` | `fail` |
| `skipped` | **台架不具备条件**（服务不可达/端口占用/闸门忙/前置事实不满足） | **`blocked`** | `blocked` |
| `unknown` | **说不清**（超时无应答/解析不出/等待超时仍学习） | `inconclusive` | `inconclusive` |

优先级（同一用例内多块混合时取最严重）：`fail` > `blocked` > `inconclusive` > `pass`。

- **suite 级 preflight**：`POST /api/cases/run` 开始前先探 `bench.*` 事实；不满足 → 整个 run
  直接 `blocked` + 逐项列出缺什么，**不逐用例跑一遍再报 skipped**（省时且结论更清楚）。

### 3.6 API 层（0041 API-1/2/3 移入本需求）

工厂 `create_cases_router(storage_dir) -> APIRouter`，`prefix="/api/cases"`，
在 `apps/workbench/app.py` 中**紧跟现有 autotest 挂载之后** `include_router`。
**base_url 注入，不得写死 8790。**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/cases` | 用例列表（id/title/steps/blocks/needs/tags） |
| GET | `/api/cases/blocks` | 块目录（id/title/needs/provides/action 摘要/参数面） |
| GET | `/api/cases/facts` | 事实目录 |
| GET | `/api/cases/suites` | 方案列表 |
| POST | `/api/cases/suites` | 保存方案（校验 case_ids 引用） |
| DELETE | `/api/cases/suites/{id}` | 删除方案 |
| GET | `/api/cases/bench` | **台架就绪快照**（5 口 usable + 8 事实三态），供 UI 置灰/预检 |
| POST | `/api/cases/run` | 运行：`{suite_id}` 或 `{case_ids}`，可带 `{params}`、`{base_url}` → 返回 **job_id（异步）** |
| GET | `/api/cases/jobs/{job_id}` | job 状态 + 进度 + 终态结果 |
| GET | `/api/cases/runs/{run_id}` | 历史结果（落盘 `data/case_runs/<run_id>/result.json`） |

**硬性**：判定逻辑一行都不许写在 API 里（只做装配 + 调度 + 落盘）。

### 3.7 UI 层（0041 UI-1..5 移入本需求）

页面 `apps/workbench/static/pages/cases/{cases.html,cases.js}`，导航表 `static/app.js` 加一条。

| 需求 | 内容 |
|---|---|
| **UI-1 用例列表** | 每行 = **用例名 + 块数 + 步骤摘要**；展开见完整 `do/expect` |
| **UI-2 台架就绪横幅** | 顶部显示 5 口 + 关键事实三态；不满足时**运行按钮置灰**并给原因（对应 §3.5 preflight） |
| **UI-3 勾选 → 存方案** | 勾选用例 → 存为方案 → **刷新后勾选随方案恢复** |
| **UI-4 两级运行** | 「▶ 立即运行」单例 + 「▶ 按方案运行」；job 面板轮询显示进度与当前用例 |
| **UI-5 断点可视化**（重点） | 失败/阻断用例：高亮问题块，列出 `failed_block` / `blocked_block` / `unmet_facts` 及原因 |
| **UI-6 结论三态着色** | pass 绿 / fail 红 / blocked 灰（台架问题，不是产品问题）/ inconclusive 黄 |
| **UI-7 主题合规** | 沿用 0012 三层 token，仅用 `var(--color-*)`，主题静态校验 0 issue |

---

## 4. 需求点（编号即验收锚点）

### 4.1 地址角色化

| 编号 | 需求 | 验收 |
|---|---|---|
| AD-1 | `common.archive_addr_of()` / `common.meter_addr_wire()` 唯一实现 | 双向换算单测通过；`grep -rn "\[::-1\]" libs/case_runner/blocks/` 只命中 common.py |
| AD-2 | 全部块/事实参数由 `addr` 改为 `meter_addr`（`addr` 保留别名） | 0041 既有 5 用例声明可正常运行（兼容性） |
| AD-3 | 用例/方案层只需给 `meter_addr` 一个参数即可跑通抄表链 | 真实台架：`--params '{"meter_addr":"123456789012"}'` 跑 `meter_read` 得 3/3 PASS |

### 4.2 幂等

| 编号 | 需求 | 验收 |
|---|---|---|
| ID-1 | `archive.add` 已存在 → pass + 「已存在」 | 连跑两次同一方案，两次结论一致 |
| ID-2 | `archive.del` 不存在 → pass | 空档案时跑 del 不 FAIL |

### 4.3 等待

| 编号 | 需求 | 验收 |
|---|---|---|
| WT-1 | `nwk.route_status` 支持 `wait_s`（默认 60）轮询到抄表态 | 学习态台架上等待后能拿到确定结论（pass 或超时 inconclusive） |
| WT-2 | `nwk.nodes_online` 支持 `wait_s`（默认 90） | 同样 |
| WT-3 | 等待期间零额外激励、轮询间隔 2s | 帧日志可证（只有只读查询帧） |

### 4.4 自愈

| 编号 | 需求 | 验收 |
|---|---|---|
| SH-1 | 端口占用自愈一次（simcon/simeter/module） | fake 409 后 close + 重试 → pass；再失败 → blocked |
| SH-2 | 自愈留痕 `self_healed[]` | 结果里可见 |
| SH-3 | 禁止自愈去 kill/重启服务 | 代码审查 + 无 subprocess/pkill 调用 |

### 4.5 失败三分类

| 编号 | 需求 | 验收 |
|---|---|---|
| VF-1 | verdict 增加 `blocked`；`skipped→blocked`、`unknown→inconclusive` | 单测覆盖四种状态聚合与优先级 |
| VF-2 | suite 级 preflight：台架不满足 → 整个 run `blocked` + 缺项清单 | 台架全关时 run 在 1s 内返回 blocked，不逐用例跑 |
| VF-3 | 绝不假 PASS（继承 0041） | `unknown`/`skipped` 永不计入 PASS 计数 |

### 4.6 API

| 编号 | 需求 | 验收 |
|---|---|---|
| API-1 | 10 个端点（见 §3.6）全绿 + 单测 | 端点单测 + 错误路径（404/422/409） |
| API-2 | 异步 job（复位 30s、抄表数十秒，不能同步阻塞） | `POST /run` 立即返回 job_id；轮询到终态 |
| API-3 | `/api/cases/bench` 返回台架就绪快照 | UI 可直接用 |
| API-4 | `/api/autotest/*` 与既有页面不回归 | `test_autotest_api.py` / `test_app.py` 全绿 |

### 4.7 UI

| 编号 | 需求 | 验收 |
|---|---|---|
| UI-1..7 | 见 §3.7 | 浏览器全链路实测：勾选→存方案→刷新恢复→运行→见三态结论与断点 |

---

## 5. 非目标（明确不做）

- ❌ **不改** 0041 的四层模型边界（Case/Block/Fact/Capability 职责与依赖方向不变）
- ❌ **不改** `libs/case_library`（279 条只读字典）、`libs/test_automation` 模型契约
- ❌ **不破坏** `/api/autotest/*` 与现有 autotest 页面
- ❌ **不接侦听台**、不接 CCO 模块日志事件作判据（沿用 0041 边界）
- ❌ **不做真固件烧录/激活**（属 `wb-flash` / `hs-dw31-upg` 领域）
- ❌ **不做** 用例录制/回放、跨机分布式调度
- ❌ **自愈不做**进程级操作（kill/重启服务/断电），只做接口级释放与重试
- ❌ **不做** 97 条 gw_cass 声明式用例的断言求值（0041 已降为可选）

---

## 6. 验收标准（DoD）

1. **零参与实测**：在**不看屏、不做任何判断**的情况下，敲一条命令跑完整个方案并拿到结论。
   ```
   case_run run --suite smoke_meter --params '{"meter_addr":"123456789012"}'
   ```
   人工只做两件事：给 `meter_addr`、按回车。**中间无任何提问、无补参、无解冲突。**
2. **单参数**：抄表链路只需 `meter_addr` 一个地址参数（AD-3 实测 3/3 PASS）。
3. **可重复**：同一方案连跑两次，两次 verdict 一致（ID-1）。
4. **三态可分**：分别构造「被测对象失败」「台架不具备」「说不清」场景，verdict 分别为
   `fail` / `blocked` / `inconclusive`，UI 着色正确（VF-1/VF-2/UI-6）。
5. **自愈可用**：制造端口占用，用例能自愈一次并通过；自愈失败给 `blocked` + 原因（SH-1/2）。
6. **等待有效**：学习态台架上 `nwk.route_status` 在 `wait_s` 内等到抄表态或给出超时 inconclusive（WT-1）。
7. `pytest libs/case_runner apps/workbench/test_cases_api.py -q` 全绿；分层铁律
   `test_layering.py` 仍全绿；`grep '^from apps' libs/` 仍 0。
8. `/api/autotest/*` 不回归。
9. UI 浏览器全链路：勾选→存方案→刷新恢复→运行→见三态结论与断点。
10. 每次运行落盘 `data/case_runs/<run_id>/result.json`，含 `verdict`/`failed_block`/`blocked_block`/
    `unmet_facts`/`self_healed`/台架快照，满足 D-4 可复现。

---

## 7. 风险

| 风险 | 应对 |
|---|---|
| 地址角色化改动面大（5 块 + 2 事实 + 5 用例 + 3 方案） | 先加换算函数 + 别名兼容，再逐块切换；每步跑 0041 既有 444 测试防回归 |
| 等待参数拖长单次运行（复位 30s + 组网 60s + 抄表 40s ≈ 2min） | job 异步 + 进度回报；UI 显示当前用例而非空转 |
| 自愈误伤别人正在用的端口 | 自愈只释放**本 run 自己检测到**的占用；复用的端口绝不自动 close（沿用 0041 capability 边界） |
| `blocked` 语义被滥用成「万能借口」 | 严格定义：只有 §3.5 表里列的 skipped 来源才算 blocked；其余一律 fail/inconclusive |
| 学习态在当前台架可能长期存在 | `wait_s` 有界；超时给 inconclusive 并写明细；**不为了变绿而放宽成 pass** |

---

## 8. 已知技术事实（引自 REQS-0041 实测，实现时必须遵守）

### 事实 1：地址字节序（§10.16）

```
电表 645 地址 人读 "123456789012"  → 645 地址域线序 12 34 56 78 90 12（不反转）
CCO 档案地址  人读 "129078563412"  → 1376.2 编码器整体反转后线序 12 34 56 78 90 12
```

- 适配器 `_bcd_bytes(人读) → 线序` **做整体反转**（正确，已真机实证）
- 电表 `sim_meter._parse_addr(人读) → 线序` **不反转**（在 645 语境正确）
- → `archive_addr_of(meter_addr) = reverse6(meter_addr)`

### 事实 2：`10H-F1` 的「从节点总数量」是**档案数**，不是**在网数**

元数据原文：「当前路由档案中的从节点总数」。
真在网判据必须用 `10H-F4` 的「已抄从节点数量」或 `10H-F2` 的逐节点「信号品质 > 0」（`nwk.nodes_online` 已实现）。

### 事实 3：`10H-F4` 上行应用数据是 **16 字节**（不是 17）

CCO 固件 `gw13762.c:4612-4687` 逐字节写 `buff[0..15]` 后 `p_tx->bufflen = 16`。
解析门限已修为 `>= 16`；**测试样本必须 16B**，多/少 1 字节会掩盖解析缺陷。

### 事实 4：STA 复位手段

经 `sta-main` 会话向 STA 控制台发 **`reboot`**（补 CRLF）。判据是日志出现
`Unicorn Bootloader` / `Run image0` / `reset reason:` + `SFT_GLB_RST`（不是命令返回码）。
复位到网络层恢复约 10–20s。存在 `reboot now` 变体，命令文本须参数化。
CCO 控制台**没有** `sta reset`；`01H-F1` 复位的是 **CCO 自己**。

### 事实 5：模拟电表必须开口才能应答

`simeter` 不会自动开串口；须 `open_port(mapping_id="simeter")`。
`fix.simeter_session` 已实现 `ensure_open`（默认 true）且**不自动关闭**（后续抄表块要它在线）。
simcon 侧则由 `execute_task` 自行管理端口生命周期，无需外部干预。

### 事实 6：`meter.brain_local` 构帧

`68 | meter_addr 字节原样 | 68 | 11 | 04 | DI 传输序 | CS | 16`，
CS = 除尾 2 字节求和低 8 位。已有共用函数 `meter.build_645_read_frame(addr, di)`。

### 事实 7：`nwk.active_registration`（`11H-F5`）必填四参

`time6`(6B BCD, `SSMMHHDDMMYY`) + `duration`(2B LE) + `retry`(1B) + `slices`(1B)。
当前块内 `time6` 是写死时间串 → 若要按「当前时刻」激活，需由 §3.3 等待或运行期取值补上。

### 事实 8：既有测试基线

`pytest libs/case_runner -q` → **444 passed, 2 skipped**；
其中 1 条 skip 依赖台架当前表地址（外部状态，非缺陷），1 条是 live gate（需 `CASE_RUNNER_LIVE=1`）。

---

## 9. 变更记录

### 变更 1 ｜ 2026-11-23 ｜ 立项
- **改成什么**: 建立需求基线 v1（黑盒零参与执行 + 地址角色化 + 幂等/等待/自愈/三分类 + API + UI）
- **为什么**: 用户要求「用例 = 块编排，入参即跑，全程零 AI/人工参与」；0041 交付的块编排已跑通真实台架，
  但仍有 5 个参与点（地址角色混淆、不幂等、学习态不等待、不自愈、失败不分三态）使其不成立
- **影响**: 新增 `apps/workbench/cases_api.py`、`pages/cases/`；改 `libs/case_runner/blocks/*` 参数与判定聚合；
  不改四层边界、不改 `case_library` / `test_automation` / `/api/autotest/*`
- **变更前基线**: 无（新建）
- **变更后基线**: v1（本文）
- **被取代**: 无
