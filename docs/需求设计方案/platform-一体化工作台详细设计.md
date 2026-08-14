# platform 一体化工作台 —— 详细设计文档

> **文档定位**：FR-6（统一集成程序）的详细设计（DD），供评审后进入编码。本文档细化 `platform/` 包的目录结构、统一后端挂载方式、编排层模块、前端页签架构、数据模型与实施顺序。
> **版本**：v0.1（评审稿）｜ **日期**：2026-08-14 ｜ **状态**：待评审
> **上游**：`docs/总设计框架/AI闭环平台项目设计需求文档.md`（§4 FR-6、§6.1、§8、§9.2）
> **复用**：现有 `desktop.py`（ADR-2/3）、`create_simcon_app(prefix="")` 挂载模式（ADR-13）、`module_log` 对照解析前端

---

## 1. 设计目标

把三个独立服务 + 三个页面收敛为**一个程序**：

| 现状（分散） | 目标（platform 统一） |
|--------------|----------------------|
| 侦听台 8765 + 页面 | 页签 1「侦听台」 |
| 模块日志/烧录 8766 + 页面（含对照解析/模拟集中器页签） | 页签 2「模块日志」+ 页签 3「对照解析」+ 页签 4「模拟集中器」 |
| sim_concentrator 8781 + REST | （并入页签 4，REST 保留） |
| —（无编排） | 页签 5「验证工作台」：Run / 场景 / 流程比对 / 归因反馈 / 报告 |

**硬性约束**：不合并底层代码、不重实现编排能力（FR-6.4）——platform 只做**挂载 + 编排 + 统一外壳**。

---

## 2. 目录结构（`apps/platform/` 顶层包，apps/ 内与 listener、module_log 平级）

```
apps/platform/
├── __init__.py
├── app.py                  # 统一 FastAPI 工厂 create_platform_app()
├── desktop.py              # 统一桌面入口（pywebview 单窗口）
├── run.py                  # uvicorn 启动入口（python -m platform.run）
├── orchestration/          # 编排层（无 UI 依赖，可被 CLI/REST 复用）
│   ├── __init__.py
│   ├── models.py           # Run / StepResult / Report 数据类（pydantic）
│   ├── store.py            # RunStore：SQLite 元数据 + 报告 JSON 归档
│   ├── scenarios.py        # 场景模板库加载/校验（JSON 目录）
│   ├── runner.py           # RunExecutor：烧录→监控→激励→报告 串行编排
│   ├── compare.py          # 期望流程比对器（期望序列 vs 实际事件流）
│   └── feedback.py         # 归因规则引擎（失败 → 归因反馈文本）
├── api.py                  # 编排路由（/api/run、/api/scenarios、/api/compare、/api/feedback）
├── static/                 # 统一前端 SPA（页签式）
│   ├── index.html
│   ├── app.js              # 页签注册表 + 路由 + 状态
│   ├── styles.css
│   └── pages/              # 各页签视图（纯前端，数据走后端 API）
│       ├── listener.html/js
│       ├── module-serial.html/js    # 复用 apps/module_log/static 现有文件（复制或代理）
│       ├── compare.html/js
│       └── workbench.html/js
├── scenarios/              # 场景模板 JSON（数据文件，随包分发）
│   ├── join_anhui.json     # 入网（安徽）
│   ├── minute_collect.json # 分钟采集闭环
│   ├── open_close.json     # 拉合闸
│   └── search_meter.json   # 搜表
├── test_app.py             # 挂载/路由测试
├── test_orchestration.py   # 编排层单测
└── requirements.txt
```

**前端复用策略**：listener 与 module_log 的现有页面文件**物理复制**到 `platform/static/pages/`（二者现为无构建的纯 HTML/JS/CSS，复制即可独立演化），避免运行时跨目录代理的脆弱性；页面内 API 相对路径在复制时统一加 `/api` 前缀适配。

---

## 3. 统一后端设计（app.py）

### 3.1 应用工厂

```python
def create_platform_app(
    listener_factory=None,     # 注入：listener.app.create_app，默认 lazy import
    module_log_factory=None,   # 注入：module_log.app.create_app
    simcon_factory=None,       # 注入：sim_concentrator.api.create_simcon_app
    run_store=None,            # 注入：RunStore（默认 data/runs.sqlite）
) -> FastAPI:
```

