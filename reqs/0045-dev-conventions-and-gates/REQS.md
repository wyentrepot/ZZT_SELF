# REQS.md — 需求基线（需求 ID：0045，标题：开发规约与可执行门禁）

> 本文件遵循「需求只追加、不覆盖」ADR 模式。
> 立项日期：2026-11-24；基线版本：**v1**。
> 依据：2026-11-24 只读勘察报告 `/tmp/survey-conventions-report.md`（规约资产与可执行门禁盘点）+ `docs/frontend-architecture-review.md`（唯一前端"规范"文本，2026-08-30）+ 与用户的 5 条拍板（见 §11）。
> **本需求只立规约与门禁，不改任何业务功能**；应用规约去重构用例库与场景测试是 **REQS-0044 v2** 的事。
> 关系：**0045 先交付**（至少 P1 骨架），0044 的新代码按本册写；两需求共同验收「新代码 0 豁免」。

---

## 1. 背景

### 1.1 用户原话（2026-11-24）

> 「目前我关心的是 ui 开发与代码开发没有规约，其次关心这部分开发内容耦合度过高，设计不合理」

两个关切在实测上**是同一件事的两面**：耦合之所以能长出来，是因为没有任何东西阻止它长。

### 1.2 实测事实（全部来自只读勘察，未运行 pytest）

| # | 事实 | 证据 |
|---|---|---|
| C-1 | 全仓 md 搜"命名规范/命名规约/代码规范/开发规约"= **0 命中**；文件命名只有隐含规律（`*_api.py` 8 个 / `test_*.py` 32 个） | `grep -rn "命名规范\|命名规约\|开发规约\|代码规范" docs/ *.md` |
| C-2 | 唯一前端"规范"是 2026-08-30 的评审报告，**通篇 0 次提及 cases / autotest 两页**；其中 14 条可核对条目**仍有 9 条在违反**（主题引导 12 份副本、`cancelRun` 双轮询未修、`esc()` 5→10 处、死令牌 95→103） | `docs/frontend-architecture-review.md:16,115,146,165,184,186,231,236` 逐条实测 |
| C-3 | 后端接口契约自述"人工同步"；`/api/cases/*` 10 条路由在 `docs/` 里 **0 处记载** | `docs/api-contract.md:3,317-320`；`grep -rn "/api/cases" docs/` 只命中 `_archive/` |
| C-4 | 库存校验脚本覆盖 **8/227 路由 ≈ 3.5%**，且**它自己当前是失败的**（v2 实际 10 条，EXIT=1） | `tools/scripts/verify_api_inventory.py:34-59,176-179`；实跑 `v2 route count is 10, expected 8` |
| C-5 | 无 CI（无 `.github/`）、无 pre-commit、无 lint 配置（无 `pyproject.toml`/`eslint.config.*`/`.pre-commit-config.yaml`/`Makefile`/`.editorconfig`）；git hooks 全部是 **LFS 托管**（改 hooks 会被覆盖） | `ls .git/hooks/` 四个 hook 内容均为 `git lfs <stage>` |
| C-6 | `docs/03-骨架设计.md:28` 的 `apps → libs` 只被 `libs/case_runner/test_layering.py` 守了 libs 反向；**apps 侧一条门禁都没有** | `test_layering.py:32-43` 的 `CORE_FORBIDDEN` 不含 apps 内部规则 |
| C-7 | 状态字符串 **5 套**（块 4 态 / 结论 4 态 / 事实 3 态 / 断言 3 态 / 报告 4 标签 + 6 档退出码）；且存在**两张故意不一致**的映射表 | `case_runner/core/models.py:44,68,88,97`；`executor.py:130-135` vs `142-147`（`skipped` 一处→`blocked`、一处→`inconclusive`） |
| C-8 | 声明里的字段**无人消费**：`cleanup` 0/16 声明同时 0 消费者；`assert[]` 18 条 13 种 type **11 种无求值器**；用例 `asserts` 7/7 全空且 `assert_evaluator` 全仓无赋值 | `models.py:245`、`executor.py:1025`、`data/blocks/*.json`、`executor.py:1194-1234` |
| C-9 | 同一常量三处双写：`0.98`（`nwk_topology.json:16` + `:23` + `nwk.py:217`）、`min:1`（`nwk_archive_count.json:12,18` + `nwk.py:228`）、4 个启动指纹串（`reset_sta.json:24-29` 与 `reset.py:55-63` 逐字重复） | 见行号 |
| C-10 | 前端重复实现：**4 个同名 `api()`**（语义各异）、**10 个转义函数**（3 种命名）、**5 个 toast**、**4 种弹窗机制**、**5 族状态标签**、**5 个 `renderTable`**；`innerHTML` 137 处；`fetch(` 16 处；`/api/` 字面量 145 处 | `survey-conventions-report.md` D 节 + 前端勘察 |
| C-11 | 上帝文件：`cases.js` 1782 行 / 99 个顶层函数 / 9 类职责；`cases.html` 内联 CSS 190 行；全 9 个 >500 行、5 个 >1000 行 | `wc -l`；`pages/cases/` |
| C-12 | 令牌体系被架空：`tokens-v2.css` 209 个定义 / **103 个零引用**；`autotest.html` 有 **4 个未定义令牌** + 16 处 hex 兜底；cases 的 `inconclusive` 用琥珀色，违背令牌层"必须独立紫相"的铁律 | `tokens-v2.css:188-204`、`autotest.html:50,51,65,70-75`、`cases.html:152` |
| C-13 | 页面注册只在一个地方（`app.js:21-33` 的 `PAGES`），但 `autotest` 页**不在其中**（僵尸页，只能直连 URL），而测试反而锁定"app.js 不许出现 autotest" | `app.js:21-33`；`test_cases_page.py:61-69` |
| C-14 | 主题引导脚本**12 份手工副本**（每份 30+ 行），而 `app.js:587-589` 注释自称"只改 CSS 一处" | 12 个 HTML 各含 `wb-theme-change` 监听 |
| C-15 | 测试在**锁定实现文本**：`test_cases_page.py` 用 `BEGIN/END` 注释标记截取 `cases.js` 源码做断言 → 结构调整即破坏测试 | `test_cases_page.py:280-306` |

