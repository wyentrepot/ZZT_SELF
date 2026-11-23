# PLAN.md — 需求 0043 执行计划（用例库扩充与长跑验证）

> **依据基线**：`REQS-0042` **v10**（黑盒用例自动运行与控制台）+ `REQS-0043` **v1**（用例库扩充与长跑验证）。
> **生效条件**：上述两份 REQS 的当前版本分别仍为 v10 / v1；若任一被升版，或 0042 的四态 verdict / 零介入不变量（D4-1..D4-6）/ 等待预算规则（0042 PLAN §0.4）被改动，则**本文件失效，不得执行**，须按新基线重新规划。
> 生成日期：2026-11-23。生成者：`req0043-plan`（共享任务板 `task-19`）。
> 角色纪律（继承 0042）：**Lead 拆任务 / 派发 / 审查 / 跑台架门禁，不自己啃大块编码**；子代理**一律禁止**跑 `case_run run` / `probe --all` / 任何写接口 curl（0042 PLAN §8「单测打到真实工作台」事故的硬规矩）。
> 台架归属：**只有 Lead 动手**。台架相关命令在本文件里标 `【Lead】`，其余命令任何执行者都可跑。

---

## 0. 目标、边界与环境

### 0.1 一句话目标

在 0042 已打通的「组网 + 抄表」链路上，**按知识库口径盘点用例库、补齐可测用例、并做重复执行与时长验证**，给出「哪些已覆盖 / 哪些本环境不可测（缺什么）/ 下一批补什么」的收敛结论；全程不改四态模型与 0042 的 API/UI 契约。

### 0.2 不变边界（今天不碰）

| 不碰 | 依据 |
|---|---|
| 四层模型边界（Case / Block / Fact / Capability）与依赖方向 | `REQS-0043` §3 |
| `libs/case_library`（279 条只读字典）、`libs/test_automation` 模型契约、`/api/autotest/*` | `REQS-0042` §5 |
| 0042 的 `/api/cases/*` 契约与用例库 UI 交互（只允许**加可选声明字段**，见 §4 P2-1，不改既有字段语义） | `REQS-0043` §3 |
| 场景族枚举（不新增 `scene` 取值：**新增用例只用 `nwk` / `meter_read`**；既有 7 条沿用它们已有的 `archive` / `reset` / `bench_prep`） | `REQS-0042` D10-1；`REQS-0043` §3 |
| 真固件烧录 / 激活 | `REQS-0043` §3（属 `wb-flash` / `hs-dw31-upg`） |

### 0.3 环境与前提（实测事实，2026-11-23 复核代码与落盘产物）

| 项 | 事实（含取证位置） |
|---|---|
| 解释器 | `.venv/bin/python`（3.10）；`python` 不存在，别用 |
| 仓库根 | `/01-workfile-ai/01-zzt/ZZT_SELF`（所有命令先 `cd` 到这里） |
| 台架链路 | `simcon(COM4) → CCO(COM12) → HPLC → STA(COM18) → 表口 → 模拟电表(COM20)`；侦听台 `listener(COM27)` 本需求不接 |
| **串口桥前置** | **Windows 侧串口桥必须先起**（`serial_bridge.py windows`）；不起则 5 口全不可用 → 每条用例一律 `blocked`，长跑会立刻触发停机条件 |
| 复位代价 | **复位 STA 后链路恢复实测 ≈10 分钟**（0042 D9-1 实测 16:20 复位 → 16:45 才恢复 2.5s 读取）→ **长跑主线绝不复位**，只用 `meter_daily`（不复位） |
| 单轮耗时基线 | `meter_smoke_min` 单轮实测 **≈17s**（`data/case_runs/20261123-174852-76efbf/result.json`：`duration_s=16.975`、`verdict=pass`） |
| 工作台进程 | 单进程承载 workbench + simcon + simeter（`apps/workbench/app.py` 内 `create_simcon_app` / `create_simeter_app` 同进程挂载）→ RSS 只需观测 1 个 pid |
| 工作台 pid 来源 | `data/runtime/workbench.json` 的 `pid`（本文件写于 `apps/workbench/run.py`，D2-3） |
| RSS 观测工具 | **无 `psutil`**（实测 `ModuleNotFoundError`）→ 用 `/proc/<pid>/status` 的 `VmRSS`；子进程峰值用 `/usr/bin/time -v` 的 `Maximum resident set size`（实测 `/usr/bin/time` 存在） |
| 只读块清单 | `nwk.topology` / `nwk.route_status` / `nwk.nodes_online` / `nwk.onnet_count` / `nwk.archive_count` / `archive.query` 声明 `tags` 含 `readonly` → `probe --readonly` 只跑这 6 个 |
| 用例 API 状态 | 编写本 PLAN 时 `apps/workbench/app.py` **尚未挂载 cases router**（0042 S4 由 `s4-api` 进行中）→ 本需求验收**只用 CLI 主线**（CL-3 / CL-5 / CL-7 均不依赖 API）；P0-1 实测其可达性并记录 |
| 结论四态 | `pass` / `fail` / `blocked` / `inconclusive`；退出码 `0=pass / 1=fail / 5=blocked / 2=inconclusive`，另 `3=用法错 / 4=声明加载失败`（`libs/case_runner/cli.py`） |
| `result.json` 字段现状 | `run_id / params / resolved_params / started_at / finished_at / duration_s / verdict / exit_code / preflight / bench / decisions[] / interventions[] / self_healed[] / cases[].{failed_block,blocked_block,failed_step,blocked_step,blocks[].step_id}`（实证文件同上） |

### 0.4 沿用 0042 的两条硬规则（本需求逐条遵守）

| 规则 | 内容 | 本需求落点 |
|---|---|---|
| **零介入不变量 D4-1..D4-6** | 参数启动瞬间冻结、执行路径零 stdin / 零 AI、`interventions[]` 恒空、有界终局（`--timeout` 默认 600s）、单条命令取证 | 长跑每轮都带 `< /dev/null`；每轮校验 `interventions == []`；任何非空即该轮作废并停跑 |
| **等待预算 §0.4** | **一条用例只允许一个等待型判据**；其余等待型块必须显式 `"wait_s": 0`；预算在 `check` 阶段静态核算，`Σ 固定开销 + Σ wait_s + 抄表预算 ≤ 600s` | 新增用例的 `plan` 一条一等待；`cco_nodes_online` 的 s1 不带 `wait_s`、s2 显式 90s |

### 0.5 全阶段共用门禁（每条都要贴出输出）

```bash
cd /01-workfile-ai/01-zzt/ZZT_SELF
.venv/bin/python -m pytest libs/case_runner -q
# 期望：全绿（0 failed）；用例条数 ≥ P0 记录的基线（见 evidence/0043/P0-baseline.txt）

.venv/bin/python -m pytest libs/case_runner/test_layering.py -q
# 期望：全绿（分层铁律不变）

grep -rn '^from apps' libs/ ; echo "exit=$?"
# 期望：无匹配输出，exit=1

grep -rn 'input(\|sys.stdin' libs/case_runner --include=*.py | grep -v /test_ ; echo "exit=$?"
# 期望：无匹配输出，exit=1（D4-2 零交互）

.venv/bin/python tools/scripts/case_run.py check --json
# 期望：{"ok": true, ..., "problems": []}，且退出码 0
```

---

## 1. 阶段与任务总览

| 阶段 | 主题 | 需求点 | 任务数 | 写入范围（互斥） |
|---|---|---|---|---|
| **P0** | 开工门禁（基线 + 台架 + 分类文档） | — | **4** | 只读 + `evidence/0043/` |
| **P1** | 用例库盘点与可测性标注 | CL-1 / CL-2 | **4** | `reqs/0043-*/CASE-BACKLOG.md` |
| **P2** | 补用例（CCO ≥1 / STA ≥1） | CL-3 / CL-4 | **5** | `libs/case_runner/core/models.py`、`libs/case_runner/cli.py`（只加展示）、`libs/case_runner/data/cases/`、`libs/case_runner/data/case_suites/`、`libs/case_runner/test_cases_declaration.py`、`libs/case_runner/test_case_category.py`（新建） |
| **P3** | 长跑验证（≥20 轮 + ≥2h） | CL-5 / CL-6 / CL-7 | **5** | `tools/scripts/case_soak.py`（新建）、`tools/scripts/test_case_soak.py`（新建）、`evidence/0043/` |
| **P4** | 收口（收敛报告 + DONE） | CL-8 | **3** | `reqs/0043-*/CASE-CONVERGENCE.md`、`reqs/0043-*/DONE.md`、`reqs/0043-*/TODO.md` |

**任务总数：21**（P0 4 / P1 4 / P2 5 / P3 5 / P4 3）。
**派发原则**：P0 必须由 Lead 完成；P1 一个子代理；P2 一个子代理（写入范围与 0042 的 `s4-api`（`apps/workbench/cases_api.py`）、`verdict-model`（`core/models.py`）**互斥**，必须等 0042 收口后再动——见 P0-1 门禁）；P3 一个子代理写驱动器 + Lead 跑台架；P4 Lead 收口。

---

## 2. P0：开工门禁（4 条任务，Lead 执行）

> 本阶段任何一条不通过 → **本需求整体不启动**，先回 0042 收口。

### P0-1 0042 交付基线复核

