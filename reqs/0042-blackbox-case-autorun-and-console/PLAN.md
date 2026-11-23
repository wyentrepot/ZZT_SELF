# PLAN.md — 需求 0042 执行计划（依据 `REQS.md` **v4**）

> 依据基线：`REQS.md` v4（变更 2 = Q1–Q12 敲定；变更 3 = 步骤实例化 + 验收按块量化；**变更 4 = 零介入硬不变量 D4-1..D4-6**）。基线不符或本文件被标失效时**不得执行**，须重新规划。
> 角色：**Lead 只拆任务/派发/审查/跑门禁，不自己啃大块编码**（用户 2026-11-23 明确要求）。
> 每阶段结束 → 汇报（做了什么 / 验证输出 / 风险）→ 用户确认后进入下一阶段（Q12-A）。

---

## 0. 目标与架构（不变的边界）

一句话目标：**一条用例 / 一个方案 = 给入参（或干脆不给）→ 触发 → 拿到可信结论**，全程零 AI / 零人工；结论能区分「被测对象真失败（fail）」/「台架没准备好（blocked）」/「说不清（inconclusive）」。

四层边界**不动**：`Case（编排）→ Block（原子块）→ Fact（事实）→ Capability（能力适配）`。
**不动**：`libs/case_library`、`libs/test_automation` 模型契约、`/api/autotest/*` 与旧「自动测试」页代码。
**判定逻辑**只允许在 `libs/case_runner`；`apps/workbench/cases_api.py` 只做装配 + 调度 + 落盘。

### 0.1 环境约束（实测）

| 项 | 事实 |
|---|---|
| 解释器 | `.venv/bin/python`（3.10.12）；`python` 不存在，别用 |
| 现有用例库基线 | `pytest libs/case_runner -q` → **444 passed / 2 skipped**；`test_layering.py` 10 passed |
| 台架 | 工作台 `http://127.0.0.1:8790` 在线（`apps/workbench/run.py`）；`/api/serial-bridge/status` 5 口 usable |
| 模拟电表 | 脑地址 `123456789012`，表口已打开；CCO 档案 `['123456789012','129078563412']`，后者信号品质=1 |
| 声明根 | `libs/case_runner/data/`（`loader.DATA_DIR`），CLI 可 `--root` 覆盖 |
| 老 `addr` 用法 | 15 块 / 8 事实 / 5 用例 / 3 方案 JSON + `blocks/{archive,meter,facts_meter,facts_nwk}.py`；**硬改名，不留别名**（D2-1） |

### 0.2 全阶段共用门禁（每条命令必须贴出输出）

```bash
cd /01-workfile-ai/01-zzt/ZZT_SELF
.venv/bin/python -m pytest libs/case_runner -q                      # 期望：全绿，用例数只增不减
.venv/bin/python -m pytest libs/case_runner/test_layering.py -q      # 期望：10 passed
grep -rn '^from apps' libs/ ; echo "exit=$?"                         # 期望：无输出
grep -rn '\[::-1\]' libs/case_runner/blocks/                         # 期望：只命中 common.py
.venv/bin/python tools/scripts/case_run.py check                     # 期望：loader problems == []
```

### 0.3 零介入不变量（D4-1..D4-6，任一被破即验收失败）

| 不变量 | 落地位置 | 检查方式 |
|---|---|---|
| D4-1 参数冻结 | `cli.py` / `cases_api.py` 启动瞬间快照 `resolved_params`，执行期只读；API 已启动 job 不受后续请求影响 | `result.json` 的 `params` 与启动时入参逐字相等；运行中改参不生效 |
| D4-2 零交互 | 执行路径无 stdin / `input()`；缺参在**启动前**判定为 `blocked` | `grep -rn "input(\|sys.stdin" libs/case_runner --include=*.py | grep -v /test_` → 0；`run ... < /dev/null` 行为一致 |
| D4-3 零 AI | `libs/` 不 import `apps/`；执行路径无 AI 调用 | `grep -rn "openai\|anthropic\|ai_api\|ai_operations" libs/case_runner/` → 0（注意排除 `re.fullmatch` 误报） |
| D4-4 自动决策留痕 | 结果结构新增 `decisions[]`（自愈/重试/等待/超时，带理由与次数）与 `interventions[]`（必须恒为空） | `result.json` 里 `interventions == []`；`decisions` 与 `self_healed` 一致 |
| D4-5 必然终局 | run 级 `timeout`（默认 600s，D2-6）兜底，任何路径都落到四态之一 | 构造卡死场景（fake 能力永不返回）→ 限时内给 `inconclusive` |
| D4-6 验收方法 | 单条命令 + `< /dev/null` + 全程无人写入 | 证据链：`evidence/0042/dod1-zero-intervention.log` + 对应 `result.json` |