### 1.3 诊断

> **不是没有规范，是规范没有被强制执行**（`docs/frontend-architecture-review.md:35,284` 的结论，本次勘察复核为真）。
> 且现有规范**本身也不覆盖新增的代码**：唯一前端规范成文于 2026-08-30，而 `cases`/`autotest`/`orchestration` 都在其后出现。

本需求要做的，是**把"人记的规范"换成"机器管的门禁"**，并让每一句规约都能被一条命令判定。

---

## 2. 目标与非目标

### 2.1 目标

1. **一册一档**：UI（前端）与代码（后端）各一册规约，每条规约 = 唯一 ID + 一句可判定的话 + 一个检查器 + 一条修复指引。
2. **可执行**：每条可机检规约都有检查器；全仓一条命令跑完（`python tools/scripts/lint/run.py --strict`）。
3. **门禁自己也被守**：每条规约配"喂违反样本必须变红、喂合规样本必须变绿"的自检，防止规则空转。
4. **存量可收敛**：允许存量违规进基线冻结，但**基线只减不增**；新代码 0 豁免。
5. **文档同源**：接口清单、块文档、错误码表、页面清单、单例清单全部由代码生成；手改即判漂移。
6. **不引入构建链**：零 node、零打包、零新第三方依赖（唯一例外见 §3.6）。

### 2.2 非目标

- ❌ 不做前端构建链（Vite / esbuild / webpack）；不引入 npm 依赖
- ❌ 不改任何业务功能与接口语义（那是 0044 v2）
- ❌ 不追求"全部规约都能机检"——人审类规约明确标注，不假装可检（见 §3.3 三档）
- ❌ 不做 CI 服务（无远端 CI 条件）；靠本地命令 + pytest 包一层
- ❌ 不改 git hooks（LFS 托管，改了会被覆盖，见 C-5）

