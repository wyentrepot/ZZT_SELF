# REQS.md — 需求基线（需求 ID：0041，标题：测试用例库与用例方案）

> 本文件遵循「需求只追加、不覆盖」ADR 模式。
> 立项日期：2026-11-23；基线版本：**v3**（当前生效正文见 **§10**；§2/§3/§4 中与本文件 §10 冲突者以 §10 为准，冲突项已在变更 3 逐条列明）。
> 依据：REQS-0032（帧工具）、REQS-0025（用例库只读字典）、REQS-0031（统一壳）；
> 现状调研见 §1「现状与证据」，全部由本会话实测/读码确认。

---

## 1. 背景与现状（均带证据）

### 1.1 用户诉求

**把自动测试部分规整成一个「测试用例库」功能：用例可勾选组合成「用例方案」，每次按方案加载执行；也支持临时加载单个用例。UI 上一条用例 = 用例名 + 用例步骤说明。**

并明确约束：**框架设计合理性为第一要素**（不为赶功能牺牲分层与契约）。

### 1.2 现状：四层用例资产，彼此不连通

| 层 | 位置 | 规模 | 能加载 | 能单独执行 |
|---|---|---|---|---|
| ① 只读用例字典 | `libs/case_library`（279 条，`data/cases.json` 152 KB） | 279 | ✅ | ❌ 纯元数据无 `run` |
| ② 声明式契约 | `libs/test_automation/models.py:144` `CasePackage` | — | ✅ | ⚠️ **有模型无执行器** |
| ③ 场景编排 | `apps/workbench/scenarios/*.json` | 6 | ✅ | ⚠️ 面向链路非单例 |
| ④ 真实执行器 | **`/01-workfile-ai/01-zzt/autotest/`（仓库外）** | 22 | ✅ | ✅ 实测 PASS |

**实测证据（本会话）**：

```
$ python3 /01-workfile-ai/01-zzt/autotest/runner.py --case simeter_handle_hex_645
===== 汇总: 1/1 PASS =====
PASS simeter_handle_hex_645  645 读 00010000 应答 68...9F16(value=001234.56)...
```

### 1.3 六个已确认的具体问题

| # | 问题 | 证据 |
|---|---|---|
| P-1 | **真正的执行器在仓库外，且不在 git 里** | `git -C /01-workfile-ai/01-zzt/autotest rev-parse` → `fatal: not a git repository`；22 个用例 + 60+ docs + results 索引无版本管理、无备份 |
| P-2 | **97 条声明式用例迁移好了却没引擎执行** | `libs/gw_cass_migrate/migrator.py` 产出 `CasePackage`（含 `assertions`），但全仓无消费 `CasePackage.assertions` 的执行器；`orchestration/runner.py:388` 的 assertions 来自运行时观测，非用例声明 |
| P-3 | **UI 勾选不持久、不可命名** | `static/pages/autotest/autotest.js:8` `var selected = {}` 纯内存；刷新即丢；无「方案」概念，只有 `chains.json` 硬编码链路（仓库外） |
| P-4 | **用例元数据不足以呈现「用例名 + 步骤说明」** | `case_library` 条目仅 `purpose`+`frames`，无 `steps`；`TestSce.json` 有 `步骤[{操作,预期结果}]` 但未进入 UI 任何页面 |
| P-5 | **三套用例源格式各异、无统一标识** | `TestSce.json`（97/79 条，中文键名）、`autotest/cases/*.py`（`CASE{}` + `run(ctx)`）、`case_library`（269 项体系）互不引用 |
| P-6 | **执行后端硬耦合仓库外路径** | `apps/workbench/autotest_api.py:41` `resolve_autotest_root()` 靠环境变量/家目录/父目录探测，交付时必然断链 |

### 1.4 与 Auto-CI-test 的关系（避免重复造轮子）

本会话已核实：`/01-workfile-ai/GW-CASS/TestSce.json`（107,164 B）与 Auto-CI-test 的 `TestSce.json`（107,464 B）**同源**（结构一致，后者多「湖南扩展」一类）。工作台**已有** `gw_cass_migrate` 消费该格式并产出 `CasePackage` + 10 个单测。

→ **结论：不再抄 Auto-CI-test 的用例格式；本需求是把它已迁移的资产接上执行器并统一进 UI。**

---

## 2. 目标

一句话：**建立「用例(Case) → 方案(Suite) → 运行(Run)」三层模型与统一执行契约，让用例资产进入仓库受版本管理，UI 可勾选组合成方案、按方案或单例加载执行，并产出可对比的结果。**

---

## 3. 框架设计（本需求第一要素，先定骨架再填内容）

### 3.1 分层与依赖方向（必须严格单向）

```
┌─────────────────────────────────────────────────────┐
│ UI 层  apps/workbench/static/pages/cases/           │  勾选/方案编辑/运行面板
├─────────────────────────────────────────────────────┤
│ API 层 apps/workbench/catalog_api.py                │  /api/cases/*（列表/方案/运行/结果）
├─────────────────────────────────────────────────────┤
│ 编排层 apps/workbench/orchestration/catalog.py      │  Suite → 执行计划 → 调度（串行/停链）
├─────────────────────────────────────────────────────┤
│ 库层   libs/case_catalog/                           │  模型/加载/校验/索引（纯数据，无执行）
│        ├ models.py  CaseSpec / SuiteSpec / Catalog  │
│        ├ loader.py  多源加载 + 归一化               │
│        └ store.py   方案持久化（JSON，人可读）      │
├─────────────────────────────────────────────────────┤
│ 后端层 libs/case_catalog/backends/                  │  执行后端（可插拔）
│        ├ base.py     CaseBackend 抽象               │
│        ├ python.py   命令式用例（autotest def run） │
│        └ declarative.py  声明式 CasePackage 执行器  │
├─────────────────────────────────────────────────────┤
│ 现存能力（不改契约）test_automation / simcon /      │
│                    simeter / module_log / parser    │
└─────────────────────────────────────────────────────┘
```