**D4-6 标准取证命令（S6 执行，Lead 亲自跑）**

```bash
.venv/bin/python tools/scripts/case_run.py run --suite meter_smoke_min \
  < /dev/null > evidence/0042/dod1-zero-intervention.log 2>&1 ; echo "exit=$?"
```

**运行期间唯一允许的动作**：只读观察（`tail` 日志、读 `result.json`）。**禁止**：改参数、补参、点页面、重启服务、任何 AI 介入。

---

## 1. 阶段与任务总览

| 阶段 | 主题 | 需求点 | 派发方式 |
|---|---|---|---|
| **S1** | 地址统一 + 默认配置 + 单入口服务发现 | AD-1..3, D2-1..3 | 3 个子代理并行 + Lead 收口 |
| **S1.5** | 编排模型：步骤实例化（同块多次、各自参数） | D3-1..D3-5 | 1 个子代理（模型层）+ Lead 收口 |
| **S2** | 幂等 / 等待 / 自愈 / `probe --readonly` | ID-1..2, WT-1..3, SH-1..3, D2-6/7/10 | 3 个子代理并行 + Lead 收口 |
| **S3** | 三分类 + 预检 + 退出码 + 落盘证据 | VF-1..3, D2-4/5/6/8 | 2 个子代理 + Lead 收口 |
| **S4** | API 层（10 端点 + 挂载 + job） | API-1..4 | 2 个子代理 + Lead 收口 |
| **S5** | UI 层（新页接管导航 + 三态着色 + 断点） | UI-1..7, D2-11/12 | 2 个子代理 + Lead 收口 |
| **S6** | 零参与收口（DoD 实测 + 证据 + 文档） | DoD 1..10 | Lead 派发实测 + 汇总 |

**写入范围不重叠**：S1 拆 `core/`+`cli` / `data/*.json` / `blocks/*.py` 三块；S2 拆 `blocks/archive.py`+`nwk.py` / `blocks/common.py`(自愈) / `cli.py`(probe)；S3 拆 `core/models.py`+`executor.py` / `cli.py`(退出码+预检)；S4 拆 `cases_api.py` / `app.py`；S5 拆 `pages/cases/` / `static/app.js`+样式。同一阶段内两个子代理**不碰同一文件**。

---

## 2. S1：地址统一 + 默认配置 + 单入口服务发现

### S1.0 前置一次性台架动作（Lead 亲自执行，D2-9）

- [ ] 删除 CCO 档案冗余条（旧地址角色错位产物，信号品质=0）：
      `.venv/bin/python tools/scripts/case_run.py run --case archive_ready --params '{"addr":"123456789012"}' --base-url http://127.0.0.1:8790`
      → 期望：档案表只剩 `129078563412`；把输出留证到 `evidence/0042/S1-archive-cleanup.txt`。
      若删除帧被 CCO 否认 → 保留现状，记录「CCO 拒删」并在 S6 结果里提示，不阻塞。

### S1.1 换算函数与默认配置（子代理 `addr-core`）

**新增/修改文件**
- 新增 `libs/case_runner/core/defaults.py`
- 新增 `libs/case_runner/data/case_defaults.json`
- 修改 `libs/case_runner/blocks/common.py`（只加两个函数，不动既有判定）
- 修改 `libs/case_runner/blocks/loader.py`（`null` 默认值 → 从 defaults 解析）
- 修改 `libs/case_runner/cli.py`（参数规格取 `meter_addr`；传 `addr` 直接报错退出码 3）
- 新增测试 `libs/case_runner/test_defaults.py`

**接口契约（三块子代理共用，不得自行改写）**