---

## 3. 规约的载体与机制（本需求的主体）

### 3.1 一条规约的构成

```jsonc
{
  "id": "FE-05",                    // 唯一 ID：册别-序号（BE/FE/UX/DS/DOC）
  "title": "颜色只能取令牌",          // 一句话规则
  "level": "block",                 // block | baseline | warn
  "check": "颜色只能 var(--…)：禁止 hex/rgb/rgba/命名色；且 var() 必须能在 tokens-v2.css 解析到",
  "why": "令牌被架空 → 主题切换失效（实测 4 个未定义令牌 + 16 处 hex 兜底）",
  "fix": "改成 var(--color-*)；缺令牌先补 tokens-v2.css",
  "checker": "rules/fe_05_token_only.py",
  "fixtures": {"bad": ["fixtures/FE-05/bad/*.html"], "good": ["fixtures/FE-05/good/*.html"]},
  "scope": ["apps/workbench/static/**/*.{html,js,css}"],
  "exceptions": ["apps/workbench/static/tokens-v2.css"]
}
```

### 3.2 检查器统一契约（复用已有的 `check_assets` 模式）

```python
# tools/scripts/lint/base.py
class Problem(NamedTuple):
    rule: str        # "FE-05"
    level: str       # block | baseline | warn
    path: str        # 仓库相对路径
    line: int | None
    message: str     # 违反了哪条、怎么改
    fix_hint: str

class Rule:
    id: str; title: str; level: str; scope: list[str]
    def check(self, ctx: "Ctx") -> list[Problem]: ...
```

- 入口：`python tools/scripts/lint/run.py [--only ID[,ID...]] [--strict] [--json] [--baseline PATH] [--write-baseline]`
- 退出码：`0` 全过；`1` 有 block 级或基线上升（`--strict` 时）；`2` 只有 warn 级
- 输出：人读一行一条 `FE-05 apps/…/autotest.html:50 unregistered token --color-status-success-fg`；`--json` 给机器

### 3.3 三档可检性（诚实边界）

| 档 | 含义 | 门禁行为 |
|---|---|---|
| **M 可机检** | 有检查器，能给出 `文件:行号` | 进 `run.py`，按 level 阻断 |
| **S 半自动** | 检查器只能**列出清单**（如"本页缺空态/错误态"），需人确认 | 进 `run.py` 但只出 warn + 一份 `lint-report.md` |
| **R 人审** | 无法机器判定（命名美感、注释质量、交互合理性） | **不进** `run.py`，只进交付前评审清单 |

> 规矩：**S/R 档不得冒充 M 档**。每条规约在表里必须标档位；标 M 就必须有 checker 与 fixtures。

### 3.4 基线冻结（存量收敛机制）

```json
// tools/scripts/lint/baseline.json
{
  "_note": "存量豁免。只允许减少；增加必须人工改并说明理由。",
  "FE-03": {"apps/workbench/static/pages/cases/cases.html": 190},
  "FE-11": {"apps/workbench/static/pages/cases/cases.js": 1782}
}
```

规则：

1. 实际计数 **> 基线** → `block`（新增违规不许进）；
2. 实际计数 **< 基线** → 只提示"基线可下调"，**不自动改**；
3. `--write-baseline` 只允许人工执行，且**只允许下调**（脚本自己拒绝上升）；
4. 基线变更必须单独提交，diff 里出现"数值变大"即判违规（`BE-GATE` 规则检查 `git diff` 的 baseline 文件）。

### 3.5 门禁自己也被守（反例自检）

```python
# tools/scripts/lint/test_rules.py
@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_rule_catches_violation(rule_id):
    """喂 fixtures/<id>/bad 样本 → 必须报出至少 1 条 Problem"""

@pytest.mark.parametrize("rule_id", ALL_RULE_IDS)
def test_rule_passes_good_sample(rule_id):
    """喂 fixtures/<id>/good 样本 → 必须 0 条 Problem（防误报把团队逼疯）"""
```