**铁律**：
1. `libs/` 不得 import `apps/`（现状已保证：`grep '^from apps' libs/` = 0 命中，本需求必须维持）。
2. 库层（`libs/case_catalog`）**只做数据与校验，不做 IO 执行**；执行一律经 `backends/`。
3. 现有 `case_library`（只读字典）**不改造**，`case_catalog` 是**新库**，二者职责不同：
   - `case_library` = 国网检测条目**字典**（知识资产，279 条，可无执行体）
   - `case_catalog` = 可执行**用例目录**（含执行后端绑定与步骤，是运行对象）
4. 现有 `autotest` 页面与 `/api/autotest/*` **保持兼容**（不破坏用户现有习惯），新页面 `/api/cases/*` 并行提供。

### 3.2 三层核心模型

| 层 | 模型 | 语义 | 关键字段 |
|---|---|---|---|
| **Case** | `CaseSpec` | 最小可执行单元 | `id` / `title` / `steps[]` / `backend` / `payload` / `tags` / `supported` / `requires[]` |
| **Suite** | `SuiteSpec` | 用户勾选组合成的方案 | `id` / `name` / `case_ids[]` / `stop_on_fail` / `order` / `notes` |
| **Run** | 复用现有 `Run`（`test_automation/models.py:212`） | 一次执行实例 | 沿用 `run_id` / `status` / `evidence` / `assertions` |

`CaseSpec.steps` 直接对齐 UI 展示需求：

```json
{
  "id": "cco_03f1_vendor_version",
  "title": "CCO 03H-F1 厂商代码和版本信息查询",
  "steps": [
    {"no": 1, "do": "模拟集中器下发 AFN=03H-F1 查询", "expect": "CCO 返回厂商代码和版本信息，帧格式正确"}
  ],
  "backend": "python",
  "payload": {"module": "autotest.cases.cco_03f1_vendor_version"},
  "tags": ["cco-afn", "smoke"],
  "supported": "yes",
  "requires": ["simcon"]
}
```

### 3.3 执行后端契约（可插拔，解决 P-2/P-4/P-5）

```python
class CaseBackend(ABC):
    name: str
    @abstractmethod
    def probe(self, case: CaseSpec) -> BackendHealth:   # 能力/依赖是否满足
    @abstractmethod
    def execute(self, case: CaseSpec, ctx: RunContext) -> CaseOutcome:
```

两个内置后端：

| 后端 | 执行对象 | 来源 | 现状 |
|---|---|---|---|
| `python` | `def run(ctx)` 命令式用例 | autotest 22 例迁入 | 能跑，需搬进仓库 |
| `declarative` | `CasePackage` 的 `assertions` | gw_cass_migrate 97 例 | **引擎需新写**（P-2 的核心） |

**这正是本需求的技术核心**：`declarative` 后端把「迁移好的 97 条声明式用例」真正变成可执行，补齐工作台最大的能力断点。

### 3.4 存储位置（解决 P-1/P-6）

| 内容 | 路径 | 入库 |
|---|---|---|
| 用例目录（含执行体） | `libs/case_catalog/data/cases/**` | ✅ |
| 用例索引（生成物） | `libs/case_catalog/data/index.json` | ✅ |
| **用例方案（用户资产）** | `data/case_suites/*.json` | ✅（人可读、可 diff、可 git 管理） |
| 运行结果 | `data/case_runs/<run_id>/` | ❌（gitignore，沿用现有 `data/reports` 策略） |

**方案存 JSON 而非 DB**的理由：用户要在 git 里看见「谁把哪个用例加进了哪个方案」，DB 做不到可 diff。

---

## 4. 需求点（编号即验收锚点）

### 4.1 用例目录（Catalog）

| 编号 | 需求 | 验收 |
|---|---|---|
| CC-1 | `CaseSpec` 模型 + JSON schema 落盘；含 `id/title/steps/backend/payload/tags/supported/requires` | 一条用例含 ≥1 个 `steps[]`，`steps[].do` 与 `steps[].expect` 必填 |
| CC-2 | 多源加载器：`python`（autotest 迁移）、`declarative`（gw_cass）、`case_library`（只读展示）三源归一为一个 Catalog | `GET /api/cases` 返回三源合并列表，每条带 `source` 字段 |
| CC-3 | **把 `autotest/` 迁入仓库** | `git ls-files` 可见全部 22 个用例 + runner；仓库外原目录保留只读副本并在 README 标注迁移 |
| CC-4 | 用例校验器：id 唯一、backend 已注册、`requires` 可解析、`steps` 非空 | 非法用例在加载期被拒并给出 `file:reason`，不静默跳过 |
| CC-5 | 索引自动生成（启动/刷新时） | `data/index.json` 与 `data/cases/**` 一致；可 `--check` 校验 |

### 4.2 用例方案（Suite）

| 编号 | 需求 | 验收 |
|---|---|---|
| SU-1 | `SuiteSpec` 模型 + `data/case_suites/*.json` 持久化 | 创建/重命名/删除方案，文件落盘可 diff |
| SU-2 | 方案 CRUD API：`GET/POST/DELETE /api/cases/suites` | 4 个端点全绿 + 单测 |
| SU-3 | **UI 勾选生成方案**：勾选用例 → 存为方案 → 下次按方案加载 | 刷新页面后方案仍在；勾选状态随方案恢复（解决 P-3） |
| SU-4 | 方案内显式顺序 + `stop_on_fail` | 顺序按用户排列执行；`stop_on_fail=true` 时首个 FAIL 即停并标注剩余 skipped |
| SU-5 | 兼容既有 `chains.json` | 原 chain 作为只读方案导入，不破坏现有用法 |

### 4.3 执行（Run）