- **目标**：确认 0042 的代码/测试/声明基线仍绿，且四态 verdict + 零介入不变量在场。
- **文件**：只读；产出 `evidence/0043/P0-baseline.txt`（新建）。
- **先看（前置失败检查）**：直接跑 §0.5 的五条共用门禁，**原样保存输出**：
  ```bash
  mkdir -p evidence/0043
  { .venv/bin/python -m pytest libs/case_runner -q 2>&1 | tail -3
    .venv/bin/python -m pytest libs/case_runner/test_layering.py -q 2>&1 | tail -3
    grep -rn '^from apps' libs/ ; echo "grep_from_apps_exit=$?"
    grep -rn 'input(\|sys.stdin' libs/case_runner --include=*.py | grep -v /test_ ; echo "grep_stdin_exit=$?"
    .venv/bin/python tools/scripts/case_run.py check --json
  } | tee evidence/0043/P0-baseline.txt
  ```
- **最小实现**：不写代码。把「实际通过数 / 声明 problems / 是否有 cases router 挂载」三项抄进 `evidence/0043/P0-baseline.txt` 的尾部注释行。
- **验证命令与期望**：
  ```bash
  .venv/bin/python tools/scripts/case_run.py list --cases --json \
    | .venv/bin/python -c "import json,sys; d=json.load(sys.stdin); print('cases=',d['counts']['cases']); print([c['id'] for c in d['cases']])"
  # 期望：cases= 7，id 含 bench_ready / archive_ready / nwk_join / sta_reset / meter_daily / meter_first_join / archive_multi
  curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/api/cases/bench
  # 期望：200（0042 S4 已落地）；若 404 → 在 P0-baseline.txt 记「cases API 未挂载，本需求只走 CLI 主线」
  ```
- **检查点**：
  - [ ] `pytest libs/case_runner` 0 failed，且条数已写入基线文件
  - [ ] `check` 的 `problems` 为 `[]`
  - [ ] 7 条用例齐全（`meter_read` 已按 0042 D9-1 拆成两条，不存在旧 id）
  - [ ] `/api/cases/bench` 的可达性已记录（200 或 404，二者都可继续，但必须写明）

### P0-2 台架 5 口自检（含 Windows 串口桥前置）

- **目标**：确认 5 口 `usable`，否则后续所有用例都会 `blocked`，长跑必停。
- **文件**：只读；产出 `evidence/0043/P0-bench.txt`（新建）。
- **先看（前置失败检查）**：串口桥状态（纯只读 GET，不是写接口）：
  ```bash
  curl -s http://127.0.0.1:8790/api/serial-bridge/status | tee evidence/0043/P0-bench-status.json \
    | .venv/bin/python -c "
  import json,sys
  d=json.load(sys.stdin); want=['simcon','cco-main','sta-main','simeter','listener']
  ready=set(d.get('ready_ports') or [])
  print('missing=',[w for w in want if w not in ready]); print('usable=',sorted(ready))
  print('unusable_detail=',d.get('unusable_ports'))"
  ```
- **最小实现**：不写代码。失败时的**唯一**处置：让用户在 **Windows 侧**先起串口桥（`serial_bridge.py windows`），再重跑上面命令；**不得**在工作台侧改配置绕过。
- **验证命令与期望**：
  ```bash
  .venv/bin/python tools/scripts/case_run.py probe --fact bench.simcon_online --json
  .venv/bin/python tools/scripts/case_run.py probe --fact bench.simeter_online --json
  .venv/bin/python tools/scripts/case_run.py probe --fact bench.sta_online --json
  # 期望：三条都 "state": "yes"；若 simeter 为 no/unknown，先【Lead】跑一次
  #   .venv/bin/python tools/scripts/case_run.py run --case bench_ready < /dev/null --json   （期望 pass，≈2s）
  ```
- **检查点**：
  - [ ] `missing= []`（5 口全 usable）
  - [ ] 三个 bench 事实 `yes`
  - [ ] `bench_ready` 单跑 `pass`（证明 simeter 表口能打开）

### P0-3 分类体系文档存在性与出处抽查

- **目标**：确认 0042 的 `CASE-LIBRARY-taxonomy.md` 已落盘且带出处，可作为 P1/P2 的唯一分类依据。
- **文件**：只读；产出 `evidence/0043/P0-taxonomy.txt`（新建）。
- **先看（前置失败检查）**：
  ```bash
  F=reqs/0042-blackbox-case-autorun-and-console/CASE-LIBRARY-taxonomy.md
  test -s "$F" && echo "EXISTS lines=$(wc -l < "$F")" || echo "MISSING"
  grep -c "库中未找到" "$F"
  grep -o "用例库/[^ \`：,)]*" "$F" | sort -u | wc -l
  ```
- **最小实现**：不写代码。基线实测值（2026-11-23）：`lines=353`、`库中未找到` 命中 **8** 处、`用例库/...` 唯一出处 **124** 个。低于该值 → 视为文档被裁，回到 0042 补齐再开工。
- **验证命令与期望**：
  ```bash
  grep -n "^### 1\.\|^### 2\.\|^### 3\.\|^### 4\.\|^### 4\.3" "$F"
  # 期望：1.1 usage / 1.2 domain / 1.3 scene / 1.4 region / 1.5 protocols / 4.3 字段最终形态 全部命中
  ```
- **检查点**：
  - [ ] 文档存在，且 `usage/domain/scene/region/protocols` 五维定义齐全
  - [ ] 今日范围清单存在（taxonomy §3：组网 14 条 N1..N14 + 抄表 14 条 M1..M14，含 1 条 `forbidden`）
  - [ ] 「库中未找到」清单存在（taxonomy §6）

### P0-4 PLAN 生效与基线钉死

- **目标**：把本文件的依据基线写死到证据里，避免「跑着跑着基线变了」。
- **文件**：只读；产出 `evidence/0043/P0-plan-freeze.txt`（新建）。
- **先看（前置失败检查）**：
  ```bash
  grep -n "依据基线\|生效条件" reqs/0043-case-library-expansion-and-soak/PLAN.md | head -3
  grep -nE "TO[D]O:|T[B]D|待补[充]|后续实[现]" reqs/0043-case-library-expansion-and-soak/PLAN.md ; echo "placeholder_exit=$?"
  ```
  （占位符检测刻意**不含** `TODO.md` 这个真实文件名；正则用 `[X]` 拆字，避免命中检测命令自身。）
- **最小实现**：`{ git rev-parse --short HEAD; grep -n "基线版本" reqs/0043-*/REQS.md reqs/0042-*/REQS.md; } | tee evidence/0043/P0-plan-freeze.txt`
- **验证命令与期望**：上面那条 `grep -nE` 期望**无匹配输出、`placeholder_exit=1`**（本文件无占位符）；0043 `REQS.md` 显示 `v1`，0042 `REQS.md` 显示 `v10`。
- **检查点**：
  - [ ] 提交号 + 两需求版本号已落证据
  - [ ] 本文件 0 占位符

---

## 3. P1：用例库盘点与可测性标注（CL-1 / CL-2，4 条任务）

> **依据**：`reqs/0042-*/CASE-LIBRARY-taxonomy.md`（P0-3 已验收）+ `reqs/0043-*/CASE-BACKLOG.md`（子代理 `kb-case-library` 产出）。
> **范围铁律**：今日只盘 **组网（`nwk`）+ 抄表（`meter_read`）** 两个场景族（`REQS-0042` D10-1）；其余场景只做「登记 + 优先级」，不标注可测性细节。

### P1-1 消费 `CASE-BACKLOG.md`：五维盘点表

- **目标**：把 taxonomy §3 的 28 条候选项（N1..N14 + M1..M14）落成 0043 的盘点表，每条带**知识库出处**。
- **文件**：`reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md`（消费或补写）。
- **先看（前置失败检查）**：
  ```bash
  test -s reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md && echo FOUND || echo MISSING
  ```
  - `FOUND` → 直接消费，只做**校验与补充**，不重写作者内容。
  - `MISSING` → 由本阶段执行者补写，**头部必须注明**「来源 = `reqs/0042-*/CASE-LIBRARY-taxonomy.md` §3/§4（`kb-case-library` 尚未落盘）」；表内容与 taxonomy 逐行一致，不得自造场景或判据。
- **最小实现**：表头固定为
  `| # | 候选 id | 场景 | 设备域 | 常用度 | 省份 | 协议 | 判据（原文摘） | 出处（文件:行） | 依赖块 | 覆盖(✅/⚠️/❌) | 可测性 | 不可测缺什么 |`
  行 = taxonomy §3.1（14 行）+ §3.2（14 行）+ taxonomy §4.1 的 7 条既有用例（标「已存在」）。
- **验证命令与期望**：
  ```bash
  B=reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md
  grep -c "^| N\|^| M" "$B"    # 期望：28
  grep -c "用例库/" "$B"        # 期望：≥ 40（出处列逐行有值）
  grep -c "库中未找到" "$B"      # 期望：≥ 1（显式登记查不到的项）
  ```
- **检查点**：
  - [ ] 28 条候选齐全，无遗漏、无自造
  - [ ] 每条有 `文件:行` 出处；查不到的写「库中未找到」
  - [ ] 表头 13 列齐全

### P1-2 可测性标注（CL-2）

- **目标**：逐条判定「本环境可测 / 不可测」，不可测必须具体到**缺哪个块 / 缺哪项能力 / 缺什么硬件 / 缺省份环境**。
- **文件**：同上（`CASE-BACKLOG.md` 的 `可测性` + `不可测缺什么` 两列）。
- **先看（前置失败检查）**：先拿到当前块清单作为「可测」判据的基线：
  ```bash
  .venv/bin/python tools/scripts/case_run.py list --blocks --json \
    | .venv/bin/python -c "import json,sys;print(sorted(b['id'] for b in json.load(sys.stdin)['blocks']))"
  # 期望：16 个块 = archive.add/archive.del/archive.query/fix.simcon_session/fix.simeter_session/
  #        meter.addr_probe/meter.brain_local/meter.read_645/nwk.active_registration/nwk.archive_count/
  #        nwk.nodes_online/nwk.onnet_count/nwk.route_restart/nwk.route_status/nwk.topology/reset.sta
  ```