- 新增一条规则**必须同时**提交 `bad/` 与 `good/` 样本，否则该规则不计入交付。
- 全仓零新增违规由 `test_no_new_violations.py` 守（`run.py --strict` 的 pytest 包装）。

### 3.6 触发点（无 CI 条件下的最大化）

| 触发点 | 做法 | 强度 |
|---|---|---|
| 命令 | `python tools/scripts/lint/run.py --strict` | 人跑，写进 README 的"验证"节与交付清单 |
| pytest | `pytest tools/scripts/lint -q`（含反例自检 + 零新增违规） | **主门禁**：全量回归时必然执行 |
| 交付清单 | `docs/04-任务安排.md` 的验收出口加一条"lint 全绿" | 流程约束 |
| git hooks | **不做**（LFS 托管，见 C-5） | — |

> 明确写清：**这里没有 CI，门禁靠"pytest 里有它"生效。** 任何把 lint 从 pytest 里摘出去的做法都等于取消门禁。

### 3.7 唯一的新增依赖例外

不新增任何第三方库。JS/HTML/CSS 的静态分析用 **Python 正则 + 括号计数**自研，**不强求 AST 精度**；规则的精度边界由 fixtures 固定（规则只对其 fixture 覆盖的语法形态负责）。这是**主动接受的取舍**，写在 §2.2。

### 3.8 目录布局

```
tools/scripts/lint/
├── run.py                  # 统一入口（problems[] + --strict + --json + baseline）
├── base.py                 # Rule / Problem / Ctx
├── baseline.json           # 存量冻结（只减不增）
├── rules/
│   ├── be_01_dependency_direction.py
│   ├── ...
│   └── fe_15_no_alert.py
├── fixtures/<RULE-ID>/{bad,good}/...
├── test_rules.py           # 反例自检（每条规则）
└── test_no_new_violations.py  # 全仓门禁
```

---

## 4. 代码册（后端，BE）

> 作用域默认 `apps/**/*.py`、`libs/**/*.py`、`tools/**/*.py`（测试文件按规则说明）。