| 编号 | 需求 | 验收 |
|---|---|---|
| RU-1 | `CaseBackend` 抽象 + 注册表；`python` 与 `declarative` 两后端 | `probe()` 对缺依赖用例返回 not-ready 而非崩溃 |
| RU-2 | **`declarative` 后端执行 `CasePackage.assertions`**（本需求技术核心） | 97 条 gw_cass 用例中至少 1 条端到端跑通并产出 assertions 结果 |
| RU-3 | 按方案执行 / 单例即时执行 两种模式 | `POST /api/cases/run {suite_id}` 与 `{case_ids:[...]}` 均可用 |
| RU-4 | 执行前置 `requires` 检查（simcon lease / 串口占用） | 资源被占返回明确原因，不静默排队 |
| RU-5 | 结果落盘 + 复用现有 evidence 口径 | 每次运行 `data/case_runs/<run_id>/result.json`；沿用 L1/L2/L3 |
| RU-6 | 结果可对比：同一方案两次运行差异 | 能回答「这次比上次多挂哪几条」 |

### 4.4 UI

| 编号 | 需求 | 验收 |
|---|---|---|
| UI-1 | 新页面「用例库」：列表 = **用例名 + 步骤说明**（可展开） | 每行显示 title 与 steps 摘要；展开见完整 do/expect |
| UI-2 | 勾选 + 方案下拉 + 「存为方案」 | 与 SU-3 联动 |
| UI-3 | 单例「立即运行」+ 方案「按方案运行」 | 两按钮各自可用，job 面板沿用现有轮询 |
| UI-4 | 支持/依赖可视化（`supported`/`requires` 未满足置灰并给原因） | 缺 simcon 等依赖的用例不可运行且说明原因 |
| UI-5 | 主题令牌合规（沿用 0012 三层 token，禁硬编码色值） | 主题静态校验 0 issue |

---

## 5. 非目标（明确不做）

- ❌ 不改 `libs/case_library`（只读字典）的数据结构与用途
- ❌ 不做用例的「录制/回放」生成
- ❌ 不做跨机器分布式调度
- ❌ 不重写 `autotest/runner.py` 的 CLI（保留兼容，内部改为委托新库）
- ❌ 不实现 Auto-CI-test 的 Jenkins/报告/通知部分（另行评估，不在本需求）

---

## 6. 验收标准（DoD）

1. `python3 -m pytest libs/case_catalog apps/workbench/test_catalog_api.py` 全绿。
2. `autotest/` 22 个用例已在 git 中，仓库外副本可删而不影响功能。
3. UI 可：勾选 ≥2 个用例 → 存为方案 → 刷新后恢复 → 按方案运行 → 见结果。
4. 单例即时运行可用，与按方案运行互不干扰。
5. `declarative` 后端至少 1 条 gw_cass 用例端到端跑通（证明 P-2 断点已补）。
6. `grep -rn '^from apps' libs/` 仍为 0 命中（分层未被破坏）。
7. 既有 `/api/autotest/*` 与 autotest 页面功能不回归。

---

## 7. 风险

| 风险 | 应对 |
|---|---|
| `declarative` 断言表达力不足（从中文「预期结果」正则提取，易失真） | 先做 1-2 条样板人工校准；提取失败标 `unsupported` 而非假 PASS |
| 迁 `autotest` 时路径变化导致用例内硬编码断链 | 迁移后用 `pytest` + 实跑 1 例验证；用例内路径统一走 `shared.infra` |
| 两套 API（`/api/autotest` 与 `/api/cases`）长期并存造成分裂 | 明确 `/api/cases` 为新权威，`/api/autotest` 标废弃预告，下一需求移除 |
| 串口资源竞争（方案含多口用例） | 沿用现有 lease 机制；`requires` 声明资源，运行前统一申请 |

---

## 8. 变更记录

> 导航：变更 1、变更 2 在本节；**变更 3 因「只追加」纪律落在 §9 之后**（见文件 §10 之前），
> 其后为当前生效正文 §10。

### 变更 1 ｜ 2026-11-23 ｜ 立项
- **改成什么**: 建立需求基线 v1（用例库三层模型 + 可插拔执行后端 + UI 方案化）
- **为什么**: 用户要求规整自动测试部分，建立「用例库 + 按方案加载」能力；现状四层资产不连通、执行器在仓库外
- **影响**: 新增 `libs/case_catalog/`、`apps/workbench/catalog_api.py`、UI 新页面；迁入 `autotest/`；不动 `case_library` 与 `test_automation` 契约
- **变更前基线**: 无（新建）
- **变更后基线**: v1（本文）
- **被取代**: 无

### 变更 2 ｜ 2026-11-23 ｜ 写 PLAN 时实测修正 RU-2 前提
- **改成什么**: 基线 **v2**。新增 §3.5「已知技术事实」，把 RU-2 的技术前提从「执行 CasePackage.assertions」修正为「**先解析激励 → 再下发 → 再断言**」的三步，并新增需求点 RU-7「能力分级」。
- **为什么**: 写 PLAN 时实测发现三条硬事实（见 §3.5），v1 的 RU-2 表述隐含假设「用例自带激励」，与实测不符。若不修正，实现会做成一个只能判「期望看到什么」却不知「该发什么」的空后端。
- **影响**: RU-2 验收表述加严；`declarative` 后端拆为「激励提取器 + 断言求值器」两个可独立测试的单元；`CaseSpec.supported` 增加 `unsupported` 语义（用于「无法提取激励」）。不影响 CC/SU/UI 各条编号。
- **变更前基线**: v1（REQS.md 变更 1）
- **变更后基线**: v2（本文 §3.5 + RU-7）
- **被取代**: 变更 1 中 RU-2 的技术表述

---

## 9. 已知技术事实（v2 增补，均为实测）

写 PLAN 阶段对 `gw_cass_migrate` 迁移产物做了实测，三条事实决定实现方式：