- **最小实现**：标注口径（写进表头的图例，避免各行自说自话）：
  - `可测` = 只用上述 16 个块 + 8 个事实**不改块**就能表达该判据；
  - `可测（判据部分）` = 关键判据缺域（写清缺哪一段，例如「缺 `10H-F2` 明细比对」）；
  - `不可测` = 必须新增块；`缺什么` 列写块 id 建议（taxonomy §3 末尾已给 6 条缺块汇总，直接引用）；
  - `禁用` = `nwk.active_registration`（`11H-F5` 搜表）→ 标 `usage=forbidden`，**永不进用例**（`REQS-0042` D6-2）。
- **验证命令与期望**：
  ```bash
  B=reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md
  grep -c "| 可测 |\|| 可测（判据部分） |\|| 不可测 |\|| 禁用 |" "$B"   # 期望：≥ 28（每行都标了）
  grep -c "缺块" "$B"                                                  # 期望：≥ 15（taxonomy §3 统计）
  ```
- **检查点**：
  - [ ] 每行可测性非空
  - [ ] 每条 `不可测` 的原因具体到块/能力/硬件/省份
  - [ ] `search_meter` 已标 `禁用` 且写明依据（`REQS-0042` D6-2）

### P1-3 分档与第一批冻结（≤ 8 条）

- **目标**：按 `可测+common → 可测+normal → 可测+rare → 不可测` 分四档，冻结「第一批」（本需求今天要补并真机验证的用例）。
- **文件**：`CASE-BACKLOG.md` 增「§分档与批次」小节；第一批的逐条定义以本文件 **附录 A** 为准（含完整 `plan` 与判据）。
- **先看（前置失败检查）**：
  ```bash
  grep -n "^## 附录 A\|^### 附录 A" reqs/0043-case-library-expansion-and-soak/PLAN.md
  # 期望：命中（第一批定义就在 PLAN 附录 A，避免与 backlog 两处不一致）
  ```
- **最小实现**：分档落表（四档 + 条数 + 判据），并写明**为什么第一批只有 4 条**：一条用例要带出处、要真机出结论、要进长跑复用，铺开 8 条会把 P2 变成一次性大改。
- **验证命令与期望**：
  ```bash
  grep -n "第一批\|第二批\|可测且常用\|不可测" reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md | head -12
  # 期望：四档齐全 + 第一批 4 条 id 与 PLAN 附录 A 逐字一致
  ```
- **检查点**：
  - [ ] 四档齐全，第一批 4 条 id 与附录 A 一致
  - [ ] 第二批（今日不做）已登记成表，不写「以后再说」

### P1-4 P1 门禁：抽查与显式空缺

- **目标**：证明盘点表可被独立复核。
- **文件**：只读；产出 `evidence/0043/P1-backlog-gate.txt`（新建）。
- **先看（前置失败检查）**：随机抽 10 条（固定抽样，保证可复现）：
  ```bash
  B=reqs/0043-case-library-expansion-and-soak/CASE-BACKLOG.md
  for id in N1 N2 N3 N5 N11 M1 M4 M6 M10 M13; do
    grep -n "| $id |" "$B" || echo "MISS $id"
  done | tee evidence/0043/P1-backlog-gate.txt
  ```
- **最小实现**：逐条回查出处是否真的存在（`grep -n "<原文摘片段>" <知识库文件>`）；查不到的改成「库中未找到」，不得保留错出处。
- **验证命令与期望**：
  ```bash
  # 以 N2 为例（期望能命中 98% 口径原文）
  grep -rn "98%" /01-workfile-ai/04-obsidian/obsidian/业务层/37_双模测试用例_业务.md | head -3
  # 期望：命中「节点总数量 = 档案个数的 98%–100%」类原句
  ```
- **检查点**：
  - [ ] 10 条抽样全部命中且出处可复现
  - [ ] 「库中未找到」项单独成表（直接引用 taxonomy §6 的 8 条 + 本需求新增的空缺）

---

## 4. P2：补用例（CL-3 / CL-4，5 条任务）

> **铁律**：每条新用例动工前**先查知识库给出处**（`REQS-0042` D6-3）；用例声明必须带 `plan` 步骤实例化（D3-1）、`on_fail`、等待预算（D8-3）、`category` / `protocols`（D10-2）。
> **新增用例全部只读**：只用 `nwk.topology` / `nwk.route_status` / `nwk.onnet_count` / `nwk.nodes_online` / `meter.addr_probe` 这 5 个只读块 → 长跑复用安全，不会污染台架状态。

### P2-1 声明层最小支持：`category` / `protocols`（TDD）

- **目标**：让用例声明能携带「常用度 / 设备域 / 场景 / 省份 / 协议」，`show` 与 `list` 可见。**只加可选字段，不改既有字段语义**。
- **文件**：
  - `libs/case_runner/core/models.py`（`CaseSpec` 增两个可选字段 + 校验）
  - `libs/case_runner/cli.py`（`_case_summary` 与 `show` 人读输出各加一行；**不动判定逻辑**）
  - `libs/case_runner/test_case_category.py`（新建）
- **先看（前置失败检查）**：模型当前 `extra="forbid"`，声明里写 `category` 会直接加载失败：
  ```bash
  PYTHONPATH=libs .venv/bin/python - <<'PY'
  from case_runner.core.models import CaseSpec
  try:
      CaseSpec(id="x", title="t", steps=[{"no":1,"do":"d","expect":"e"}],
               plan=[{"id":"s1","block":"nwk.topology"}],
               category={"usage":"daily","domain":"cco","scene":"nwk","region":"sgcc"},
               protocols=["1376.2"])
      print("UNEXPECTED: accepted")
  except Exception as e:
      print("EXPECTED-BEFORE:", type(e).__name__)
  PY
  # 期望（改动前）：EXPECTED-BEFORE: ValidationError
  ```
- **最小实现**（字段形状写死，避免实现者自由发挥）：
  ```python
  # core/models.py :: class CaseSpec
  category: dict[str, str] = Field(default_factory=dict)   # 四键 usage/domain/scene/region
  protocols: list[str] = Field(default_factory=list)       # 见 taxonomy §1.5 词表
  ```
  校验（`@field_validator("category")`）：非空时必须**四键齐全**且值为非空字符串；**不做值域硬校验**（词表只写进 docstring 与 PLAN 附录），避免把 taxonomy 的演进绑进代码。
- **失败测试先写**（`test_case_category.py`）：
  - `test_category_optional_default_empty`：不写 `category` → `{}`，加载/check 全绿；
  - `test_category_requires_four_keys`：只给三键 → `ValidationError`；
  - `test_protocols_list`：`protocols=["1376.2","645"]` 可加载并在 `_case_summary` 出现；
  - `test_show_renders_category`：`_case_item` 的 `item["category"]["domain"] == "cco"`，`item["protocols"] == ["1376.2"]`。
- **验证命令与期望**：
  ```bash
  .venv/bin/python -m pytest libs/case_runner/test_case_category.py -q     # 期望：全绿
  .venv/bin/python -m pytest libs/case_runner -q                           # 期望：全绿（条数只增不减）
  .venv/bin/python -m pytest apps/workbench/test_cases_api.py apps/workbench/test_app.py -q
  # 期望：全绿（cases API 若已挂载，读模型对缺失字段容忍；若未挂载，此条只跑 test_app.py）
  .venv/bin/python tools/scripts/case_run.py check --json                  # 期望：problems == []
  ```
- **检查点**：
  - [ ] 4 条新单测全绿，旧测试 0 回归
  - [ ] `check` problems 仍为 `[]`
  - [ ] `GET /api/cases` 既有字段不变（若已挂载，用 `test_cases_api.py` 证明）

### P2-2 既有 7 条用例补分类（取值来自 taxonomy §4.1）

- **目标**：让「按常用 / CCO / STA / 场景 / 省份 / 协议筛选」在现有用例上立即成立。
- **文件**：`libs/case_runner/data/cases/{bench_ready,archive_ready,nwk_join,sta_reset,meter_daily,meter_first_join,archive_multi}.json`（**只加两个字段，不动 `plan`/`steps`**）。
- **先看（前置失败检查）**：确认它们当前都没有这两个字段：
  ```bash
  grep -l '"category"' libs/case_runner/data/cases/*.json ; echo "exit=$?"
  # 期望（改动前）：无输出，exit=1
  ```
- **最小实现**（逐条照抄 taxonomy §4.1，**不新增 scene 取值**）：

  | 用例 | `category` | `protocols` |
  |---|---|---|
  | `bench_ready` | `{"usage":"daily","domain":"bench","scene":"bench_prep","region":"sgcc"}` | `["1376.2"]` |
  | `archive_ready` | `{"usage":"daily","domain":"cco","scene":"archive","region":"sgcc"}` | `["1376.2"]` |
  | `nwk_join` | `{"usage":"acceptance","domain":"cco","scene":"nwk","region":"sgcc"}` | `["1376.2","645"]` |
  | `sta_reset` | `{"usage":"daily","domain":"sta","scene":"reset","region":"sgcc"}` | `["none"]` |
  | `meter_daily` | `{"usage":"daily","domain":"cco","scene":"meter_read","region":"sgcc"}` | `["1376.2","645"]` |
  | `meter_first_join` | `{"usage":"acceptance","domain":"cco","scene":"nwk","region":"sgcc"}` | `["1376.2","645"]` |
  | `archive_multi` | `{"usage":"daily","domain":"cco","scene":"archive","region":"sgcc"}` | `["1376.2"]` |

  `bench_prep` / `reset` 是 **taxonomy §4.3 已定的取值**（§1.3 表尾 + `sta_reset` 唯一），不是本需求新造场景族。