- 默认工厂**惰性导入**各子应用（与 `apps/module_log/app.py` 动态 import simcon 同模式，兼容 PyInstaller 打包时补 hiddenimports）。
- **挂载策略**（关键：避免双前缀，沿用 ADR-13 约定）：

| 挂载路径 | 子应用 | 说明 |
|----------|--------|------|
| `/api/listener` | `create_app(...)`（listener） | listener 路由原本是根路径 `/api/*`，需检查其路由是否带前缀；**若不带前缀，用 FastAPI 子应用挂载 + 前端统一加 `/api/listener` 前缀**，或直接 `app.mount("/api/listener", sub)` |
| `/api/module-serial` | `create_app(...)`（module_log） | 同上 |
| `/api/simcon` | `create_simcon_app(prefix="")` | 直接复用 ADR-13 已验证模式 |
| `/api/run` `/api/scenarios` `/api/compare` `/api/feedback` | platform.api 路由 | 编排层（见 §4） |

> **待评审点 A**：listener/module_log 现有路由大多挂在根路径（`/api/...`、`/static`）。方案一（推荐）：platform 用 `app.mount("/api/listener", ...)` 挂载，前端请求 `/api/listener/xxx`；方案二：platform 不物理挂载，只做**同进程多端口代理**（uvicorn 多 app 监听多端口，前端 iframe/跳转）——不推荐，违背"一个程序"目标。**评审确认后需逐路由核对前缀**。

### 3.2 端口与启动

- platform 统一端口 **8790**（待评审问题 1 建议值）。
- `python -m platform.run` → uvicorn 8790；`platform/desktop.py` → pywebview 单窗口加载 `http://127.0.0.1:8790/`。
- 独立服务 8765/8766/8781 **保留**为开发者模式（FR-6.4 双模式并存），`启动工具.bat` 新增选项 6 = 一体化工作台。

---

## 4. 编排层设计（orchestration/）

> 编排层是无 UI 依赖的核心资产：CLI、REST、AI agent 三端复用，等价于 loghooks/sim_concentrator 的独立定位。

### 4.1 models.py —— 统一报告数据模型（FR-5.2 落地）

```python
class Run(BaseModel):
    run_id: str                    # "run-YYYYMMDD-HHMMSS-xxxx"
    scenario_id: str               # 场景模板 id
    firmware: FirmwareInfo         # version / commit / flash_file_sha256
    created_at: datetime
    status: Literal["pending", "running", "passed", "failed", "aborted"]

class Report(BaseModel):
    run_id: str
    firmware: FirmwareInfo
    scenario: str
    sources: SourcesSummary        # module_log 事件摘要 / listener 帧数 / simcon 结论
    assertions: list[Assertion]    # [{id, expected, actual, result}]
    flow_compare: FlowCompare      # §4.4 输出
    verdict: Literal["pass", "fail"]
    artifacts: list[str]           # 日志路径 / 帧 hex / 报告文件
```

### 4.2 store.py —— RunStore（SQLite + JSON 归档）

```sql
CREATE TABLE runs (
  run_id       TEXT PRIMARY KEY,
  scenario_id  TEXT NOT NULL,
  status       TEXT NOT NULL,
  firmware_ver TEXT,
  firmware_commit TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  report_path  TEXT            -- 指向 data/reports/{run_id}.json
);
CREATE TABLE run_steps (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id     TEXT NOT NULL REFERENCES runs(run_id),
  seq        INTEGER NOT NULL,
  kind       TEXT NOT NULL,    -- flash | monitor | stimulus | compare | feedback
  detail     TEXT,             -- 步骤参数/结果摘要（JSON 文本）
  result     TEXT              -- pass / fail / skipped
);
```

- 数据目录：`data/runs.sqlite` + `data/reports/{run_id}.json`（frozen 模式下落在 exe 同目录 `runtime/`，沿用 `_base_dir/_runtime_dir` 约定，见 ADR-2/3）。
- 归档策略：报告 JSON 不可变、run 元数据可更新（status 流转）。

### 4.3 scenarios.py —— 场景模板库

场景 = "期望流程" + "激励任务" + "监控规则集" 三者绑定：