### 事实 A：迁移出的 CasePackage 没有激励信息

```
迁移用例数: 97
source/firmware/flash/boot/observe/cleanup: 全部 None
device: DeviceSpec(resource_type='serial_port', resource_id='COM24')
```

`migrator.py:96-113` 只填 `case_id/name/description/parameters/device/assertions`；
`CasePackage` 模型本身也无 `stimulus` 字段（只有 `observe`）。

→ **assertions 只说「期望看到什么」，没说「该发什么」。** `declarative` 后端必须先从 `parameters.steps[].action` 解析激励。

### 事实 B：激励文本可解析但非全覆盖

| 项 | 覆盖 |
|---|---|
| 能从「标题 + steps[].action」提取 AFN/FN | **84 / 97（86.6%）** |
| 有 `assertions` 的用例 | **70 / 97（72.2%）** |
| 不能提取激励的 13 条 | `gw_cass_67 负荷曲线`、`gw_cass_70 外设-继电器Modbus`、`gw_cass_79 抄表功能`、`gw_cass_80 645事件上报` 等 |

→ 必须做**能力分级**，不可执行用例标 `unsupported` 并写明原因，**绝不假 PASS**。

### 事实 C：既有约定（可直接依赖）

- 测试：`PYTHONPATH=apps:libs python3 -m pytest libs/gw_cass_migrate -q` → `10 passed`（实测）。
- 路由：`app.py:492` 用 `create_autotest_router(storage_dir=...)` 工厂 + `include_router`，新 API 沿用。
- 分层：`grep '^from apps' libs/` = `0`（基线已达标，本需求须维持）。

### 新增需求点

| 编号 | 需求 | 验收 |
|---|---|---|
| RU-7 | **用例能力分级**：`yes`（激励+断言均可得）/ `partial`（仅其一）/ `unsupported`（两者皆不可得，或无法提取激励） | 每条非 `yes` 用例必须有非空 `supported_reason`；`unsupported` 永不出现在运行结果里被计为 PASS |

---

### 变更 3 ｜ 2026-11-23 ｜ 用户口述定稿：拆最小块 + 事实前置

- **改成什么**: 基线 **v3**。主线由 v2 的「Case→Suite→Run + 可插拔 CaseBackend + UI 勾选优先」改为
  **「原子块(Block) → 事实(Fact) → 用例(Case) → 方案(Suite)」四层模型，先铺齐全部原子块与事实，再编排用例，UI 本轮做**。
  新增 §10 作为当前生效正文。
- **为什么**: 用户明确诉求为「测试用例拆分成一块一块的最小块，用例只是把这些最小块编排起来」；
  并给出具体链路（升级需先组网 → 组网需先验档案 → 档案需模拟电表在线且 STA 能取到表地址）。
  v2 的模型没有「块」与「事实」两层，导致每条用例必须重写一遍前置动作，且失败无法定位到断点。
  另据用户决策：组网判据**只用 1376.2 帧**，**不接入侦听台**；在线为主（非离线优先）；22 例**一次性全迁**。
- **影响**:
  - 新增 `libs/case_runner/`（core / capabilities / data）、`tools/scripts/case_run.py`、
    `apps/workbench/cases_api.py`、`apps/workbench/static/pages/cases/`。
  - v2 的 `libs/case_catalog` 命名**不再使用**（未动工，无迁移成本）；`CaseSpec` 由 `case_runner.core.models` 承接。
  - `libs/case_library`（279 条只读字典）、`libs/test_automation`（模型）、`apps/workbench/autotest_api.py`
    三处契约**均不改**，沿用 v2 §3.1 铁律 3/4。
  - `docs/api-endpoints-inventory.md` 需补 `/api/cases/*` 段落。
- **变更前基线**: v2（本文 §9 + RU-7；`PLAN.md` 为 v2 计划）
- **变更后基线**: v3（本文 §10；`PLAN.md` 已按 v3 重写）
- **被取代**:
  - v2 §2「目标」中的「可插拔 CaseBackend」表述 → 由 §10.2 四层模型取代
  - v2 §3.3「执行后端契约」→ 由 §10.3 能力适配器契约取代
  - v2 §3.2「三层核心模型」→ 由 §10.2 四层模型取代
  - v2 §4.1 CC-1/CC-3/CC-5 → 由 §10.5 BL-* / CS-* 取代
  - v2 §4.1 CC-2「三源归一加载」→ **本需求不做**：v3 的用例源仅「22 例迁入 + 手写用例 JSON」两源；
    `libs/case_library` 的 279 条只读字典**保留为知识资产、不改造、不进 case_runner 用例目录**
    （见 §10.6 第 1 条）。若日后需要，另行立项。
  - v2 §4.2 SU-1/SU-3/SU-4 保留；SU-5（chains.json 兼容）降级为可选
  - v2 §4.3 RU-1/RU-2/RU-7 → 由 §10.5 RU-* 取代（RU-7「绝不假 PASS」原则被完整继承）
  - v2 §5 非目标、§7 风险中「declarative 断言表达力不足」一条 → 本需求不再以 97 条声明式为主线，降为可选
- **不影响**（v2 中继续有效）:
  - §3.1 铁律 1（`libs/` 不得 import `apps/`）
  - §3.1 铁律 3（`case_library` 不改造）
  - §3.1 铁律 4（`/api/autotest/*` 保持兼容）
  - §3.4 存储位置策略（方案存 JSON 可 diff；运行结果 gitignore）
  - §6 DoD 第 6/7 条（分层不破坏、autotest 不回归）

---

## 10. v3 生效正文：原子块 + 事实 + 编排

### 10.1 用户诉求（原话口径）

> 测试用例拆分成一块一块的，比如升级是先要组网，是必须先验证档案，然后再验证组网；
> 组网是必须 STA 部分获取表地址，也就是模拟电表必须在线，且 STA 能获取表地址。
> 这是一步步来组合成的，我需要的是拆分成每一个最小块，用例只是把这些最小块给编排起来即可。