- **验证命令与期望**：
  ```bash
  .venv/bin/python tools/scripts/case_run.py list --cases --json \
    | .venv/bin/python -c "
  import json,sys
  for c in json.load(sys.stdin)['cases']:
      print(c['id'], c.get('category'), c.get('protocols'))"
  # 期望：7 行，每行四键齐全 + 协议非空
  .venv/bin/python tools/scripts/case_run.py check --json      # 期望：problems == []
  ```
- **检查点**：
  - [ ] 7 条全部带分类，取值与上表逐字一致
  - [ ] `plan` / `steps` / `needs` 内容 0 改动（`git diff --stat` 只显示新增行）
  - [ ] `check` problems `[]`

### P2-3 新增 CCO 域用例 3 条（组网侧只读）

- **目标**：补 3 条 `domain=cco` 用例，把 0042 D8-5 登记的抽样缺口（`nwk.route_status` / `nwk.nodes_online` 无用例入口）一次性补上，并加一条可独立复用的组网达标判定。
- **文件**（新建 3 个 JSON）：`libs/case_runner/data/cases/{cco_topo_gate,cco_route_state,cco_nodes_online}.json`
- **先看（前置失败检查）**：三条 id 必须不存在（否则是覆盖别人的用例）：
  ```bash
  for id in cco_topo_gate cco_route_state cco_nodes_online; do
    test -e "libs/case_runner/data/cases/$id.json" && echo "EXISTS $id" || echo "NEW $id"; done
  # 期望（改动前）：NEW ×3
  ```
- **最小实现**：三条声明**逐字**按附录 A 的 A.1 / A.2 / A.3 写（含 `plan`、`on_fail`、等待预算、`category`、`protocols`、`steps[].do/expect` 文案）。要点：
  - 一条用例**只有一个等待型块**（§0.4）；`cco_nodes_online` 的 `nwk.onnet_count` 不传 `wait_s`（该块无等待语义，只探一次）。
  - 判据文案必须能解释四态：不足→`fail`、取不到/超时→`inconclusive`、台架不可达→`blocked`。
- **验证命令与期望**：
  ```bash
  .venv/bin/python tools/scripts/case_run.py check --json            # 期望：problems == []
  .venv/bin/python tools/scripts/case_run.py show cco_topo_gate      # 期望：出现「步骤表」与「分类」两段
  .venv/bin/python tools/scripts/case_run.py show cco_route_state --json \
    | .venv/bin/python -c "import json,sys;d=json.load(sys.stdin);print([s['block_id'] for s in d['item']['steps_table']])"
  # 期望：['nwk.route_status']
  ```
- **检查点**：
  - [ ] 3 条加载成功，`steps_table` 与附录 A 一致
  - [ ] 每条都带 `category`（含 `domain=cco`）+ `protocols` + `on_fail=stop`
  - [ ] 无写块、无复位、无搜表（`grep -c "active_registration\|reset.sta" libs/case_runner/data/cases/cco_*.json` 期望 0）

### P2-4 新增 STA 域用例 1 条 + 新方案 `case_lib_probe`

- **目标**：满足 CL-4「STA 域 ≥1 条」；同时给 P3 一个「只读多用例」方案，便于长跑里做非破坏性抽检。
- **文件**：
  - 新建 `libs/case_runner/data/cases/sta_addr_bind.json`（附录 A 的 A.4）
  - 新建 `libs/case_runner/data/case_suites/case_lib_probe.json`
- **先看（前置失败检查）**：
  ```bash
  test -e libs/case_runner/data/cases/sta_addr_bind.json && echo EXISTS || echo NEW
  test -e libs/case_runner/data/case_suites/case_lib_probe.json && echo EXISTS || echo NEW
  # 期望（改动前）：NEW ×2
  ```
- **最小实现**：
  - `sta_addr_bind`：`domain=sta`，`scene=meter_read`（**不新增场景族**），被测对象 = STA 的 645 `13H` 取址/透传能力（口径差异说明见附录 A 末注）。
  - 方案 `case_lib_probe`：
    ```json
    {"id": "case_lib_probe",
     "name": "0043 只读抽检（台架前置 → 组网达标 → 路由状态 → 在线节点 → STA 绑表）",
     "case_ids": ["bench_ready", "cco_topo_gate", "cco_route_state", "cco_nodes_online", "sta_addr_bind"],
     "stop_on_fail": false,
     "notes": "全部业务步骤只读（无复位/无建档/无删档）。stop_on_fail=false：任一条 pending 不影响其余出结论，便于一次看到全部缺口。bench_ready 在前提供 bench.simeter_online 并打开表口（D8-2：台架前置只放方案层）。"}
    ```
    等待预算（§0.4 静态核算）：`bench_ready` ≈2s + `topology` 300 + `route_status` 60 + `nodes_online` 90 + `addr_probe`（`expect_timeout` 15、`wait_s` 0）≈ **≤ 470s < 600s 硬上限**；`run --timeout` 保持默认 600。
- **验证命令与期望**：
  ```bash
  .venv/bin/python tools/scripts/case_run.py check --json                     # 期望：problems == []
  .venv/bin/python tools/scripts/case_run.py show sta_addr_bind                # 期望：步骤表 1 步（meter.addr_probe）
  .venv/bin/python tools/scripts/case_run.py show case_lib_probe --json \
    | .venv/bin/python -c "import json,sys;print(json.load(sys.stdin)['item']['case_ids'])"
  # 期望：['bench_ready', 'cco_topo_gate', 'cco_route_state', 'cco_nodes_online', 'sta_addr_bind']
  ```
- **检查点**：
  - [ ] `check` problems `[]`
  - [ ] 方案 5 个 case_id 全部存在（`check` 会校验引用）
  - [ ] 预算核算写进方案 `notes`（可被复核）

### P2-5 P2 门禁：真机出结论（CL-4）

- **目标**：新增用例在真实台架上跑出**可解释**的结论（`pass` / `fail` / `blocked` 任一，且理由成立）。
- **文件**：只读；产出 `evidence/0043/P2-realrun-*.log` + 对应 `data/case_runs/<run_id>/result.json`。
- **先看（前置失败检查）**：先确认台架仍就绪（P0-2 口径）：
  ```bash
  curl -s http://127.0.0.1:8790/api/serial-bridge/status \
    | .venv/bin/python -c "import json,sys;print(json.load(sys.stdin).get('ready_ports'))"
  # 期望：含 simcon/cco-main/sta-main/simeter
  ```
- **最小实现**（**【Lead】执行**，台架归属方；子代理不得跑 `run`）：
  ```bash
  # 单条真机：CCO 域（组网达标，只读）
  .venv/bin/python tools/scripts/case_run.py run --case cco_topo_gate \
    < /dev/null --json > evidence/0043/P2-realrun-cco_topo_gate.json 2>&1 ; echo "exit=$?"
  # 期望：exit=0 且 verdict=pass；若 exit=5(blocked) → 台架不可用，先回 P0-2；若 exit=1(fail) → 核对档案数/拓扑比例再判

  # 单条真机：STA 域（绑表，只读透传）
  .venv/bin/python tools/scripts/case_run.py run --case sta_addr_bind \
    < /dev/null --json > evidence/0043/P2-realrun-sta_addr_bind.json 2>&1 ; echo "exit=$?"
  # 期望：exit=0 verdict=pass（表地址 = 123456789012，默认值）；exit=2 → 应答超时，记「说不清」并复跑一次

  # 方案级：一次拿到「台架前置 + 4 条新用例」的结论
  .venv/bin/python tools/scripts/case_run.py run --suite case_lib_probe \
    < /dev/null --json > evidence/0043/P2-realrun-case_lib_probe.json 2>&1 ; echo "exit=$?"
  # 期望：exit=0；cases[].verdict 全 pass
  ```
- **失败判据（必须能区分四态，不得混）**：
  | 现象 | 结论 | 处置 |
  |---|---|---|
  | `exit=5` / `verdict=blocked` 且 `blocked_block` 指向台架口/会话 | 台架不具备 | 回 P0-2 修桥，**不算产品缺陷** |
  | `exit=1` / `verdict=fail` 且 `failed_block=nwk.topology` | 组网真不达标 | 记「被测对象失败」，附档案数与实际节点数 |
  | `exit=2` / `verdict=inconclusive` | 说不清（超时/取值非法） | 复跑一次；两次都 inconclusive 才写结论 |
  | `interventions` 非空 | 该轮零介入不变量被破 | **作废该轮**，重跑并记证据 |
- **验证命令与期望**：
  ```bash
  .venv/bin/python - <<'PY'
  import json,glob
  for p in sorted(glob.glob("evidence/0043/P2-realrun-*.json")):
      d=json.load(open(p))
      print(p, d.get("verdict"), d.get("exit_code"), "interventions=",d.get("interventions"))
  PY
  # 期望：三条记录 verdict ∈ {pass}，interventions 全为 []
  ```
- **检查点**：
  - [ ] CCO 域 1 条真机出结论（CL-4 前半）
  - [ ] STA 域 1 条真机出结论（CL-4 后半）
  - [ ] 三条证据文件落盘，`interventions == []`
  - [ ] 每条结论都能一句话说清「为什么是这个态」

---

## 5. P3：长跑验证（CL-5 / CL-6 / CL-7，5 条任务）