```python
# libs/case_runner/core/defaults.py
DATA_DEFAULTS_FILE: Path       # <repo>/libs/case_runner/data/case_defaults.json
CONFIG_OVERRIDE_FILE: Path     # <repo>/config/case_defaults.json（不存在 = 不覆盖）
def load_defaults(refresh: bool = False) -> dict[str, Any]
def default_for(key: str, fallback: Any = None) -> Any
def default_meter_addr() -> str          # 默认 "123456789012"
def default_mapping_id(role: str) -> str  # role ∈ {"simcon","simeter","sta"}
def defaults_sources() -> dict[str, str]  # 每个键来自哪个文件（诊断/show 用）
```

```python
# libs/case_runner/blocks/common.py
def archive_addr_of(meter_addr: Any) -> str   # 12 位串 = reverse6(电表 645 人读地址)；非法 → ""
def meter_addr_wire(meter_addr: Any) -> bytes  # = bytes.fromhex(电表 645 人读地址)，不反转；非法 → b""
```

- `case_defaults.json` 出厂内容：`{"meter_addr": "123456789012", "mapping_id": {"simcon": "simcon", "simeter": "simeter", "sta": "sta-main"}}`
- 覆盖规则：`config/case_defaults.json` 存在时**按顶层键覆盖**（`mapping_id` 按子键合并）。
- 声明里 `"meter_addr": null` 表示「取默认」；`loader` 加载时把 `null` 解析成 `defaults.default_meter_addr()`，因此 `case_run show` 与 `--params` 都能看到/覆盖它。

**先写失败测试（TDD）**
- `test_defaults.py::test_archive_addr_of_double_direction`：`123456789012↔129078563412`、`000000000001↔010000000000` 双向；非法输入返回 `""`。
- `test_defaults.py::test_layers_override`：临时 config 覆盖 `meter_addr` 后 `default_meter_addr()` 变化，删掉恢复。
- `test_defaults.py::test_wire_not_reversed`：`meter_addr_wire("123456789012") == bytes.fromhex("123456789012")`。

**验收命令**
```bash
.venv/bin/python -m pytest libs/case_runner/test_defaults.py -q
grep -rn '\[::-1\]' libs/case_runner/blocks/     # 只命中 common.py
```

### S1.2 声明 JSON 全量改名（子代理 `addr-decl`）

**修改文件**（只碰 JSON，不碰 `.py`）
- `libs/case_runner/data/blocks/*.json`（15）：`action.params.addr` → `meter_addr: null`；`action.params.meters` → `meter_addrs: null`；`assert` 里的 `addr` → `meter_addr`
- `libs/case_runner/data/facts/*.json`（8）：`params_schema.addr` → `meter_addr`；有 `required:true` 的改为 `required:false` + `default: null`
- `libs/case_runner/data/cases/*.json`（5）、`data/case_suites/*.json`（3）：`steps[].do/expect` 文案里出现「addr」的改成「表地址（meter_addr）」
- `title` 里写死「addr」的同步改成「表地址」

**硬要求**
- 全库 `grep -rn '"addr"' libs/case_runner/data/` → 0；`grep -rn '000000000001' libs/case_runner/data/` → 0（默认值统一进 `case_defaults.json`）
- 每个 JSON 保持合法、键序不乱（`python -c "import json,glob;[json.load(open(f)) for f in glob.glob('libs/case_runner/data/**/*.json',recursive=True)]"` 无异常）

**验收命令**
```bash
.venv/bin/python tools/scripts/case_run.py check      # loader problems == []
.venv/bin/python tools/scripts/case_run.py show archive.add   # 参数面出现 meter_addr，默认值 123456789012
```

### S1.3 块执行体改造（子代理 `addr-blocks`）

**修改文件**（只碰这 5 个 py + 其对应测试）
- `libs/case_runner/blocks/archive.py`：`action.get("addr")` → `meter_addr` + `archive_addr_of()`；`meters` → `meter_addrs` 逐个换算；回查判据比对档案地址
- `libs/case_runner/blocks/meter.py`：`addr_probe` / `read_645` / `brain_local` 读 `meter_addr`，构帧用 `meter_addr_wire()`
- `libs/case_runner/blocks/facts_meter.py`：`sta.meter_addr_known` 读 `meter_addr`
- `libs/case_runner/blocks/facts_nwk.py`：`archive.contains` 读 `meter_addr`，比较前 `archive_addr_of()`
- `libs/case_runner/blocks/nwk.py`：只改文案/参数名，判定不动