三条派生约束（用户逐项拍板）：

| 约束 | 用户决策 |
|---|---|
| 组网判据 | **只用 1376.2 帧**：10H-F1 在网数 + 02H-F1 内层 645 地址；**不接入侦听台** |
| 执行面 | **在线为主**（不起服务/不占串口跑不了属正常，离线非主线） |
| 用例资产 | 仓库外 `autotest/` **22 例一次性全迁**入仓库 |
| 实施顺序 | **先铺齐全部原子块，再编排用例** |
| UI | 本轮做（用例名 + 块清单 + 勾选成方案） |

### 10.2 四层模型（取代 v2 §3.2 三层）

```
┌ Case 编排层 ─────────────────────────────────────────────────┐
│ case = { needs:[fact_id], blocks:[block_id], asserts:[…] }     │
│ 只做「事实求值 → 块顺序调用 → 聚合断言」，零帧逻辑              │
└───────────────────────────────────────────────────────────────┘
        │ 依赖
┌ Block 原子块层 ──────────────────────────────────────────────┐
│ block = { id, needs:[fact_id], provides:[fact_id],            │
│           action:{afn,fn,params} | {recipe} | {semantic},     │
│           assert:[…], cleanup:[…] }                           │
│ 一次可独立判定的交互；可单跑、可被任意用例复用                  │
└───────────────────────────────────────────────────────────────┘
        │ 依赖
┌ Fact 事实层（只读观测，不产生激励）──────────────────────────┐
│ fact = { id, probe:<可调用>, schema }                          │
│ 只能「探测」，不能「假设」；探测不到 = unknown，绝不判 PASS    │
└───────────────────────────────────────────────────────────────┘
        │ 依赖
┌ Capability 能力适配层 ────────────────────────────────────────┐
│ 每个文件只对接一个领域库；彼此零 import                          │
└───────────────────────────────────────────────────────────────┘
```

| 层 | 模型 | 语义 | 关键字段 |
|---|---|---|---|
| **Block** | `BlockSpec` | 最小可独立判定的交互 | `id` / `title` / `needs[]` / `provides[]` / `action` / `assert[]` / `cleanup[]` / `tags[]` |
| **Fact** | `FactSpec` | 只读前置事实 | `id` / `title` / `probe`（语义引用）/ `params_schema` |
| **Case** | `CaseSpec` | 编排，本身无帧逻辑 | `id` / `title` / `steps[]`（do/expect，供 UI 展示）/ `needs[]` / `blocks[]` / `asserts[]` / `tags[]` |
| **Suite** | `SuiteSpec` | 用户勾选组合 | `id` / `name` / `case_ids[]` / `stop_on_fail` / `notes` |
| **Run** | 复用 `test_automation.models.Run` | 一次执行实例 | 沿用 `run_id` / `status` / `steps` |

**结论模型不新增**：块结果、事实结果一律落 `test_automation.models.AssertionResult`
（`outcome ∈ pass|fail|inconclusive`），不造第三套。

### 10.3 能力适配器契约（取代 v2 §3.3 CaseBackend）

```python
class Capability(Protocol):
    name: str
    def probe(self) -> Health: ...                  # 依赖是否就绪
```

| 适配器 | 对接的领域库 | 形态 |
|---|---|---|
| `capabilities/simcon.py` | 工作台 HTTP `/api/simcon/*`、`/api/ai/v2/verification-runs` | HTTP，不 import 领域库内部 |
| `capabilities/simeter.py` | 工作台 HTTP `/api/simeter/*` | HTTP |
| `capabilities/frames.py` | `parser_lib.facade`（decode/build/verify） | 进程内，只读 |
| `capabilities/mclt.py` | `libs/minute_assert`（698/645 递归断言） | 进程内，只读 |

**不复制任何逻辑**：
- 构帧一律 `parser_lib.adapters.adapter_10376.encode_app_data` / `sim_concentrator.scenario_codec.build_send`
- 解析一律 `parser_lib.facade.decode`（含内嵌 645/698 递归）/ `sim_concentrator.matcher`
- 执行原语复用 `simcon/verify` 的 `send / expect / recv_only / wait_mclt_stored / read_mclt_11e3`

### 10.4 分层与依赖方向

```
tools/scripts/case_run.py │ apps/workbench/cases_api.py     ① 入口（参数/IO/返回，零测试逻辑）
        ↓
libs/case_runner/core/{models,registry,planner,executor}.py  ② 内核（零领域依赖）
        ↓
libs/case_runner/capabilities/*.py                          ③ 能力适配（彼此零 import）
        ↓
parser_lib / sim_concentrator / sim_meter / loghooks / minute_assert   ④ 领域层（契约不动）
```

**可测铁律（写成 `test_layering.py`，不靠人记）**：

1. `core/` 不得 import `capabilities/*`，也不得 import 任何领域库（`parser_lib`/`sim_concentrator`/`sim_meter`/`loghooks`/`minute_assert`/`apps.*`）
2. `capabilities/*` 之间互不 import；每文件只对接一个领域库
3. `libs/` 整体不得 import `apps/`（沿用 v2 铁律 1）
4. 块/事实/用例/方案**均为 JSON 数据**；新增块或用例不得需要改 Python

### 10.5 需求点（编号即验收锚点，取代 v2 §4.1/§4.3 对应条）

#### A. 内核与门禁

| 编号 | 需求 | 验收 |
|---|---|---|
| KE-1 | `core/models.py`：`BlockSpec`/`FactSpec`/`CaseSpec`/`SuiteSpec` + JSON schema | 非法声明（缺 `action`/`needs` 未注册/`blocks` 为空）加载期报 `file:reason`，不静默跳过 |
| KE-2 | `core/planner.py`：事实 DAG 求值 → 未满足则下游块标 `skipped` | 前置事实 `unknown` 时块**必须** `skipped`，结果里 `outcome != pass` |
| KE-3 | `core/executor.py`：顺序执行 + 聚合断言 + 落 `AssertionResult` | 单块 fail 不影响同 run 其余块记录；`stop_on_fail` 时下游标 skipped |
| KE-4 | **`test_layering.py` 三条铁律 AST 断言** | 铁律 1/2/3 任一破坏即测试红 |