> **总参数（本阶段唯一口径）**
>
> | 参数 | 值 | 依据 |
> |---|---|---|
> | 长跑方案 | **`meter_smoke_min`**（`bench_ready` + `meter_daily`，**不复位**） | `REQS-0043` §6 风险表；0042 D9-1 |
> | 每轮命令 | `case_run run --suite meter_smoke_min < /dev/null --json` | D4-2 零交互（`< /dev/null`）+ D4-6 单条命令取证 |
> | 块 A | **≥ 20 轮连跑**（间隔 0s），预计 ≈ 20×17s ≈ 6min | CL-5 |
> | 块 B | **≥ 2 小时持续**（`--interval-s 60`），预计 ≈ 93 轮 | CL-6 |
> | 每轮自检 | `probe --readonly --all`（6 个只读块）+ `timeout 240` 包裹；结果只记录不左右停机 | `REQS-0043` §6「每轮前 probe --readonly 自检」 |
> | **停机条件 1** | 连续 **3 轮 `exit=5`（blocked）** → 立即停 | `REQS-0043` §6 |
> | **停机条件 2** | 连续 **3 轮非 pass**（含 `fail`/`inconclusive`）→ 停下转人工定位 | D-3 三态可分，不拿 `blocked` 冒充 `fail` |
> | 暂停条件 | 自检连续 3 轮非 0（桥/服务抖）→ 暂停并检查串口桥，恢复后从断点续跑 | 避免把「桥抖」误记成用例失败 |
> | RSS 观测点 1 | **工作台进程**：`/proc/$(.venv/bin/python -c "import json;print(json.load(open('data/runtime/workbench.json'))['pid'])")/status` 的 `VmRSS`，每轮 run 前/后各采一次（本机无 `jq`，一律用 python 取值） | 单进程承载 simcon+simeter |
> | RSS 观测点 2 | **每轮 CLI 进程峰值**：`/usr/bin/time -v` 的 `Maximum resident set size (kbytes)` | 无 `psutil`，用 GNU time |
> | RSS 判据（我方定义） | 20 轮后工作台 `VmRSS` 增幅 **≤ 10%** 且不出现「连续 15 轮单调递增」→ 无泄漏迹象；增幅 **> 20%** 或单调 15 轮 → 判 `RSS-LEAK`，**复跑 20 轮**，两次都增长才写结论 | CL-5「RSS 不单调增长」 |
> | 证据根 | `data/case_runs/<run_id>/result.json`（原始，不删不改）+ `evidence/0043/soak-A|B/` | CL-7 |

### P3-1 长跑驱动器 `tools/scripts/case_soak.py`（TDD）

- **目标**：把「轮次循环 + 每轮自检 + RSS 采样 + 停机条件 + 汇总」做成一个可复跑的命令，杜绝手敲 `for` 循环漏采证据。
- **文件**：新建 `tools/scripts/case_soak.py`、`tools/scripts/test_case_soak.py`。
- **先看（前置失败检查）**：
  ```bash
  test -e tools/scripts/case_soak.py && echo EXISTS || echo NEW     # 期望（改动前）：NEW
  .venv/bin/python tools/scripts/case_soak.py --dry-run --rounds 2
  # 期望（改动前）：No such file or directory / ModuleNotFoundError → 失败
  ```
- **最小实现**（接口写死，禁止自由发挥）：
  ```
  python tools/scripts/case_soak.py
      --suite meter_smoke_min        # 必填，长跑只允许 meter_smoke_min / case_lib_probe
      --rounds 20                    # 块 A：轮数上限
      --duration-s 0                 # 块 B：总时长上限（秒），0 = 不按时长
      --interval-s 0                 # 轮间隔
      --out evidence/0043/soak-A     # 输出目录（不存在则建）
      --max-blocked-streak 3         # 停机条件 1
      --max-nonpass-streak 3         # 停机条件 2
      --selfcheck-timeout-s 240      # probe 自检的超时包裹
      --dry-run                      # 只打印将执行的命令与输出路径，不执行
  ```
  每轮固定动作（顺序不可换）：
  1. 采 `wb_rss_kb_before`；
  2. `subprocess.run(["/usr/bin/time","-v", PY, "tools/scripts/case_run.py","run","--suite",SUITE,"--json"], stdin=DEVNULL, capture_output=True)`；
  3. 采 `wb_rss_kb_after`；从 stderr 抽 `Maximum resident set size`；
  4. `timeout <selfcheck-timeout-s> probe --readonly --all --json` → 记 `selfcheck_exit`、`selfcheck_missing_ports`；
  5. 追加一行 JSON 到 `<out>/rounds.jsonl`：
     `{"round","ts","verdict","exit_code","duration_s","run_id","result_path","wb_rss_kb_before","wb_rss_kb_after","wb_rss_delta_kb","cli_maxrss_kb","selfcheck_exit","selfcheck_summary"}`；
  6. 判停机条件（`blocked_streak` / `nonpass_streak`），命中即 `break` 并写 `<out>/STOP.txt`；
  7. `finally` 里写 `<out>/summary.md`（轮数、verdict 分布、耗时 p50/p95、工作台 RSS 首末与增幅、CLI 峰值 RSS 最大/最小、停机原因、**失败轮次 → run_id → result.json 路径**）与 `<out>/rounds.csv`。
  **硬约束**：驱动器**只调 CLI 子进程**，不 import `libs.case_runner` 的任何判定（分层：工具层不做判定）；`interventions != []` 的轮直接标 `VOID` 并计入 nonpass。
- **失败测试先写**（`test_case_soak.py`，零台架）：
  - `test_dry_run_prints_round_count`：`--dry-run --rounds 20` → stdout 20 行 `case_run.py run --suite ...`，退出码 0，不创建任何 `data/case_runs/*`；
  - `test_summary_tolerates_unreadable_json`：喂一个坏 JSON 文件 → `summary.md` 仍生成且该轮记 `verdict=""`；
  - `test_stop_on_three_blocked`：fake 轮结果 `[blocked,blocked,blocked]` → 第 3 轮后停，`STOP.txt` 含「连续 3 轮 blocked」；
  - `test_rss_delta_recorded`：fake 的 before/after → `rounds.jsonl` 有 `wb_rss_delta_kb`。
- **验证命令与期望**：
  ```bash
  .venv/bin/python -m pytest tools/scripts/test_case_soak.py -q        # 期望：全绿
  .venv/bin/python tools/scripts/case_soak.py --dry-run --rounds 20 --out /tmp/soak-dry | head -3
  # 期望：3 行命令预览，且 /tmp/soak-dry 下无数据文件、data/case_runs 条数不变
  ```
- **检查点**：
  - [ ] 4 条单测全绿，无台架依赖（`grep -c "8790\|127.0.0.1" tools/scripts/test_case_soak.py` 期望 0）
  - [ ] `--dry-run` 零副作用（`data/case_runs` 条数前后一致）
  - [ ] 驱动器不 import `case_runner`（`grep -n "import case_runner\|from case_runner" tools/scripts/case_soak.py` 期望 0）

### P3-2 块 A：≥ 20 轮连跑（CL-5）

- **目标**：20 轮连续执行，结论稳定、耗时稳定、RSS 不单调增长。
- **文件**：产出 `evidence/0043/soak-A/{rounds.jsonl,rounds.csv,summary.md,r*.json,r*.time}`。
- **先看（前置失败检查）**：单轮基线可跑通：
  ```bash
  .venv/bin/python tools/scripts/case_soak.py --dry-run --rounds 1 --out /tmp/soak-check | head -1
  .venv/bin/python tools/scripts/case_run.py probe --readonly --all | tail -5
  # 期望：dry-run 打印 1 行；probe 人读摘要末尾出现「--readonly 跳过 10 个写块」（16-6），
  #       6 个只读块无 fail（pass/unknown 都算自检通过记录，unknown 不计 pass）
  ```
- **最小实现**（**【Lead】执行**）：
  ```bash
  .venv/bin/python tools/scripts/case_soak.py \
      --suite meter_smoke_min --rounds 20 --interval-s 0 \
      --out evidence/0043/soak-A
  # 期望：exit 0；evidence/0043/soak-A/summary.md 写明 rounds=20、verdict 全 pass、p95 耗时
  ```
- **验证命令与期望**：
  ```bash
  .venv/bin/python - <<'PY'
  import json,collections
  rows=[json.loads(l) for l in open("evidence/0043/soak-A/rounds.jsonl")]
  print("rounds=",len(rows))
  print("verdicts=",collections.Counter(r["verdict"] for r in rows))
  print("exit=",collections.Counter(r["exit_code"] for r in rows))
  d=[r["duration_s"] for r in rows if r["duration_s"]]
  print("dur_min/max=",min(d),max(d))
  print("wb_rss_first/last=",rows[0]["wb_rss_kb_before"],rows[-1]["wb_rss_kb_after"])
  print("growth_pct=",round((rows[-1]["wb_rss_kb_after"]-rows[0]["wb_rss_kb_before"])/rows[0]["wb_rss_kb_before"]*100,2))
  PY
  # 期望：rounds=20；verdicts={'pass':20}；exit={0:20}；growth_pct ≤ 10
  ```
- **检查点**：
  - [ ] 20 轮全部 `pass`（或异常轮均能一句话解释，且解释了停机/续跑）
  - [ ] 耗时无趋势性劣化（`dur_max - dur_min` ≤ 单轮典型值的 100%）
  - [ ] RSS 增幅 ≤ 10% 且无「连续 15 轮单调递增」
  - [ ] `summary.md` 的「失败轮次表」为空（若有，逐轮给 run_id）