| ID | 规则 | 档 | 等级 | 检查方式 | 现存违规（实测） |
|---|---|---|---|---|---|
| **BE-01** | 依赖方向：`libs/**` 不得 import `apps/**`（已有）；新增 `libs/<pkg>` 之间**无环** | M | block | AST import 图 + 拓扑检测 | 0（反向）；包间环未查 |
| **BE-02** | 路由薄层：`apps/workbench/*_api.py` ≤ **300 行**、只 import `*_service`/`libs`、**零领域判定**（不得出现 verdict 赋值、不得 import 执行器） | M | block | 行数 + AST import + 关键词 | `cases_api.py` 2447 行，**HTTP 适配仅 117 行**，业务+编排 ≈1800 行 |
| **BE-03** | 文件体量：`libs/**`、`apps/**` 源文件 ≤ **600 行**；`tools/**` ≤ 800；测试 ≤ 1200 | M | baseline | 行数 vs 基线 | 5 个超千行：`cli.py` 3901 / `common.py` 2626 / `cases_api.py` 2447 / `ai_operations.py` 2401 / `meter.py` 2076 |
| **BE-04** | 函数体量 ≤ **80 行**（测试除外） | M | baseline | AST | 未全量统计，`CasesService._execute` 113 行等 |
| **BE-05** | 状态常量**单一来源**：`pass/fail/blocked/inconclusive`、块态、事实态、断言态只允许定义在**一处** `states.py`；其它文件出现这些字面量即违规（基线按文件放行） | M | block | 字面量扫描 vs 定义点 | **5 套**状态词表（C-7） |
| **BE-06** | 映射函数唯一：块态→结论、块态→断言态各只允许一处实现 | M | block | 表名/函数名唯一性 | **2 张表故意不同**（`executor.py:130-135` vs `:142-147`） |
| **BE-07** | **声明字段必须有消费者**：声明模型每个字段名，必须在非声明代码中出现 ≥1 次读取；否则判死字段 | M | block | 字段名 → 消费点 grep | `cleanup`（0/16 声明、0 消费）+ `case/di_echo/note_value/note_mine` 4 键无读取（C-8） |
| **BE-08** | **声明与代码不得双写同一常量**：同一语义数值在声明 JSON 与 Python 常量里同时出现即违规 | M | block | 声明数值 ↔ Python 常量交叉 | `0.98`×3 处、`min:1`×3 处、启动指纹 4 串×2 处（C-9） |
| **BE-09** | 错误体统一 `{code,message,details,request_id}`；**错误码必须登记**在生成的 `docs/error-codes.md`，未登记的 code 即违规 | M | block | 路由错误构造点 + 码表 diff | **3 种 body 形状**；`cases_api.py` 自造 **17 个码**无注册表；`autotest_api.py` 用 409 表"框架不可用"违反契约 4 处 |
| **BE-10** | 路由必须出现在**生成**的 `docs/api-endpoints.md`（手写不算） | M | block | 生成物 diff | `/api/cases` 10 条在 docs **0 记载**（C-3） |
| **BE-11** | 禁止硬编码 host/port/绝对路径（配置模块白名单） | M | block | 字面量扫描 | `cli.py:201-203` 8790、`app.py:33` PORT |
| **BE-12** | 每个 `libs/*` 包与 `apps/workbench/*.py` 必须有对应测试文件；**零测试模块必须清零或进基线** | M | baseline | 文件映射 | `ai_api.py` 538、`serial_tags_api.py` 69、`desktop.py` 110、`case_library/generate.py` 532 等 ~12 个 |
| **BE-13** | 全局可变单例必须登记在生成的 `docs/singletons.md`（模块级 dict/list） | M | baseline | AST 模块级赋值 | 8 处隐式全局（`BLOCK_REGISTRY`、`_executor()` 的函数属性、`global app`、`_sessions`、`_cache` ×3、`_consumer`） |
| **BE-14** | 执行入口必须有超时与取消：新增 `execute*/run*` 公共函数须带 `timeout` 形参 | M | block | AST 形参 | 执行器**无超时**，超时全在 CLI 层（R-5 僵尸线程） |
| **BE-15** | 执行路径零交互 / 零 AI：不得出现 `input(`、`sys.stdin`、AI 客户端调用 | M | block | 字面量扫描 | 0（0042 D4-2/3 已实装，本条只是**收编成门禁**） |

---

## 5. UI 册（前端，FE）

> 作用域默认 `apps/workbench/static/**`（含 `pages/`）。前提：0044 v2 引入**原生 ES Module**（`<script type="module">`，零构建零依赖）与 `static/shared/`。