#### B. 原子块（Block，铺齐后编排）

| 编号 | 需求 | 验收 |
|---|---|---|
| BL-1 | 台架类：`fix.simeter_session` / `fix.simcon_session` | 会话不在线 → 块 `skipped` + 原因，不崩 |
| BL-2 | 档案类：`archive.add` / `archive.del` / `archive.query` | `archive.add` 判据 = 00H-F1 确认 **且** 10H-F2 能回查到该地址 |
| BL-3 | 组网类：`nwk.route_restart` / `nwk.route_pause` / `nwk.active_registration` / `nwk.onnet_count` / `nwk.route_status` / `nwk.topology` / `nwk.node_call` | 每块独立可跑、独立判；`active_registration` 按 11H-F5 全参构帧（见 §10.7 事实 I） |
| BL-4 | 抄表类：`meter.addr_probe` / `meter.read_645` / `meter.brain_local` | `addr_probe` 判据 = 02H-F1 上行内嵌 645 的地址域 == 期望地址（见 §10.7 事实 E） |
| BL-5 | 分钟采集类：`mclt.task_config` / `mclt.archive_link` / `mclt.wait_stored` / `mclt.read_11e3` | 复用现有语义步；内层 698 断言走 `minute_assert` |
| BL-6 | 升级类：`upg.start` / `upg.segment` / `upg.end` / `upg.version_query` | 分片状态机：段确认 seg_id 递增、超时重发有界、结束帧确认 |
| BL-7 | 块自检入口 `case_run.py probe --all` | 无台架时全部 `unknown`（非 fail、非 pass），有台架时逐块给结论 |

#### C. 事实（Fact）

| 编号 | 需求 | 验收 |
|---|---|---|
| FT-1 | 8 个事实：`bench.simeter_online` / `bench.simcon_online` / `archive.contains(addr)` / `nwk.onnet_count_min(n)` / `nwk.route_running` / `sta.meter_addr_known(addr)` / `mclt.task_configured(task_no)` / `upg.version(module)` | 每个 fact 有独立单测（fake 探测源） |
| FT-2 | 事实探测**复用块的断言函数**，不另写一份判定逻辑 | 代码审查：`fact.archive.contains` 与 `block.archive.query` 共用同一判定函数 |
| FT-3 | 事实三态：`yes` / `no` / `unknown` | `unknown` 永不折算为 `yes` 或 `pass` |

#### D. 用例与方案

| 编号 | 需求 | 验收 |
|---|---|---|
| CS-1 | 用例 = 编排：`needs[]` + `blocks[]` + `asserts[]`；`steps[]` 仅用于 UI 展示（do/expect） | 用例 JSON 内**不得**出现帧 hex、不得出现参数计算 |
| CS-2 | 页面外 `autotest/` 22 例**全部迁入**仓库并改写为块编排 | `git ls-files` 可见 22 例；仓库外原目录保留只读副本 |
| CS-3 | `sta_main_mclt_list_query` 的 `shell_ro` 外部脚本调用**改为**走 module 控制台 API | `grep -rn 'shell_ro' libs/case_runner/` = 0 |
| CS-4 | 至少 3 条端到端用例：`upgrade_15f1`（升级链）、`nwk_join`（档案→组网）、`mclt_collect`（分钟采集） | 3 条在台架齐全时全 PASS；台架缺失时给出断点块与原因 |
| SU-1 | `SuiteSpec` + `data/case_suites/*.json` 持久化（沿用 v2 §3.4） | 创建/重命名/删除，文件可 diff |
| SU-2 | 既有 `chains.json` 导入为只读方案（v2 SU-5，**降级为可选**） | 若做：`legacy_<name>` 方案可列出 |

#### E. 入口与 UI

| 编号 | 需求 | 验收 |
|---|---|---|
| API-1 | `POST /api/cases/run`：`{suite_id}` 或 `{case_ids:[…]}` | 两种 body 均可用；`requires` 未满足返 409 + 明确原因 |
| API-2 | `GET /api/cases` / `GET/POST/DELETE /api/cases/suites` / `GET /api/cases/runs/{run_id}` | 端点全绿 + 单测 |
| API-3 | `GET /api/cases/blocks` / `GET /api/cases/facts`（块与事实目录，供 UI 展开） | 返回每块 `needs/provides/assert` 摘要 |
| UI-1 | 新页面「用例库」：列表 = **用例名 + 块清单**（可展开每块 do/expect） | 每行显示 title 与块数；展开见块详情 |
| UI-2 | 勾选 + 方案下拉 + 「存为方案」；**刷新后勾选随方案恢复** | F5 后勾选仍在（解决 v2 P-3） |
| UI-3 | 单例「▶ 立即运行」+ 方案「▶ 按方案运行」 | 两入口各自可用，job 面板沿用现有轮询 |
| UI-4 | **断点可视化**：失败用例高亮失败块，并列出该块未满足的事实与原因 | 一眼看出「断在哪个块」 |
| UI-5 | 主题令牌合规（沿用 0012 三层 token，禁硬编码色值） | 主题静态校验 0 issue |

#### F. 执行语义

| 编号 | 需求 | 验收 |
|---|---|---|
| RU-1 | 能力 `probe()` 对缺依赖返回 not-ready 而非崩溃 | 缺服务/缺串口时返回明确原因 |
| RU-2 | 结果落盘 `data/case_runs/<run_id>/result.json`，复用 L1/L2/L3 口径 | 每次运行有 `result.json`；证据可经 `run_id`+`seq` 回放 |
| RU-3 | **绝不假 PASS**（继承 v2 RU-7） | `unknown`/`skipped` 永不出现在 PASS 计数里 |
| RU-4 | 同一方案两次运行可对比差异 | 能回答「这次比上次多挂哪几条、断在哪个块」 |