**硬要求**
- 默认值兜底：`normalize_addr(options.get("meter_addr")) or default_meter_addr()`（顺序：入参 > 声明默认 > 配置默认）
- **禁止**在块内出现 `[::-1]`（反转只许在 `common.py`）
- 块态语义不变（pass/fail/skipped/unknown），本阶段不引入 `blocked`
- 既有块测试按新参数名改预期（Q3-A：可改预期，不删用例）

**验收命令**
```bash
.venv/bin/python -m pytest libs/case_runner -q          # 全绿
.venv/bin/python tools/scripts/case_run.py run --case meter_read --json   # 不传地址即可跑；真实台架期望 3/3 PASS
```

### S1.4 单入口服务发现（子代理 `addr-core` 顺带完成，D2-3）

- 工作台启动时写 `data/runtime/workbench.json`：`{"host","port","pid","started_at","base_url"}`（`apps/workbench/run.py` 与 `desktop.py` 各一处，写失败只 warning 不阻塞）
- CLI 解析顺序：`--base-url` > `WORKBENCH_BASE` > `data/runtime/workbench.json` > `http://127.0.0.1:8790`（末档打一行提示「未发现运行时文件，按本机默认端口」）
- 能力层**不读**该文件（§3.6 不写死原则仍在）

**验收**
```bash
.venv/bin/python tools/scripts/case_run.py run --case bench_ready --json   # 零地址参数即可拿到真实结论
```

### S1 汇总门禁（Lead 执行后汇报）

```bash
.venv/bin/python -m pytest libs/case_runner -q                # 全绿；444 → ≥444（只增不减）
.venv/bin/python -m pytest apps/workbench/test_cases_api.py apps/workbench/test_autotest_api.py -q
.venv/bin/python tools/scripts/case_run.py run --case meter_read --json     # 真实台架 3/3 PASS
```

---

## 3. S1.5：编排模型 —— 步骤实例化（D3-1..D3-5）

**要解决的问题**（用户原话口径）：现在 `CaseSpec.blocks` 只是块 id 列表、参数全 run 只有一份，**同一个动作跑两次且参数不同做不到**（「下发档案」要下发 3 块不同档案就无解）。升级为「步骤实例化」但仍保持线性，不做树。

### 模型（子代理 `orchestration`）

- `libs/case_runner/core/models.py`
  - 新增 `StepSpec`：`id`（用例内唯一）、`block`（块 id）、`params: dict = {}`、`on_fail: Literal["stop","continue"] = "stop"`、`note: str = ""`
  - `CaseSpec` 增 `plan: list[StepSpec] = []`；`blocks` 改为**可选**（保持兼容）
  - 校验：`blocks` 与 `plan` **至少给一个**；两者都给 → 以 `plan` 为准并在 `case_run check` 里给一条提示；`plan` 内 `id` 重复 / `block` 不存在 → 校验失败
  - `steps`（`{no, do, expect}` 人读说明）保持不动
- `libs/case_runner/core/planner.py`：`plan()` 改按 `plan` 展开（无 `plan` 时由 `blocks` 自动展开，行为与现在完全一致）；`PlannedBlock` 增 `step_id`，允许同一 `block_id` 出现多次
- `libs/case_runner/core/executor.py`：按 step 顺序执行；块记录带 `step_id`；`on_fail=stop` 时下游 skipped（等价现在的 stop_on_fail），`on_fail=continue` 时后续照跑；用例级 `stop_on_fail` 仍作为方案级默认保留
- **参数优先级**（D3-2）：run 级 `--params` > `step.params` > 声明默认（`null` → 配置默认）
- `libs/case_runner/cli.py`：`show <case>` 增「步骤表」（step id / 块 / 关键参数 / on_fail）；`run --json` 每块输出带 `step_id`（`cli.py` 上一阶段刚被 `addr-cli` 改过，本任务只做增量）

### 资产

- 新增 `libs/case_runner/data/case_suites/meter_smoke_min.json`：`["bench_ready","archive_ready","meter_read"]`（≈40s 一轮，不复位不组网）→ 本需求「零参与」验收主线
- 新增用例 `data/cases/archive_multi.json`：`plan` 里两次 `archive.add`（同块、不同 `meter_addr`），**只用于单测**（D3-5：台架只有 1 块真表，真机不跑）