```json
{
  "id": "minute_collect",
  "name": "分钟采集闭环（安徽）",
  "module": "cco",
  "expected_flow": [
    {"step": "onnet",       "event_type": "network.onnet",       "within_ms": 30000},
    {"step": "collect",     "event_type": "collect.minute.e4",   "within_ms": 60000},
    {"step": "no_error",    "negate": true, "event_type": "join.assoc.err"}
  ],
  "stimulus": {
    "task_file": "tasks/minute_collect.json",   // sim_concentrator 验证任务
    "responders": []                            // 可选应答规则覆盖
  },
  "monitor": {
    "rules": ["common", "provinces/anhui"],     // loghooks 规则集引用
    "sources": ["module_log", "listener"]
  }
}
```

- 模板放 `platform/scenarios/*.json`（随包分发），运行期也可加载用户目录（`--scenarios-dir`）。
- `expected_flow` 即"输入事件名 → 得到完整运行流程"的**期望侧**声明（FR-5.3）。

### 4.4 compare.py —— 期望流程比对器（FR-5.3 落地）

输入：`expected_flow`（§4.3）+ 实际事件流（loghooks scan 结果）。

输出四类差异（可机器消费 + 前端渲染）：

```json
{
  "steps": [
    {"step": "onnet",   "status": "hit",    "actual_time": "2026-08-14T10:00:01+08:00"},
    {"step": "collect", "status": "timeout","expected_within_ms": 60000},
    {"step": "no_error","status": "negate_triggered", "actual_event": {"type": "join.assoc.err", "time": "..."}}
  ],
  "missing": ["collect"],            // ❌ 缺失
  "timeouts": ["collect"],           // ⚠️ 超时
  "out_of_order": [],                // 🔀 顺序错乱
  "negated": ["no_error"],           // 🚫 不应出现却出现
  "verdict": "fail"
}
```

算法：事件流按时间排序，对 expected_flow 依序匹配（支持 `optional` 步跳过）；`within_ms` 判定超时；`negate` 步在窗口内扫到即触发；命中次序与声明不符标 `out_of_order`。

### 4.5 feedback.py —— 归因规则引擎（FR-5.4 落地）

规则表（JSON，可配置）：

```json
{
  "rules": [
    {"when": {"compare.negated": ["join.assoc.err"]},
     "then": "关联流程异常：检查 assoc 相关打印与信标接收，重点看 NID 分配"},
    {"when": {"compare.missing": ["collect.minute.e4"]},
     "then": "分钟上报缺失：检查采集任务配置（OI 6000/6001）与集中器是否下发抄读帧"}
  ]
}
```

输出：结构化反馈（`[{issue, evidence, suggestion}]`），供 AI 编码模型/工程师阅读；接口先行，消费方后续对接。

### 4.6 runner.py —— RunExecutor（全链路编排）

```
execute_run(run):
  1. flash    → 调 module_log 烧录能力（XMODEM，复用 module_serial_service）或标记"已烧录"
  2. monitor  → 复位后启动 loghooks 实时/离线扫描（收集事件流）
  3. stimulus → 调 sim_concentrator runner 执行任务（场景绑定的 task）
  4. compare  → 期望流程 vs 实际事件流
  5. feedback → 按归因规则生成反馈
  6. report   → 聚合 Report 落盘 + 更新 runs 表
```

- 每步独立可跳过（`--skip-flash` 等），支持"仅监控"、"仅激励"等局部闭环（AI 可按需组合）。
- 所有子能力**调用现有模块 API**，不重实现（FR-6.4 第三条）。

---

## 5. 前端设计（static/）

### 5.1 页签注册表（app.js）

```js
const PAGES = [
  { id: "listener",    title: "侦听台",     src: "pages/listener.html" },
  { id: "module",      title: "模块日志",   src: "pages/module-serial.html" },
  { id: "compare",     title: "对照解析",   src: "pages/compare.html" },
  { id: "simcon",      title: "模拟集中器", src: "pages/simcon.html" },
  { id: "workbench",   title: "验证工作台", src: "pages/workbench.html" },
];
```

- 顶部页签栏 + 内容区 iframe/嵌入渲染（纯静态方案，零构建）。
- 各页签页面保持与现有独立应用页面一致的使用方式（页签 = 应用级导航，非功能级）。

### 5.2 验证工作台页签（新增核心）