### 10.6 明确不做的（本需求边界）

1. ❌ **不改** `libs/case_library`（279 条只读字典，知识资产）
2. ❌ **不改** `libs/test_automation` 的模型契约（只消费，不改造）
3. ❌ **不破坏** `/api/autotest/*` 与现有 autotest 页面
4. ❌ **不接侦听台**（用户明确：测试用例大部分不需要涉及侦听台）
5. ❌ **不接 CCO 模块日志事件**作为组网判据（本需求组网判据只用 1376.2 帧）
6. ❌ **不做真固件烧录/激活**：升级类块只做 15H-F1 分片编排 + 帧级回验；
   真实烧录归 `wb-flash` / `hs-dw31-upg` 领域
7. ❌ **不实现** `network_assert.evaluate()`（不接侦听台/日志则不需要）
8. ❌ 不做用例录制/回放、不做跨机分布式调度
9. ⚠️ 97 条 gw_cass 声明式用例的 `declarative` 断言求值**降为可选**（v2 技术核心不再属本需求主线）

### 10.7 已知技术事实（v3 增补，均为本会话实测）

#### 事实 D：原子块构帧能力已实测

下行无参即可构帧的 `(AFN, FN)` 共 **30 个**（`encode_app_data` 枚举实测）：
`00-F1`、`01-F1/F2/F3`、`03-F1/F2/F4/F5/F16/F100`、`04-F2`、`05-F101`、
`10-F1/F2/F4/F5/F6/F21/F31/F100/F101/F104/F112/F230`、`11-F6/F101`、`12-F1/F2/F3`、`14-F2`。

带参数构帧实测通过（本次逐条实跑，hex 为实测产物）：

| 块 | 实测 hex |
|---|---|
| `archive.add` 11H-F1 | `6817004300000000000111010001010000000000025A16` |
| `archive.del` 11H-F2 | `68160043000000000001110200010100000000005916` |
| `nwk.route_restart` 12H-F1 | `680F00430000000000011201005716` |
| `nwk.route_pause` 12H-F2 | `680F00430000000000011202005816` |
| `nwk.onnet_count` 10H-F1 | `680F00430000000000011001005516` |
| `archive.query` 10H-F2 | `6812004300000000000110020000000A6016` |
| `nwk.route_status` 10H-F4 | `680F00430000000000011008005C16` |
| `nwk.topology` 10H-F21 | `681200430000000000011010020000006616` |
| `nwk.node_call` 04H-F2 | `680F00430000000000010402004A16` |
| `upg.start/segment` 15H-F1 | `681C00430000000000011501000300000200000000010200AABBC716` |

#### 事实 E：02H-F1 内嵌 645 递归解析可用 —— 「STA 拿到表地址」的帧级判据成立

实测构上行 02H-F1 并解析：

```
68210083000000000001020100021068000000000001681104333433...1516
→ decode ok
→ nested[0].structure = "645"
→ nested[0].fields: 地址域 = 000000000001  ← 期望表地址
```

→ **`sta.meter_addr_known(addr)` 事实与 `meter.addr_probe` 块可落地，无需侦听台、无需模块日志。**

#### 事实 F：三个场景模板引用不存在的 task 文件（组网编排断链）

`os.path.exists` 实测：

| 场景 | 引用 task | 存在 |
|---|---|---|
| `join_anhui.json` | `tasks/join_anhui.json` | ❌ |
| `search_meter.json` | `tasks/search_meter.json` | ❌ |
| `open_close.json` | `tasks/open_close.json` | ❌ |
| `minute_collect.json` | `tasks/anhui_minute_collect.json` | ✅ |

→ 组网编排不能依赖现有 scenarios，须由块层重建（BL-3）。

#### 事实 G：`network_assert.evaluate()` 未实现

`libs/network_assert/core.py:52` 的 `evaluate()` 直接 `raise NotImplementedError`。
因本需求不接侦听台/日志事件，**该缺口不影响本需求**，明确列为非目标（§10.6 第 7 条）。

#### 事实 H：改前基线有 1 条既有红（与 0041 无关）

```
pytest libs/test_automation libs/case_library libs/gw_cass_migrate libs/parser_lib libs/sim_meter
→ 550 passed, 1 failed, 66 skipped
FAILED test_10376_full_coverage.py::test_10f104_up_unsupported
  assert encode_app_data(0x10, 104, {}, direction="down") == b""   # 实际 b'\x00\x00\x00'
```

原因是提交 `846a7c1` 把 10H-F104 移出「无数据单元白名单」并入 10-F21 家族（起始序号 2B + 数量 1B），
但 `test_10376_full_coverage.py` 未同步更新（HEAD 上已红）。

→ **本需求不动它**；在 `TODO.md` 记为已知红，P0 门禁以此为准（除该条外须全绿）。

#### 事实 I：11H-F5 构帧必填四参

`encode_app_data(0x11, 5, {})` → `ValueError: 缺少参数 duration`。
实际需 `time6` 六字节时间 + `duration`(2B LE) + `retry`(1B) + `slices`(1B)。
→ `nwk.active_registration` 块须把这四个参数显式化为块参数，不设隐式默认。

#### 事实 J：15H-F1 可构帧但无分片状态机

单帧构帧实测通过（事实 D 末行）。仓库内**无**任何 15H-F1 分片驱动代码：
`recipes/` 只有 `add_archive` / `clear_archive`；`scenarios/` 无升级编排；UI 无升级入口。
→ 分片状态机（起始→中间×N 等段确认→结束→版本回读）是 BL-6 的**新增实现**。