### 失败测试先写（子代理 `orchestration` 负责）

- `test_models.py`：`plan` 解析；`id` 重复 → 报错；`block` 不存在 → 报错；`blocks`+`plan` 并存 → 以 plan 为准并提示；两者都缺 → 报错
- `test_planner.py`：同一块两次实例化 → 两个 `PlannedBlock`，`step_id` 不同、参数各自独立
- `test_executor.py`：顺序执行；`on_fail=continue` 后续照跑；`on_fail=stop` 下游 skipped；`case_run` 结果里 `step_id` 可见
- `test_cli.py`：`show meter_read` 输出步骤表；`run --json` 每块带 `step_id`

### 验收命令

```bash
.venv/bin/python -m pytest libs/case_runner -q          # 全绿；用例数只增不减
.venv/bin/python tools/scripts/case_run.py check        # loader problems == []
.venv/bin/python tools/scripts/case_run.py show meter_read
.venv/bin/python tools/scripts/case_run.py show meter_smoke_min
```

### 反向兼容门禁

- 老用例（只有 `blocks`、没有 `plan`）必须行为完全不变：`pytest libs/case_runner -q` 里 0041 既有用例不掉
- `cases_api.py` / UI 尚未接（S4/S5 再接），本阶段不要求

---

## 4. S2：幂等 / 等待 / 自愈 / probe 闸

### S2.1 幂等（子代理 `idem-wait`）

- `archive.add`：先 `10H-F2` 查档 → **已存在** → `pass` + detail「已存在，跳过下发」；**查不全**（`complete=False`）→ `unknown`（不得当不存在去下发）；不存在 → 下发 `11H-F1` + 确认 + 回查双判据
- `archive.del`：不存在 → `pass` + detail「本就不存在」
- 失败测试：`test_archive_nwk.py::test_add_idempotent_existing`（fake 已存在 → pass）、`test_del_absent_passes`
- 验收：`.venv/bin/python tools/scripts/case_run.py run --suite smoke_meter_noreset` 连跑两次，两次 verdict 一致

### S2.2 等待（子代理 `idem-wait`）

- `nwk.route_status`：`wait_s` 默认 60、轮询 2s、只发只读查询；到「抄表」→ pass；学习态持续到超时 → `unknown` +「等了 N 秒仍处学习态」
- `nwk.nodes_online`：`wait_s` 默认 90，同上
- `reset.sta`：沿用既有 30s 指纹等待
- 失败测试：`test_route_status_waits_until_running`、`test_route_status_timeout_detail`（含「等待期间只有只读帧」断言：fake 能力调用序列）

### S2.3 自愈（子代理 `selfheal`，只碰 `blocks/common.py` + `core/executor.py` 的留痕字段）

- 命中「端口占用 / 会话残留」→ **不看归属直接抢占**（D2-7）：`close` → 重试**一次** → 成功则 pass 并留痕
- `self_healed: [{action, target, result, detail}]` 落到 `BlockResult` → 聚合到 `CaseRunResult.to_dict()`
- 禁止：`subprocess` / `pkill` / 重启服务（代码审查项；门禁 `grep -rn "subprocess\|pkill\|os.system" libs/case_runner/blocks/` 只允许既有零命中）
- 失败测试：fake 409 → close + 重试 → pass 且 `self_healed` 非空；二次仍 409 → `skipped`（S3 后才是 `blocked`）

### S2.4 `probe --readonly`（子代理 `probe-gate`，只碰 `cli.py`）

- 默认行为**不变**（全跑，含写块）；显式 `--readonly` 时只跑只读块（声明的 `tags` 含 `readonly`），写块记 `skipped` + 「--readonly 已跳过写块」
- 失败测试：`test_cli.py::test_probe_readonly_skips_write_blocks`

### S2 汇总门禁

```bash
.venv/bin/python -m pytest libs/case_runner -q
.venv/bin/python tools/scripts/case_run.py run --suite smoke_meter_noreset --json   # 连跑两次，verdict 一致
.venv/bin/python tools/scripts/case_run.py probe --readonly --all --json            # 复位类块不被执行
```

---

## 4. S3：三分类 + 预检 + 退出码 + 落盘证据