### P3-3 块 B：≥ 2 小时持续（CL-6）

- **目标**：跨小时级持续执行，证明「失败可复现、无人工介入、结论稳定」。
- **文件**：产出 `evidence/0043/soak-B/{rounds.jsonl,rounds.csv,summary.md,...}`。
- **先看（前置失败检查）**：块 A 的 `summary.md` 存在且 rounds ≥ 20（**先过 A 再跑 B**，避免用长跑掩盖 20 轮里的问题）。
- **最小实现**（**【Lead】执行**）：
  ```bash
  .venv/bin/python tools/scripts/case_soak.py \
      --suite meter_smoke_min --duration-s 7200 --interval-s 60 \
      --out evidence/0043/soak-B
  # 期望：exit 0；整个过程中台架不动、无人介入；结束写 summary.md（rounds 预计 ≈ 90）
  ```
- **验证命令与期望**：
  ```bash
  .venv/bin/python - <<'PY'
  import json,collections,datetime
  rows=[json.loads(l) for l in open("evidence/0043/soak-B/rounds.jsonl")]
  t0=datetime.datetime.fromisoformat(rows[0]["ts"]); t1=datetime.datetime.fromisoformat(rows[-1]["ts"])
  print("rounds=",len(rows),"span_min=",round((t1-t0).total_seconds()/60,1))
  print("verdicts=",collections.Counter(r["verdict"] for r in rows))
  print("run_ids_of_nonpass=",[(r["round"],r["run_id"]) for r in rows if r["verdict"]!="pass"])
  PY
  # 期望：span_min ≥ 120；verdicts 以 pass 为绝对多数；非 pass 轮全部带 run_id
  ```
- **检查点**：
  - [ ] 时间跨度 ≥ 120 分钟（按 `rounds.jsonl` 首末 `ts` 计算，不按人工记忆）
  - [ ] 非 pass 轮次逐条可定位（round → run_id → `data/case_runs/<run_id>/result.json`）
  - [ ] 全程 `interventions == []`（在抽样里逐份核对，见 P3-4）
  - [ ] 停机/暂停若发生，`STOP.txt` 写明触发条件与当时台架状态

### P3-4 证据完整性抽查（CL-7）

- **目标**：证明每次运行落盘证据完整，事后可复现。
- **文件**：只读；产出 `evidence/0043/P3-evidence-audit.txt`。
- **先看（前置失败检查）**：随机取 3 份（1 份块 A、2 份块 B，或最新 3 份）：
  ```bash
  .venv/bin/python - <<'PY' | tee evidence/0043/P3-evidence-audit.txt
  import json,glob
  need=["verdict","params","decisions","interventions","self_healed",
        "started_at","finished_at","duration_s","bench"]
  files=sorted(glob.glob("data/case_runs/*/result.json"))[-3:]
  for p in files:
      d=json.load(open(p))
      missing=[k for k in need if k not in d]
      step_ids=all("step_id" in b for c in d.get("cases",[]) for b in c.get("blocks",[]))
      print(p, "verdict=",d.get("verdict"), "missing=",missing,
            "interventions=",d.get("interventions"), "step_id_all=",step_ids)
  PY
  ```
- **最小实现**：逐项核对下表，任一不满足即记问题并回到对应阶段修（**不得改历史 result.json**）：
  | 字段 | 期望 |
  |---|---|
  | `verdict` | ∈ {pass, fail, blocked, inconclusive} |
  | `params` / `resolved_params` | 存在且与启动入参一致（D4-1 冻结） |
  | `decisions[]` | 存在（自愈/重试/等待/超时各带 kind/attempts/reason） |
  | `interventions[]` | **恒为空数组** |
  | `self_healed[]` | 存在（可为空） |
  | `started_at` / `finished_at` / `duration_s` | 存在且 `finished_at ≥ started_at` |
  | `bench` 台架快照 | 存在且含 `facts` |
  | `cases[].blocks[].step_id` | 每个块都有 |
- **验证命令与期望**：`grep -c "missing= \[\]" evidence/0043/P3-evidence-audit.txt` 期望 3。
- **检查点**：
  - [ ] 3 份全部 `missing= []`
  - [ ] 3 份 `interventions == []`
  - [ ] 审计文件落盘并含原始路径

### P3-5 异常定位与停机处置（CL-5 / CL-6 的可解释性）

- **目标**：让「停在哪、为什么停、怎么恢复、从哪续」四件事写在证据里，不靠人回忆。
- **文件**：`evidence/0043/soak-A|B/STOP.txt`、`evidence/0043/P3-incident-log.md`（新建，无异常也要写一行「无异常」）。
- **先看（前置失败检查）**：确认停机条件真的会触发（用驱动器单测证明，不拿真台架试）：
  ```bash
  .venv/bin/python -m pytest tools/scripts/test_case_soak.py -q -k stop_on_three_blocked
  # 期望：1 passed
  ```
- **最小实现**：按下面的处置表逐条落文档，每条必须带命令与判读：
  | 症状 | 判读 | 处置（不许自行发挥） |
  |---|---|---|
  | 连续 3 轮 `exit=5` blocked | 台架不具备（桥断/端口被占/服务不可达） | 停跑 → 查 `/api/serial-bridge/status` → 让用户在 Windows 侧重启串口桥 → `bench_ready` 单跑确认 → **换新 `--out` 目录续跑**（不覆盖旧证据） |
  | 连续 3 轮 `fail` | 被测对象真失败 | 停跑 → 打开该轮 `result.json` 的 `failed_block`/`failed_step` → 记 `P3-incident-log.md` → 不重启台架，先定位 |
  | 连续 3 轮 `inconclusive` | 说不清（超时/学习态） | 停跑 → 看 `decisions[]` 的等待次数与 `blocked_block` → 延长观察 1 轮再判 |
  | 自检连续 3 轮非 0 | 链路抖动而非用例问题 | **暂停**（不是停止）→ 修桥后从断点续跑，`P3-incident-log.md` 记时间段 |
  | RSS 增幅 > 20% | 泄漏嫌疑 | 不停跑 → 标记 → 另跑 20 轮复现；两次都增长才写 `RSS-LEAK` 结论 |
  **禁止**：长跑期间复位 STA、跑 `smoke_meter`/`meter_first_join`、`probe` 不带 `--readonly`、改用例参数「让它变绿」。
- **验证命令与期望**：
  ```bash
  grep -n "结论\|处置\|续跑" evidence/0043/P3-incident-log.md | head -5
  # 期望：至少 1 行（无异常时写「本轮长跑无异常；停机条件由单测证明可用」）
  ```
- **检查点**：
  - [ ] 停机条件有单测证明（不是「大概会停」）
  - [ ] 任何一次停机都在 `STOP.txt` + `P3-incident-log.md` 有记录
  - [ ] 续跑用新目录，旧证据不被覆盖

---

## 6. P4：收口（CL-8，3 条任务）

### P4-1 收敛报告

- **目标**：回答三个问题 —— 已覆盖什么、为什么不可测、下一批优先补什么。
- **文件**：新建 `reqs/0043-case-library-expansion-and-soak/CASE-CONVERGENCE.md`。
- **先看（前置失败检查）**：
  ```bash
  ls evidence/0043/ | tr '\n' ' '
  # 期望：含 P0-*.txt、P2-realrun-*.json、soak-A/、soak-B/、P3-evidence-audit.txt、P3-incident-log.md
  ```
- **最小实现**：四节固定结构 ——
  1. **已覆盖**：用例 id × 场景 × 判据档位 × 真机结论（引 `data/case_runs/<run_id>`）；
  2. **本环境不可测**：逐条给「缺哪个块/能力/硬件/省份环境」（引 `CASE-BACKLOG.md` + taxonomy §3 缺块汇总）；
  3. **下一批优先级**：可测且常用 > 可测判据弱 > 需新增块 > 需省份环境，每条给建议块与预算估计；
  4. **长跑结论**：块 A/块 B 的轮数、verdict 分布、耗时、RSS 增幅与判读（引 `soak-*/summary.md`）。
- **验证命令与期望**：
  ```bash
  grep -c "^## " reqs/0043-case-library-expansion-and-soak/CASE-CONVERGENCE.md   # 期望：4
  grep -c "data/case_runs/" reqs/0043-case-library-expansion-and-soak/CASE-CONVERGENCE.md  # 期望：≥ 3
  ```
- **检查点**：
  - [ ] 四节齐全，无「以后再说」式占位
  - [ ] 每条结论可点回证据文件

### P4-2 `DONE.md` + `TODO.md` 勾选

- **目标**：把阶段验收结果固化。
- **文件**：新建 `reqs/0043-case-library-expansion-and-soak/DONE.md`；改 `reqs/0043-case-library-expansion-and-soak/TODO.md`（只勾选，不改条目语义）。
- **先看（前置失败检查）**：`grep -c "^- \[ \]" reqs/0043-*/TODO.md` 记录未勾数（作为改动前基线）。
- **最小实现**：`DONE.md` 按「阶段 → 交付物路径 → 验证命令 → 实测输出摘要」表格写；未达成项**不勾**，并在 `DONE.md` 单列「未达成 + 原因 + 下一批归属」。
- **验证命令与期望**：
  ```bash
  grep -c "^- \[x\]" reqs/0043-case-library-expansion-and-soak/TODO.md
  # 期望：等于实际达成条目数（TODO.md 共 17 条 = P0 4 + P1 4 + P2 4 + P3 3 + P4 2；
  #       逐条对照证据，不许一次性全勾）
  ```
- **检查点**：
  - [ ] 勾选与证据一一对应
  - [ ] 未达成项有原因与归属