#### 事实 K：22 例依赖分布

逐文件扫描 `ctx.verify` / `ctx.simeter()` / `shell_ro`：

| 依赖 | 例数 | 说明 |
|---|---|---|
| simcon 在线 | 19 | 走 `/api/simcon/verify` |
| simeter 在线 | 3 | 其中 2 例同时依赖 simcon |
| `shell_ro` 外部脚本 | 1 | `sta_main_mclt_list_query`（需按 CS-3 改写） |
| 完全离线 | 1 | `simeter_handle_hex_645` |

`supported` 分布：`yes` 20 / `partial` 2。

### 10.8 验收标准（DoD，取代 v2 §6）

1. `pytest libs/case_runner apps/workbench/test_cases_api.py -q` 全绿。
2. `test_layering.py` 三条铁律全绿；`grep -rn '^from apps' libs/` 仍为 **0**。
3. 全部 23 个原子块 + 8 个事实有独立单测；`case_run.py probe --all` 可运行且无台架时全 `unknown`。
4. 22 个用例已在 `git ls-files` 中；`grep -rn 'shell_ro' libs/case_runner/` = 0。
5. 3 条端到端用例（升级 / 组网 / 分钟采集）在台架齐全时 PASS。
6. **断点可定位**：任一前置事实不满足时，结果明确列出「失败的块 + 未满足的事实 + 原因」，且该块不入 PASS 计数。
7. UI 可：勾选 ≥2 用例 → 存为方案 → 刷新恢复 → 按方案运行 → 见结果与断点。
8. `/api/autotest/*` 与 autotest 页面功能不回归。
9. 除事实 H 记录的既有红外，改前基线相关库测试无新增红。

### 10.9 风险（v3）

| 风险 | 应对 |
|---|---|
| 块粒度切太细 → 块数膨胀、编排冗长 | 块以「一次可独立判定的交互」为界；同一 AFN/FN 的查询类合并为一个参数化块 |
| 事实探测开销大（每次都发帧查询） | 同 run 内事实结果缓存；显式 `refresh_facts` 才重探 |
| 台架不在线导致大量 `unknown`，看起来「什么都没测」 | `probe --all` 明确输出台架前置表；UI 置灰并给原因 |
| 15H-F1 分片状态机时序复杂（等段确认/超时重发） | 先做「无重发的最小闭环」，重发策略作为块参数，超时有界 |
| 迁 22 例时改写工作量大 | 按依赖分组批量改写（19 例 simcon 组结构高度相似），先 1 例做样板再批量 |

### 10.10 台架端口事实（2026-11-23 用户口述 + 实测核对）

#### 事实 L：用户口述端口与仓库配置存在一处不一致

| 口 | 用户口述 | 仓库配置（`config/serial_ports.json`） | 一致 |
|---|---|---|---|
| 模拟电表 simeter | COM20 | `simeter` → COM20 | ✅ |
| STA | COM18 | `sta-main` → COM18 | ✅ |
| CCO | COM12 | `cco-main` → COM12 | ✅ |
| **集中器 simcon** | **COM4** | `ecu-biz` → **COM28**（`usage: simcon`） | ❌ |

实测：全仓库当前配置**无任何 COM4**（仅 `serial_ports.json.usbipd-backup-20261022.json` 历史备份里有 `simcon → COM4`）。
`/api/serial-bridge/status` 实时返回也确认 simcon 走 `mapping_id=ecu-biz` / `windows_com=COM28`。

→ **待用户确认**：是「物理接线已改到 COM4，配置待更新」，还是「口述沿用旧记忆，实际仍为 COM28」。
   确认前，块层一律用 `mapping_id` 寻址（不硬编码 COM 号），使该差异不影响代码。

#### 事实 M：当前串口桥所有口 `usable=false`（开工硬前置未满足）

`GET /api/serial-bridge/status` 实测，6 个映射（listener/sta-main/cco-main/simeter/ecu-biz/ecu-mgmt）
**全部 `usable=false`**。按该端点自身口径，`usable` = enabled 且 workbench_ready 且本口 errors 为空。

→ 影响：P2 块的**台架联调**被阻塞；但 P1 内核与 P2 块声明/单测（fake 注入）**不在线也可推进**。
   联调前需先让 Windows 侧串口桥与 WSL PTY 就绪。

### 10.11 第一批交付范围（2026-11-23 用户追加决策）

用户决策：**用例先不用全做**；第一步只做**抄表测试用例**链路，前置依次为 **复位 → 档案 → 组网**；
第一次组网需要**复位 STA**，之后按需复位。

| 项 | 第一批纳入 | 说明 |
|---|---|---|
| 台架块 | ✅ `fix.simeter_session` / `fix.simcon_session` | |
| **复位块（新增）** | ✅ `reset.sta`（STA 复位/重启） | 第一次组网前必做；之后按需。具体手段待调研（见 P2-0） |
| 档案块 | ✅ `archive.add` / `archive.del` / `archive.query` | add 双判据不变 |
| 组网块 | ✅ `nwk.active_registration` / `nwk.onnet_count` / `nwk.route_status` / `nwk.route_restart` | 不纳入 `topology`/`node_call`/`route_pause`（第二批） |
| 抄表块 | ✅ `meter.addr_probe` / `meter.read_645` / `meter.brain_local` | 本批核心 |
| 分钟采集块 | ⬜ 第二批 | |
| 升级块 | ⬜ 第二批 | |
| 事实 | ✅ `bench.*`×2 / `archive.contains` / `onnet_count_min` / `route_running` / `sta.meter_addr_known` | 6 个；另 2 个（mclt/upg）第二批 |
| UI | ⬜ 第三批 | 先 CLI + API |

→ 本批块数 **12**（含新增 `reset.sta`），事实 **6**；DoD 相应收窄，但 §10.8 的
「断点可定位」「绝不假 PASS」两条**不变**。