| ID | 规则 | 档 | 等级 | 检查方式 | 现存违规（实测） |
|---|---|---|---|---|---|
| **FE-01** | 页面结构固定：`pages/<id>/{<id>.html,<id>.js,<id>.css?}`，不得有其它命名 | M | block | 路径正则 | `pages/listener/index.html`（+独立 app 双入口，进基线） |
| **FE-02** | **页面注册双向一致**：`PAGES` 每条必须有对应目录；每个目录必须在 `PAGES` 有且仅有一条 | M | block | 目录 ↔ `app.js:21-33` 对账 | `autotest` 有目录无注册（僵尸页，C-13） |
| **FE-03** | 新页禁止内联 `<style>`（存量进基线，只减不增） | M | baseline | HTML 扫描 | `cases` 190 / `scenario` 100 / `autotest` 53 行 |
| **FE-04** | 禁止 `style="…"` 属性（**HTML 与 JS 模板串都算**） | M | baseline | 扫描 | `scenario.js` **22 处**（样式藏在 JS 串里，审计假象）+ cases 3 + autotest 3 + 各页 HTML |
| **FE-05** | 颜色只能 `var(--…)`：禁 hex / rgb / rgba / 命名色；且每个 `var()` 必须能在 `tokens-v2.css` 解析到 | M | block | 扫描 + 令牌表对账 | `autotest.html` **4 个未定义令牌** + 16 hex 兜底 + 4 处裸 `#fff` |
| **FE-06** | 令牌零冗余：`tokens-v2.css` 零引用令牌 = 0 | M | baseline | 定义 ↔ 引用对账 | **103 / 209 零引用**（含 `--color-status-inconclusive-bg/-border`） |
| **FE-07** | **单一转义实现**：页面不得自定义 `esc`/`escapeHtml`，必须 import `shared/dom.js` | M | block | 函数名扫描 + import 检查 | **10 个**（3 种命名、2 种覆盖度；cases 唯一转义 `'`） |
| **FE-08** | **单一请求封装**：禁止裸 `fetch(`；禁止 `/api/` 字面量（只允许 `shared/api.js` 一处） | M | block | 扫描 | **4 个同名 `api()`** + `fetch(` 16 处 + `/api/` 145 处 / 12 文件 |
| **FE-09** | **单一四态映射**：`pass/fail/blocked/inconclusive` 字面量只允许在 `shared/verdict.js`；且 `inconclusive` **必须**用 `--color-status-inconclusive`（紫），不得用 warn 琥珀 | M | block | 字面量 + 令牌对账 | **6 处**映射；`cases.html:152` 用琥珀违背铁律（C-12） |
| **FE-10** | **单一 UI 组件族**：toast / modal / table / badge 只允许 `shared/ui/*`，页面不得自造 | M | block | 函数名 + 类名扫描 | **5 toast / 4 modal / 5 renderTable / 5 族标签**（C-10） |
| **FE-11** | 体量：js ≤ **400** 行、css ≤ **500**、html ≤ **300**、单函数 ≤ **60** 行 | M | baseline | 行数 + 括号计数 | `cases.js` 1782 / `listener/app.js` 2627 / `simcon.js` 2158；>500 行 9 个 |
| **FE-12** | 模块边界：页面 JS 必须是 ESM（`import`/`export`）或 IIFE，**禁止顶层隐式挂 window** | M | block | 顶层声明扫描 | `pages/listener/app.js` 79 个函数泄漏到 `window` |
| **FE-13** | 主题引导唯一份：`shared/theme-boot.js`；HTML 只允许 `<script src>` 引用 | M | block | HTML 内联脚本扫描 | **12 份**手工副本（C-14） |
| **FE-14** | 状态容器唯一：每页只允许**一个**状态对象 + **一个** `render()` 入口；`render*` 函数内**不得写状态** | M | baseline | 括号计数 + 赋值扫描 | `cases.js:629-638` `renderRail` 临时改并回滚 `S.filters` |
| **FE-15** | 禁止 `alert(` / `confirm(`（用 `shared/ui/modal`） | M | block | 扫描 | **15 处** |

---

## 6. UX 册（交互与可用性，UX）

| ID | 规则 | 档 | 等级 | 检查方式 | 现状 |
|---|---|---|---|---|---|
| **UX-01** | 每个数据区必须有三态：加载 / 空 / 错误 | S | warn | 检查器列出缺哪个态，出 `lint-report.md` | cases 有部分；scenario/autotest 无统一 |
| **UX-02** | 破坏性操作必须二次确认（删除/覆盖/复位） | M | block | 调用点邻域必须出现 modal/confirm 组件 | `cases.js:1663` 用原生 `confirm`；`autotest` 无 |
| **UX-03** | 可交互元素键盘可达 + aria：纯图标按钮必须有 `aria-label`；禁止 `onclick=` 内联 | M | block | 扫描 | 内联 `onclick=` 实测 **0**（保持）；aria 覆盖未查 |
| **UX-04** | 禁用态必须给原因（置灰 + 一句人话） | S | warn | 检查器列置灰点，缺原因进报告 | 运行按钮由台架就绪横幅给原因（0042 已做） |
| **UX-05** | 人读文本**不得出现内部块 id**（`xxx.yyy` 形态），只允许在"开发者详情"折叠区与 `--json` | M | block | 文本扫描 + 白名单标记 | 0042 D11-2 已定规矩，**无门禁** |

---

## 7. 声明与数据册（DS）