- **模型层**（子代理 `verdict`，碰 `core/models.py` / `core/executor.py`）
  - `OUTCOMES` 增 `blocked`；`BLOCK_TO_VERDICT = {pass→pass, fail→fail, skipped→blocked, unknown→inconclusive}`
  - 优先级 `fail > blocked > inconclusive > pass`；`_verdict()` 与方案级聚合同口径
  - 断言层 `AssertionResult` **一字不改**（D2-4）
  - 失败测试：四态聚合 + 优先级 + 「unknown/skipped 永不计入 pass」
- **CLI 层**（子代理 `verdict-cli`，碰 `cli.py`）
  - 退出码：0 pass / 1 fail / **2 inconclusive** / 3 用法 / 4 加载失败 / **5 blocked**
  - `--timeout` 默认 600s（D2-6）：超时按 `inconclusive` 收尾，detail 写明卡在哪个块
  - 落盘 `data/case_runs/<run_id>/result.json`：`verdict`/`failed_block`/`blocked_block`/`unmet_facts`/`self_healed`/台架快照
- **预检**（子代理 `verdict-cli`）：run 开始前探「所选用例 needs 并集」，不满足 → 整轮 `blocked` + 缺项清单，**<1s 返回、不逐用例跑**；缺参同理（D4-2：启动前判定，不提问）
- **零介入不变量落点**（子代理 `verdict`）：`result.json` 增 `decisions[]`（自愈/重试/等待轮询/超时收尾，各带 `kind/attempts/reason`）、`interventions[]`（**恒为空数组**，任何写入即本次运行不成立）、`started_at`/`finished_at`/`duration_s`；`params` 为启动瞬间快照（D4-1）
- **静态门禁测试**（子代理 `verdict`）：`test_zero_intervention.py` —— ①执行路径无 `input(`/`sys.stdin`；②无 AI 调用；③`interventions` 恒空；④fake 能力永不返回时按 `timeout` 收敛为 `inconclusive`（D4-5）
- 验收
  ```bash
  .venv/bin/python tools/scripts/case_run.py run --suite meter_only --params '{"meter_addr":"999999999999"}' --json  # 期望 fail（产品/链路真失败）
  .venv/bin/python tools/scripts/case_run.py run --suite meter_only --base-url http://127.0.0.1:1 --json            # 期望 blocked（台架不具备，1s 内）
  .venv/bin/python tools/scripts/case_run.py run --suite meter_only --timeout 5 --json                              # 期望 inconclusive（说不清）
  ```

---

## 5. S4：API 层

- 升级 `apps/workbench/cases_api.py`（骨架 1184 行 / 9 端点可复用）：补 `/api/cases/bench`、`blocked` 字段、预检、`self_healed`、`verdict` 四态
- `app.py`：紧跟 autotest 之后 `include_router(create_cases_router(storage_dir=storage_dir))`（删掉 495-498 的「未挂载」注释）
- 判定逻辑**一行都不许**进 API（只装配/调度/落盘）；`base_url` 注入优先级 `body.base_url` > `WORKBENCH_BASE` > 请求 origin
- 验收：`pytest apps/workbench/test_cases_api.py apps/workbench/test_autotest_api.py apps/workbench/test_app.py -q` 全绿；`grep -rn '8790' apps/workbench/cases_api.py` → 0

---

## 6. S5：UI 层

- 新增 `apps/workbench/static/pages/cases/{cases.html,cases.js}`；`static/app.js` 导航**新增「用例库」并移除「自动测试」**（D2-11，旧页文件保留）
- UI-1..7：列表（用例名+块数+步骤摘要，可展开）/ 台架就绪横幅（不满足置灰+原因）/ 勾选存方案（刷新恢复）/ 两级运行 + job 轮询 / **断点可视化**（failed_block、blocked_block、unmet_facts）/ 三态着色 / 主题令牌 0 issue
- 方案落盘 `data/case_suites/`，内置 3 个作首次种子后完全可改（D2-12）
- 验收：`node reqs/0012-workbench-theme-refactor/verify-theme-coverage.js` 0 issue；`pytest apps/workbench/test_app.py -q`；Playwright 全链路截图留证 `evidence/0042/`

---

## 7. S6：零参与收口（DoD）