### P4-3 索引与基线冻结

- **目标**：让 0043 在需求索引里状态正确。
- **文件**：`REQS-INDEX.md`（**由 Lead 更新**，本 PLAN 不代改）。
- **先看（前置失败检查）**：`grep -n "0043" REQS-INDEX.md` 确认现有行。
- **最小实现**：Lead 把 0043 状态改为「已交付」或「部分交付（附未达成项）」，并在变更记录写一行依据（`DONE.md` + 收敛报告路径）。
- **验证命令与期望**：`grep -n "0043" REQS-INDEX.md` 期望命中状态行与日期。
- **检查点**：
  - [ ] 索引状态与 `DONE.md` 一致
  - [ ] 本需求的证据路径在索引或 `DONE.md` 可点开

---

## 7. 风险与回滚

| 风险 | 触发迹象 | 处置 / 回滚 |
|---|---|---|
| **长跑把台架跑挂**（端口/会话泄漏） | 连续 3 轮 `exit=5`；`probe --readonly` 自检非 0；`serial-bridge/status` 出现 `unusable_ports` | 立即停跑（停机条件 1）→ 查桥 → 用户在 Windows 侧重启串口桥 → 重启工作台（D2-9 允许）→ `bench_ready` 单跑确认 → **换新 `--out` 目录续跑**，旧证据保留 |
| **复位带来的长恢复**（实测 ≈10 分钟） | 长跑中途误跑 `meter_first_join`/`smoke_meter`，之后全部 `inconclusive` | 长跑配置层禁止：驱动器 `--suite` 只接受 `meter_smoke_min` / `case_lib_probe`（其它值直接 `UsageError` 退出码 3）；已误触 → 停跑等链路恢复（≈10min），期间只发 `probe --readonly` |
| **`blocked` 与 `fail` 混淆**（D-3 被破） | 汇总表把 exit 5 与 exit 1 混成一列 | `rounds.jsonl` 同时记 `verdict` + `exit_code`；汇总表分列计数；报告里禁止用「失败」笼统概括 `blocked` |
| **省份用例无环境** | 盘点表出现 `bj/zj/fj/...` 用例被标「可测」 | 只登记不实施：`可测性=不可测`，原因写「缺省份环境（本台架仅国网通用 + 单一表地址）」；不造环境（`REQS-0043` §3） |
| **与 0042 并发改同一文件** | `core/models.py` 同时被 `verdict-model` 与 P2-1 改 | P0-1 门禁要求 0042 收口后才动；P2-1 只加两个**可选**字段 + 独立测试文件；冲突时以 0042 现状为基（`git diff` 复核后再提交） |
| **声明层改动引入回归** | `pytest libs/case_runner` 变红或 `check` 出 problems | 回滚范围仅 `core/models.py` + `cli.py` 的展示两处（`git checkout -- libs/case_runner/core/models.py libs/case_runner/cli.py`），JSON 声明保留但改用 `tags` 临时表达分类 |
| **新用例污染台架**（误把写块/复位写进用例） | 新用例 `plan` 出现 `archive.add/del`、`reset.sta`、`nwk.active_registration` | P2-3/P2-4 检查点里已有 `grep` 门禁（期望 0）；命中即视为未完成，删用例重写 |
| **驱动器把判定逻辑抄进工具层**（分层被破） | `tools/scripts/case_soak.py` 出现 `import case_runner` 或自行判 verdict | P3-1 检查点门禁（`grep` 期望 0）；违反则该文件重写，只保留子进程调度 + 汇总 |

**通用回滚原则**：`data/case_runs/**` 是**只增证据**，任何情况下不删不改；回滚只针对代码/声明（`git checkout -- <具体路径>`），且回滚后必须重跑 §0.5 五条共用门禁。

---

## 8. 今天不做（明确清单）

| 不做 | 原因 |
|---|---|
| **不新增场景族** | `REQS-0042` D10-1：今天只做组网 + 抄表；`scene` 取值只用 `nwk` / `meter_read`（既有用例沿用它已有的 `archive` / `reset` / `bench_prep`） |
| **不改 0042 的 API/UI 契约** | `/api/cases/*` 端点、字段语义、UI 交互一律不动；P2-1 只加**可选声明字段**，`GET /api/cases` 既有字段不变 |
| **不改四层模型** | Case/Block/Fact/Capability 边界与依赖方向不变；本需求**不新增任何块/事实** |
| **不做真固件烧录/激活** | 属 `wb-flash` / `hs-dw31-upg` |
| **不造省份环境** | 省份用例只登记（不可测原因写清），不搭环境（`REQS-0043` §3） |
| **不做 48h 长跑** | 今天门槛是 ≥20 轮 + ≥2h；48h 属于下一批（taxonomy M10 `meter_longrun` 已登记出处） |
| **不删/不改历史 `result.json`** | D4-6 证据链只增 |

---

## 9. 需求点覆盖对照（CL-1..CL-8）

| 需求点 | 验收口径 | 落在哪个任务 |
|---|---|---|
| **CL-1** 盘点表（五维 + 出处） | 表在 `reqs/0043-*/CASE-BACKLOG.md`；抽查 10 条有出处 | P1-1、P1-4 |
| **CL-2** 可测性标注 | 逐条 `可测/不可测`，不可测写缺什么 | P1-2、P1-3 |
| **CL-3** 新用例沿用 0042 规范 | `check` problems==[]；`show` 见步骤表 + 分类 | P2-1、P2-3、P2-4 |
| **CL-4** 按域补齐（CCO ≥1 / STA ≥1） | 真机跑出可解释结论 | P2-3、P2-4、P2-5 |
| **CL-5** ≥20 轮连跑 + RSS | 日志 + 汇总表（轮次/结论/耗时/RSS） | P3-1、P3-2 |
| **CL-6** ≥2h 持续、失败可复现、零介入 | 证据 `data/case_runs/*` + 报告 | P3-3、P3-5 |
| **CL-7** 证据完整 | 抽查 3 份 `result.json` 字段齐全 | P3-4 |
| **CL-8** 收敛报告 | `DONE.md` 或报告，含下一批清单 | P4-1、P4-2 |

---

## 10. 派发记录

| 阶段 | 任务 | 执行者 | 写入范围 | 状态 |
|---|---|---|---|---|
| P0 | P0-1..P0-4 | Lead | `evidence/0043/` | 待开始 |
| P1 | P1-1..P1-4 | 子代理 `case-backlog-0043` | `reqs/0043-*/CASE-BACKLOG.md` | 待派发（依赖 `kb-case-library` 落盘；未落盘则按 P1-1 的 fallback 依据补写） |
| P2 | P2-1..P2-4 | 子代理 `case-add-0043` | `core/models.py`、`cli.py`（展示）、`data/cases/`、`data/case_suites/`、`test_cases_declaration.py`、`test_case_category.py` | 待派发（依赖 P0-1：0042 收口） |
| P2 | P2-5 真机 | Lead | `evidence/0043/` | 待派发 |
| P3 | P3-1 | 子代理 `soak-driver` | `tools/scripts/case_soak.py`、`tools/scripts/test_case_soak.py` | 待派发 |
| P3 | P3-2..P3-5 | Lead（跑）+ 子代理（审计） | `evidence/0043/` | 待 P3-1 完成 |
| P4 | P4-1..P4-3 | Lead | `reqs/0043-*/CASE-CONVERGENCE.md`、`DONE.md`、`TODO.md`、`REQS-INDEX.md` | 待开始 |

---

## 附录 A — 第一批用例清单（4 条，全部只读，含出处与判定要点）

> 依据：`reqs/0042-*/CASE-LIBRARY-taxonomy.md` §3 / §4（P0-3 已验证）。每条都满足：只用现有 16 块、不改块、`plan` 步骤实例化、单一等待型判据、四态可解释。
> `usage` 取值按 taxonomy §1.1（`smoke/daily/acceptance/probe/forbidden`）；本条第一批只出现 `daily` / `probe`。

### A.1 `cco_topo_gate` — 组网达标判定（CCO 域 / `nwk`）

- **判定要点**：入网从节点数 ≥ `ceil(档案数 × 0.98)` → `pass`；等满 300s 仍明确不足 → `fail`；档案数 = 0 或应答取不到 → `inconclusive`；台架不可达 → `blocked`。
- **出处**：`业务层/37_双模测试用例_业务.md:42-48`（「节点总数量 = 档案个数的 98%–100%」）；`03_QGDW10376.2_全帧类型.md:1154-1180`；台账 `用例库/CCO/台账/144_国网用例_AFN功能_…_台账.md:42`（源表行 6 步骤 2「所有从节点均组网成功」）。
- **声明**：
  ```json
  {"id": "cco_topo_gate",
   "title": "组网达标判定（10H-F21 拓扑比例门槛 98%；只读）",
   "category": {"usage": "daily", "domain": "cco", "scene": "nwk", "region": "sgcc"},
   "protocols": ["1376.2"],
   "needs": ["bench.simcon_online"],
   "steps": [{"no": 1,
     "do": "查询网络拓扑（10H-F21），入网从节点数与档案数对比；等待上限取块声明默认 300s，等待期只发只读查询",
     "expect": "入网从节点数 ≥ ceil(档案数 × 0.98) → 达标通过；等满上限仍明确不足 → 失败；档案数为 0 或应答取不到 → 判「不知道」，不算通过"}],
   "plan": [{"id": "s1", "block": "nwk.topology", "params": {}, "on_fail": "stop"}],
   "tags": ["nwk", "topology", "readonly", "0043"]}
  ```

### A.2 `cco_route_state` — 路由工作状态（CCO 域 / `nwk`）