| ID | 规则 | 档 | 等级 | 检查方式 | 现状 |
|---|---|---|---|---|---|
| **DS-01** | 每个声明类型必须有 schema（`*.schema.json` 或 pydantic 模型），且 `check` 覆盖它 | M | block | 声明文件 ↔ schema 对账 | 有 pydantic 模型 + `loader` 校验；**无 `*.schema.json` 文件** |
| **DS-02** | 台架默认值只允许出现在**配置**（`config/`），不得写进块声明或 Procedure | M | block | 声明里出现地址/端口/协议默认值即违规 | `case_defaults.json` 在 libs、`DEFAULT_ADDR="000000000001"` 在 `blocks/meter.py:169`（**代码默认与配置默认不同值**） |
| **DS-03** | 期望值必须标适用范围（台架/协议/省份） | S | warn | 检查器列出无 `applies_to` 的期望值 | `1234.56`/`220.1` 写死在 `meter_daily.json:23-28` |

---

## 8. 文档册（DOC）

| ID | 规则 | 档 | 等级 | 检查方式 | 现状 |
|---|---|---|---|---|---|
| **DOC-01** | 五类文档必须**由代码生成**、禁止手写：`docs/blocks/*.md`、`docs/api-endpoints.md`、`docs/error-codes.md`、`docs/pages/*.md`、`docs/singletons.md` | M | block | 生成 → 比对 → 不一致即漂移 | 块文档 5 份**全手写**（REQS-0044 AC-7 要求生成，未实现） |
| **DOC-02** | 文档漂移校验进 `check`：`case_run check` / `lint run` 都能查 | M | block | 命令退出码 | 无 |
| **DOC-03** | 过时文档必须标"历史快照 + 日期"，否则视为当前事实源 | R | — | 人审清单 | `docs/api-endpoints-inventory.md:1` 自标过期但仍被引用 |

---

## 9. 实施顺序（不允许跳）

| 阶段 | 内容 | 出口 |
|---|---|---|
| **G0** | 机制骨架：`base.py`/`run.py`/baseline/两个 pytest 包装 + **第一条规则 BE-01 + FE-02**（各带 fixtures） | 命令与 pytest 都能跑；两条规则的反例自检红/绿 |
| **G1** | **代码册**（BE-01..BE-15）全量落地 + 基线冻结 | `run.py --strict` 只剩基线内违规；`pytest tools/scripts/lint -q` 全绿 |
| **G2** | **UI 册**（FE-01..FE-15）+ 共享模块骨架 `static/shared/`（api/dom/verdict/theme-boot/ui） | 同上；`shared/` 五件被 0044 的编排台页使用 |
| **G3** | **UX + DS + DOC 册** + 五类生成式文档 | `DOC-01` 五类生成全绿 |
| **G4** | 收口：README/`docs/04` 写入验证命令；`docs/03` 补 `case_runner` 与 `data/case_runs`（现缺）；`verify_api_inventory.py` 修到绿或降级为 DOC-01 的一部分 | 全量回归绿 + 交付清单更新 |

> **G1 的基线冻结是硬门槛**：基线一旦冻结，0044 v2 的重构**每删一行上帝文件、每收一个重复实现，基线都要同步下调**——这就是"耦合只减不增"的执行机制。

---

## 10. 验收标准（可检查）

| # | 验收项 | 判定方式 |
|---|---|---|
| AC-1 | 全仓一条命令跑完全部规约 | `python tools/scripts/lint/run.py --strict` 退出码 0（基线内） |
| AC-2 | 门禁自己也被守 | `pytest tools/scripts/lint -q` 全绿；**每条规则**都有 bad/good fixture 且两向自检成立 |
| AC-3 | 基线只减不增 | 对 `baseline.json` 做一次"故意调大"的改动 → 门禁必须红 |
| AC-4 | 存量可收敛 | 冻结后 `baseline.json` 的总违规数在 0044 v2 每个阶段结束时**单向下降** |
| AC-5 | 新代码 0 豁免 | 新增一个违反 FE-07（自造 `esc`）的页面 → 门禁立刻红 |
| AC-6 | 文档同源 | 手改一份 `docs/blocks/*.md` → `check` 判漂移；重新生成后恢复 |
| AC-7 | 零新依赖 | 不新增 `package.json`/lock/`pyproject.toml`；`git status` 无 node 产物 |
| AC-8 | 规则可读 | 每条规约在表里有：档位、等级、检查方式、修复指引；R/S 档不冒充 M 档 |
| AC-9 | 无 CI 也不至于失效 | `pytest tools/scripts/lint` 在 README 与 `docs/04` 的验收命令里；全量 `pytest` 会跑到它 |