> **验收块范围按 D3-4**：必验 6 块（`fix.simcon_session` / `fix.simeter_session` / `archive.add` / `meter.addr_probe` / `meter.read_645` / `meter.brain_local`）；`nwk.route_status` + `nwk.nodes_online` 只做**一次性抽样**给等待机制留证据；其余 7 块仅改名 + 单测。

| DoD | 动作 | 期望 |
|---|---|---|
| 1 零参与 | `case_run run --suite meter_smoke_min`（**一个字参数都不传**） | ≈40s 内拿到结论，中间无提问/无补参 |
| 2 单参数 | `case_run run --case meter_read --params '{"meter_addr":"123456789012"}'` | meter_read 3/3 PASS |
| 3 可重复 | `meter_smoke_min` 连跑两次 | verdict 一致（不得因档案已存在 FAIL） |
| 4 三态 | 三种场景各一次 | fail / blocked / inconclusive，UI 着色正确 |
| 5 自愈 | 占用 simcon 口后跑一次 | 自愈一次并通过；再失败给 blocked + 原因 |
| 6 等待 | 抽样跑一次 `nwk_join` | `nwk.route_status` 在 `wait_s` 内等到抄表或超时 inconclusive |
| 7 测试 | `pytest libs/case_runner apps/workbench/test_cases_api.py -q` | 全绿；分层铁律 0 |
| 8 不回归 | `/api/autotest/*` | 全绿 |
| 9 UI | 浏览器全链路 | 勾选→存方案→刷新恢复→运行→见三态与断点 |
| 10 落盘 | `data/case_runs/<run_id>/result.json` | 含 verdict/断点/`step_id`/self_healed/台架快照 |

收口后：`TODO.md` 勾选 → `DONE.md` 追加 → `REQS-INDEX.md` 状态更新 → 每阶段一次 git 提交（Q9-A）。

---

## 8. 风险与回滚

| 风险 | 处置 |
|---|---|
| 地址改名面广（15+8+5+3 JSON + 5 py） | 三块子代理范围互斥；每块完成即跑 `pytest libs/case_runner -q`；失败 `git checkout -- <范围>` 回滚该块 |
| 默认值进了代码库（台架地址） | 集中在 `case_defaults.json` 一处；换台架只改一份，或 `config/case_defaults.json` 覆盖 |
| 自愈抢占踢掉人的会话 | 用户已定（D2-7）：主动跑用例优先；`self_healed[]` 留痕，事后可查 |
| 真机验证打乱台架状态 | 每轮真机验证前先 `probe --readonly`；验证后记录档案数与路由状态到证据文件 |
| **单测打到真实工作台**（D2-3 副作用，本轮已发生 1 次） | 默认地址兜底生效后，任何「无台架」测试必须显式关兜底：autouse fixture 把运行时文件指到 tmp、`DEFAULT_BASE_URL=""`、清 `WORKBENCH_BASE`。`test_cli.py` 已加 `isolate_service_discovery`；S2/S3 新增测试照此办理。**更正（2026-11-23 16:1x 取证）**：那次测试实际打了真实工作台，帧日志里 15:47:48 `11H-F1`（建档）、15:47:51 `11H-F2`（删档，删掉了真表的档案）、15:48:14 `11H-F5` 的 `run_id` 都是 `run-verify.task-*`（不是 `case_run` 风格），所以「按 run_id 过滤 = 0 条」的判据是错的。后续硬规矩：① 子代理一律禁止跑 `case_run run` / `probe --all`；② 「无台架」测试必须在传输层直接拒绝（不只关地址兜底）；③ 破坏性块（`archive.del`）已加「显式才删」闸，正是为挡这类误伤 |
| 运行期有人在改台架/改参 | D4-1..D4-4：参数快照 + `interventions[]` 恒空 + `decisions[]` 留痕；S6 按 D4-6 单条命令取证，运行期间只读观察 |
| 测试预期值改动掩盖回归 | 只改与旧口径直接冲突的几条；新增用例覆盖新行为；用例数只增不减 |

---

## 9. 派发记录

| 阶段 | 任务 id | 子代理 | 写入范围 | 状态 |
|---|---|---|---|---|
| S1 | — | `addr-core` / `addr-decl` / `addr-blocks` | 见 S1.1–S1.4 | 待派发 |