- **判定要点**：工作状态 = 抄表（当前状态 00）→ `pass`；学习态持续等满 60s → `inconclusive`（不是失败）；字段缺失/非法 → `inconclusive`。
- **出处**：台账 `用例库/CCO/台账/144_…_台账.md:41`（源表行 5 步骤 4「工作状态=00，当前状态=00（抄表）」）；`用例库/CCO/能力域/156_国网AFN功能回归清单_用例蒸馏.md:69`（`10H-F4`）。
- **为什么必须有它**：`REQS-0042` D8-5 明写 `nwk.route_status` 目前**无任何用例入口**，本用例补上该抽样缺口（不塞回 `nwk_join`）。
- **声明**：
  ```json
  {"id": "cco_route_state",
   "title": "路由运行状态判定（10H-F4，工作状态=抄表；学习态超时判说不清；只读）",
   "category": {"usage": "probe", "domain": "cco", "scene": "nwk", "region": "sgcc"},
   "protocols": ["1376.2"],
   "needs": ["bench.simcon_online"],
   "steps": [{"no": 1,
     "do": "查询路由运行状态（10H-F4），等待上限显式 60s；等待期只发只读查询，达标即停",
     "expect": "工作状态=抄表 → 通过；学习态等满 60s 仍不抄表 → 判「不知道」并写明等了多久；字段缺失/取值非法同样判「不知道」"}],
   "plan": [{"id": "s1", "block": "nwk.route_status", "params": {"wait_s": 60}, "on_fail": "stop"}],
   "tags": ["nwk", "route-status", "readonly", "0043"]}
  ```

### A.3 `cco_nodes_online` — 在线/可达节点数（CCO 域 / `nwk`）

- **判定要点**：两路交叉（`10H-F4` 已抄从节点数 + `10H-F2` 逐节点信号品质）任一读数 ≥ 1 → `pass`；等满 90s 两路都为 0 → `fail`。
- **出处**：`用例库/CCO/能力域/156_…_用例蒸馏.md:69`（`10H-F4` 15 步）；台账 `用例库/CCO/台账/144_…_台账.md:41`；`REQS-0042` §8 事实 2（`10H-F1` 是档案数、**不是**在网数）。
- **等待预算合规**：`nwk.onnet_count` 无等待语义（只探一次）、`nwk.nodes_online` 显式 `wait_s: 90` → 一条用例只有一个等待型判据（§0.4）。
- **声明**：
  ```json
  {"id": "cco_nodes_online",
   "title": "在线/可达节点数（10H-F4 已抄数 + 10H-F2 信号品质交叉，≥1；只读）",
   "category": {"usage": "probe", "domain": "cco", "scene": "nwk", "region": "sgcc"},
   "protocols": ["1376.2"],
   "needs": ["bench.simcon_online"],
   "steps": [
     {"no": 1, "do": "查询路由档案中的从节点总数量（10H-F1），只探一次不等待",
      "expect": "档案数 ≥ 1 → 本步通过；本步只证明「有档案」，不证明「抄得通」"},
     {"no": 2, "do": "查询真实可达节点（10H-F4 已抄从节点数 + 10H-F2 逐节点信号品质），等待上限 90s，等待期只发只读查询",
      "expect": "两路任一读数 ≥ 1 → 有节点可达；等满 90s 两路都为 0 → 失败"}],
   "plan": [
     {"id": "s1", "block": "nwk.onnet_count", "params": {"min": 1}, "on_fail": "stop"},
     {"id": "s2", "block": "nwk.nodes_online", "params": {"min": 1, "wait_s": 90}, "on_fail": "stop"}],
   "tags": ["nwk", "online", "readonly", "0043"]}
  ```

### A.4 `sta_addr_bind` — STA 取表地址（绑表）（STA 域 / `meter_read`）

- **判定要点**：上行 `02H-F1` 内嵌 645 地址域 == 期望表地址（默认 `123456789012`）→ `pass`；有应答但地址不符 → `fail`；等满 `expect_timeout` 无应答 → `inconclusive`。
- **出处**：`用例库/STA/台账/123_国网用例_台账.md:73`（源表行 35 步骤 1-9「CCO端正确返回13H-F1，645报文控制码C=91」——STA 侧 645 `13H` 取址/透传）；`REQS-0042` D5-2（`13H` 地址域必须全 FE，块侧已实现）。
- **声明**：
  ```json
  {"id": "sta_addr_bind",
   "title": "STA 取表地址（02H-F1 透传 645 13H；被测对象 = STA 的取址/透传能力；只读）",
   "category": {"usage": "daily", "domain": "sta", "scene": "meter_read", "region": "sgcc"},
   "protocols": ["1376.2", "645"],
   "needs": ["bench.simcon_online", "bench.simeter_online"],
   "steps": [{"no": 1,
     "do": "让 STA 透传 645 读通信地址命令（1376.2 02H-F1，内层 13H，地址域全 FE），应答等待显式钉 15s（实测单数据项往返 2.3–2.6s，6 倍余量），只探一次不轮询",
     "expect": "上行帧内嵌 645 地址域 == 期望表地址 → STA 确实能取到表地址（本步通过 ≠ 抄表通过）；有应答但地址不符 → 失败；等满 15s 无应答 → 判「不知道」"}],
   "plan": [{"id": "s1", "block": "meter.addr_probe", "params": {"expect_timeout": 15, "wait_s": 0}, "on_fail": "stop"}],
   "tags": ["sta", "645", "13h", "bind", "readonly", "0043"]}
  ```

> **域归属口径说明（必须保留在用例 notes 里，避免与盘点表打架）**：taxonomy §4.2 把 `meter.addr_probe` 归 `domain=cco`（块视角：帧从 CCO 侧下发）。本用例取 **`domain=sta`**（用例视角：**被测对象是 STA 的取址/透传行为**，判据主语是「STA 能否取到表地址」），与 STA 库台账行 35 的判据主语一致。两条口径不冲突：块域 ≠ 用例被测对象域（taxonomy §1.2 关键判据原文：「一个用例归属于哪个域，看被测对象而不是前置设备清单」）。

## 附录 B — 第二批登记（今日不做，只留出处与卡点）

| 建议 id | 场景 | 卡点（为什么今天不做） | 出处 |
|---|---|---|---|
| `cco_archive_probe` | 档案（组网前置） | `scene=archive` 超出今日「组网+抄表」范围 | `用例库/CCO/台账/144_…:40`；`用例库/CCO/能力域/156_…:136` |
| `cco_archive_sync`（N1） | 档案加-查-删-清闭环 | 判据缺 `10H-F2` 明细比对（缺块 `nwk.archive_detail`）+ 含删档破坏性动作 | `用例库/CCO/台账/144_…:40`（行 4） |
| `sta_reset_verify` | 复位（STA） | 复位后链路恢复 ≈10 分钟，会打断长跑窗口；单列按需执行 | `REQS-0042` §8 事实 4；`用例库/CCO/台账/144_…:42` |
| `meter_13h_645_full`（M1） | 抄表（645 全控制码 B1/B2/D1/94/9A/9C/DC） | 现块只覆盖「读成功 C=91」，长报文/否认/写操作需扩块 | `用例库/STA/台账/123_…:73` |
| `meter_read_698`（M2） | 抄表（698） | 缺 698 抄表块（现 `meter.read_645` 只构 645） | `用例库/STA/台账/123_…:73`（步骤 11-14） |
| `nwk_read_all`（N3） | 组网后全网抄表 | 缺块 `meter.read_all`（全网多表 + 逐表比对） | `用例库/CCO/台账/144_…:42`（行 6 步骤 3） |
| `meter_f1f1_batch`（M4） | 并发抄表 | 缺块 `meter.read_batch`；且库内明说判据弱「只能当冒烟」 | `用例库/STA/台账/123_…:74`；`156_…:128` |
| `nwk_search_meter`（X1） | 搜表 | **禁用**：搜表不是入网手段，且会先停抄/暂停路由，污染主线 | `REQS-0042` D6-2；`用例库/CCO/台账/144_…:41` |
| 省份用例（`bj/zj/fj/hn/hlj/cq/...`） | 各场景 | 缺省份环境（本台架只国网通用 + 单一表地址）；按 `REQS-0043` §3 只登记 | taxonomy §1.4、§6 |

## 附录 C — 证据路径约定

| 阶段 | 证据路径 | 说明 |
|---|---|---|
| P0 | `evidence/0043/P0-baseline.txt`、`P0-bench-status.json`、`P0-bench.txt`、`P0-taxonomy.txt`、`P0-plan-freeze.txt` | 只读命令原始输出 |
| P1 | `evidence/0043/P1-backlog-gate.txt` + `reqs/0043-*/CASE-BACKLOG.md` | 抽样与出处复核 |
| P2 | `evidence/0043/P2-realrun-*.json`、`P2-check.txt`；原始 `data/case_runs/<run_id>/result.json` | 真机结论 + 声明校验 |
| P3 | `evidence/0043/soak-A/`、`soak-B/`（`rounds.jsonl`/`rounds.csv`/`summary.md`/`r*.json`/`r*.time`/`STOP.txt`）、`P3-evidence-audit.txt`、`P3-incident-log.md`；原始 `data/case_runs/<run_id>/result.json` | 长跑全量证据 |
| P4 | `reqs/0043-*/CASE-CONVERGENCE.md`、`DONE.md`、`TODO.md`、`REQS-INDEX.md` | 收口文档 |

**证据硬规矩**：`data/case_runs/**` 只增不改不删；`evidence/0043/**` 长跑分目录（A/B/续跑各自独立），任何续跑都不覆盖旧目录。