- **场景选择**：`GET /api/scenarios` 列出模板 → 选场景 → 填固件信息（版本/commit/文件 hash）→ 可选勾选执行阶段。
- **执行**：`POST /api/run` 创建并执行 → 轮询 `GET /api/run/{id}` 展示进度（烧录→监控→激励→比对→反馈逐步状态条）。
- **结果**：`GET /api/run/{id}/report` 渲染：事件流时间线 + 流程比对图（四类差异色标）+ 逐步结论 + 归因反馈卡片 + 证据链（日志/帧 hex 可点开）。

### 5.3 对照解析页签

- 复用现有 module_log「对照解析」交互（来源卡片 + 双向联动），数据源扩展：平台内可直接扫 `data/logs/` 下历史日志。

---

## 6. 桌面入口（desktop.py）

- 复制 apps/module_log/desktop.py 模式（ADR-2）：后台线程起 uvicorn(8790) → 主线程 pywebview 开窗；未装 pywebview 回退浏览器。
- 窗口标题：「AI 闭环工作台」；窗口尺寸 1440×900 起。
- frozen 路径处理沿用 `_base_dir/_runtime_dir/_log_dir` 约定（LOG 与 runtime 落在 exe 同目录）。

---

## 7. 实施顺序（建议）

| 步 | 内容 | 出口 |
|----|------|------|
| P1 | `platform/` 骨架：app.py 工厂 + 挂载 3 子应用 + 静态页签外壳 + desktop.py | 8790 一个窗口见 4 个页签可切换 |
| P2 | orchestration models/store + `/api/run` 建单/查询（先做"仅监控"最小闭环） | 一次真实日志扫描产出 Report |
| P3 | scenarios 模板库 + compare 比对器 + 工作台页签渲染 | 四类差异可视化正确 |
| P4 | runner 全链路（烧录→监控→激励→比对→反馈）+ feedback 归因 | 端到端 Run 出完整报告 |
| P5 | 打包（`tools/packaging/platform.spec`，hiddenimports 补全）+ 启动工具.bat 选项 6 | 一体化 exe 可用 |

---

## 8. 验收标准（对齐 FR-6.5）

1. 双击一体化 exe → 单窗口含 5 个页签，各页签功能与独立应用一致。
2. 验证工作台按场景模板一键执行，报告含：事件流摘要 + 流程比对（缺失/超时/乱序/负向）+ 归因反馈 + 证据链。
3. 报告按 run_id 归档，`GET /api/run/{id}` 可回溯。
4. 底层模块独立运行与全量 pytest 回归不破（402 passed 基线）。

---

## 9. 风险与依赖

| 项 | 说明 | 对策 |
|----|------|------|
| 子应用路由前缀冲突 | listener/module_log 路由多挂根路径 | P1 逐路由核对，前端统一加前缀；必要时给子应用路由加 prefix |
| listener 依赖 C# DLL/pythonnet | platform 挂载 listener 时同样需要 DLL 环境 | 惰性导入 + 挂载失败降级（页签显示"不可用"，不拖垮整体） |
| 前端复制维护 | 页面复制后与源独立演化 | 文档注明来源版本；后续可考虑抽公共 API client |
| 串口争用 | 多页签可能同时碰同一串口 | 沿用 409 冲突约定（ADR-13），不做互斥锁 |
| PyInstaller 打包 | 动态 import 的子应用/数据文件易漏 | 按 ADR-8 经验：hiddenimports + collect_data_files（scenarios/rules） |
| 编排时序（复位等待/事件收集窗口） | 全链路时序参数需可配置 | 场景模板增加 `timing` 段（复位等待、监控窗口） |

---

## 10. 待评审问题

1. listener/module_log 路由前缀改造方式（§3.1 待评审点 A）：统一加前缀 vs 保持根路径 + 前端代理。
2. platform 端口 8790 与 exe 名称「AI 闭环工作台」是否确认。
3. 验证工作台首期场景范围（建议：分钟采集闭环 + 入网）。
4. 前端页面：物理复制现有页面（推荐） vs iframe 直连独立服务端口。
5. Run 执行是否支持异步队列（长任务后台跑 + 前端轮询），还是同步阻塞（简单优先）。

---

*本文档为 FR-6 详细设计评审稿，评审确认后进入编码；架构决策同步追加至 DECISIONS.md（ADR-14）。*