---

## 11. 已拍板决策（2026-11-24，用户逐条确认）

| # | 决策 | 选择 | 理由（用户口径） |
|---|---|---|---|
| D-1 | 需求切分 | **拆两个：0045 规约与门禁 + 0044 v2 重构** | 规约是横切全仓的资产，重构是一次性的 |
| D-2 | 前端模块化 | **引入原生 ES Module（零构建）+ 抽 `static/shared/`** | 不引入构建链的前提下，唯一能真正解掉重复实现的手段 |
| D-3 | 门禁实现 | **自研 Python 检查器**（不建议 eslint） | 零新依赖；能查结构类规则（行数、令牌、死字段），eslint 查不了 |
| D-4 | 存量策略 | **新代码 0 豁免 + 白名单只减不增** | 一次性全量整改工期不可控 |
| D-5 | 与 0044 的关系 | **0045 P1 骨架先生效，0044 新代码按本册写** | 否则重构出来的新代码又是"没有规约"的 |

---

## 12. 待确认（剩余，需用户拍板）

1. **BE-03 的行数上限取值**：600 行（推荐，`cli.py` 3901 需要拆成 ≥7 个模块）还是 800 行（少拆一点，但上帝文件容忍度更高）？
2. **FE-11 的 js 400 行上限**：`listener/app.js` 2627 行按业务域可切 6 个（评审 P0-3 建议 ≤400 行/个），是否接受一次性拆完？
3. **`docs/03-骨架设计.md` 是否允许改写**：它现在把 `libs/test_automation` 定为规范模型（§3），且目录清单**缺 `case_runner`**、`data` 里写的是 `runs/<run_id>` 而实际是 `case_runs/`。0044 v2 要把 `libs/case_runner` 立为新 canonical，必须改这份文档（§10 说"变更须经过设计评审"）。
4. **`verify_api_inventory.py` 的去向**：修到绿并收编为 BE-10 的实现，还是直接由 DOC-01 的接口清单生成器取代（删除脚本）？
5. **`tokens.css`（349 行 / 234 变量 / 0 引用）的处置**：三处要求互相矛盾（`check_assets.py:29` 要求存在、`test_app.py:316` 要求存在、`test_cases_page.py:90` 要求页面不得引用）。直接删除并同步改三处，还是保留为归档？

---

## 13. 交付物

| 交付 | 位置 |
|---|---|
| 本需求（规约与门禁契约） | `reqs/0045-dev-conventions-and-gates/REQS.md` |
| 阶段状态 | 同目录 `TODO.md` |
| 细粒度执行计划 | 同目录 `PLAN.md`（**待 REQS 过审后由 writing-plans 生成**） |
| 检查器与规则 | `tools/scripts/lint/`（含 fixtures 与两个 pytest 包装） |
| 前端共享模块 | `apps/workbench/static/shared/` |
| 生成式文档 | `docs/blocks/*.md`、`docs/api-endpoints.md`、`docs/error-codes.md`、`docs/pages/*.md`、`docs/singletons.md` |

---

## 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1 | 2026-11-24 | 首版：规约载体与机制（Rule/Problem/基线/反例自检/无 CI 下的触发点）+ 四册规约目录（BE 15 / FE 15 / UX 5 / DS 3 / DOC 3）+ 实施顺序 G0–G4 + 验收 AC-1..9 + 5 条已拍板 + 5 条待确认 |
