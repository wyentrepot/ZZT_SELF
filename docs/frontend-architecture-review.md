# 前端工程架构评审报告

> 评审日期：2026-08-30
> 评审人：前端架构分析专家
> 评审方式：静态分析（46 个活跃前端文件 / 29,777 行 + 26 个归档文件 / 12,720 行）

---

## 一、项目信息

| 项 | 值 |
| --- | --- |
| 项目 | 侦听台改造（`D:\2-侦听台改造`） |
| 宿主形态 | Python (FastAPI 风格) + pywebview 桌面壳 |
| 前端形态 | **纯静态零构建** —— 无框架、无打包器、无 npm |
| 页面组织 | Shell（`index.html`）+ 9 个 iframe 保活子页 + hash 路由 |
| 前端文件 | 46 个活跃（html 22 / js 18 / css 6），29,777 行 |
| 归档/备份 | 26 个，12,720 行（含 `_tmp_theme_audit/backup`、`docs/_archive`） |
| 工程化配置 | **无**（无 package.json / ESLint / Prettier / Vite / tsconfig / CI） |
| 模块化 | **零** —— 全仓 `import` / `export` 出现次数 = 0 |

---

## 二、总览评分

| 维度 | 得分 | 星级 | 诊断结论 |
| --- | --- | --- | --- |
| 技术栈健康度 | 9 / 20 | ⭐⭐ | 零依赖无供应链风险，但零工程化等于无护栏 |
| 架构设计模式 | 9 / 25 | ⭐⭐ | Shell 设计意图正确，模块边界完全缺失 |
| 工程化成熟度 | 7 / 20 | ⭐⭐ | 仅有契约快照测试，且部分测试在固化技术债 |
| 性能与可维护性 | 9 / 20 | ⭐⭐ | iframe 保活正确，重复与上帝文件拖垮可维护性 |
| 规范一致性 | 8 / 15 | ⭐⭐⭐ | Token 体系质量高，但覆盖残缺、无强制力 |
| **综合** | **42 / 100** | **⭐⭐** | **有架构意识，无架构约束** |

**一句话诊断**：这个项目的问题不是"没有规范"——恰恰相反，`tokens.css` 的水准高于多数 Vue/React 项目。真正的问题是**规范只存在于文件里，没有沉淀为机制**：Token 建了 156 个但 95 个没人用，主题改造写进了 REQS-0010 但代码仍是旧版，每个页面各自造 `esc()`。规范靠人记，不靠工具管。

---

## 三、维度详解

### 3.1 技术栈健康度 — 9/20

**正面**
- 零第三方依赖：无供应链攻击面，无幽灵依赖，无 lock 文件漂移问题。
- 刻意选择的"零构建"策略（`app.js` 注释明确写了"纯静态，零构建"），对 pywebview 桌面壳场景是**合理取舍**，不应盲目引入 React/Vue。

**问题**
| 问题 | 证据 | 影响 |
| --- | --- | --- |
| 无任何工程化配置 | `ls package.json eslint.config.* vite.config.* tsconfig.json` 全部不存在 | 无法 lint、无法格式化、无法做 CI 门禁 |
| 无 TypeScript | 全仓 0 个 `.ts` 文件 | 接口契约靠注释维护，前后端契约靠 Python 测试反查 |
| 无模块系统 | 18 个 JS 文件，`import`/`export` 计数 = 0 | 依赖顺序靠 HTML 里 `<script>` 的书写顺序，脆弱 |
| 无测试框架 | 前端零单测 | 仅有 601 行 Python 契约快照测试 |

**注意**：我不建议直接上 Vite + 框架。这个仓库的前端是**桌面壳内的工具界面**，引入构建链会显著抬高维护成本。见 §4 的 P2-10/P2-11 渐进方案。

---

### 3.2 架构设计模式 — 9/25

**正面（设计意图是正确的）**
- `apps/workbench/static/app.js`：iframe 保活（`ensureFrame` 首次创建后只切换 `hidden`）、hash 路由、侧栏折叠/窄屏抽屉、焦点回归 —— 注释详尽，边界考虑周到（键盘 Escape、aria 属性）。**这是有经验的写法。**
- 页面注册表 `PAGES` + 分组 `GROUPS` 的数据驱动导航，结构清晰。

**问题 1：双部署形态下的代码双轨（架构约束，非人为疏忽）**

> **勘误**：本节初版曾把下列重复定性为"疏忽导致的复制粘贴分叉"。经核实为**误判**——这是有意的设计决策，`apps/workbench/app.py` 文件头注释已明确记录：
> > `/api/listener/*` 与 `/api/module-serial/*`：请求 `/api/listener/logs/status` → 子应用内部路由 `/api/logs/status`。JS 内 `/api/` 统一改为 `/api/listener/`、`/api/module-serial/`

**两个入口并存**，这是重复存在的根本原因：

| 入口 | 启动方式 | 静态目录 | API 前缀 |
| --- | --- | --- | --- |
| 独立 app | `apps/listener/run.py` | `apps/listener/static/` | `/api/...` |
| 嵌入工作台 | `apps/workbench/run.py` | `apps/workbench/static/pages/listener/` | `/api/listener/...` |

工作台以 `_mount_proxied(app, "listener", _sub, "/api/listener")` 挂载子应用，且 `app.mount("/static", ...)` 只挂自己的 static 目录 —— 因此两份前端**都必须存在**，不是冗余。

但按 **git blob** 分级后（以 git 为唯一事实来源），问题性质分四类，**处理方式截然不同**：

**A 类 · 纯冗余 —— blob 完全相同，可零风险消除**（合计 3,752 行）

| 文件 A | 文件 B | blob | 行数 |
| --- | --- | --- | --- |
| `apps/listener/static/styles.css` | `pages/listener/styles.css` | `35408bcd` | 1333 |
| `apps/module_log/static/module-serial.js` | `pages/module-serial/module-serial.js` | `5c481175` | 1131 |
| `apps/module_log/static/styles.css` | `pages/module-serial/styles.css` | `08105f5b` | 1288 |

**B 类 · 纯配置差异 —— 业务逻辑 100% 相同，差异只有 API 前缀**

| 文件对 | blob A / B | 差异行数 | 差异性质 |
| --- | --- | --- | --- |
| `listener/static/app.js` vs `pages/listener/app.js` | `773c9941` / `69efecf6` | 116 | 全部是 `/api/` → `/api/listener/` |
| `listener/static/frames-pro.js` vs `pages/listener/frames-pro.js` | `1884d069` / `c4f628bb` | 6 | 全部是 `/api/` → `/api/listener/` |

**C 类 · 合理的架构差异 —— 不应合并**

`index.html`、`module-serial.html` 的嵌入版多出防主题闪跳脚本（独立 app 非 iframe 渲染，不需要这段）。**这是正确的差异，保留。**

**D 类 · 真实漂移 —— 需业务确认**

`module-serial.html` 除路径前缀外，默认串口参数不一致：

| 参数 | 独立版 `apps/module_log/` | 嵌入版 `pages/module-serial/` |
| --- | --- | --- |
| 波特率默认值 | `115200` selected | `9600` selected |
| 校验位默认值 | `N` selected | 见代码 |

同一功能在两个入口的默认值不同。**是否属有意设计需业务侧确认，不由架构评审单方面定性。**

> **问题 1 的真正结论**：不是"不该有两份"，而是**业务逻辑（99%）与部署配置（1%）被物理耦合在同一文件里**。理想状态是业务逻辑只有一份，前缀由部署层注入。

**问题 2：上帝文件**

`pages/listener/app.js` —— 2136 行、79 个顶层函数、**6 个业务域**共处一室：

| 业务域 | 代表函数 | 函数数 |
| --- | --- | --- |
| 文件选择对话框 | `pickerOpen/Close/DirRow/FileRow/Confirm` | 6 |
| 帧列表 + 详情渲染 | `renderFrames/renderDetail/renderFieldTable/switchDetailTab` | 12 |
| 分钟采集分析 | `renderMinuteAnalysis/renderTaskConfig*/summarizeMinuteReports` | 10 |
| 串口实时采集 | `setSerialState/startSerialPolling/stopSerialPolling` | 3 |
| **网络承载评估** | `renderNetwork*/makeMetricRow/buildRouteValue` | 18 |
| **Canvas 图表绘制** | `drawSuccessRateChart` | 1 |

网络评估 + Canvas 绘图属于独立业务能力，被塞进侦听台页面文件。

**问题 3：视图/样式/逻辑三合一**

22 个 HTML 文件中，**21 个**内嵌 `<style>` 块（26~484 行），**20 个**内嵌 `<script>` 块。

| 文件 | 内联 CSS 行数 | 内联 style 属性 |
| --- | --- | --- |
| `ui-demo/workbench-ui-demo.html` | 484 | **264** |
| `docs/ui/sim-concentrator-ui.html` | 392 | 51 |
| `apps/workbench/static/preview/index.html` | 332 | 9 |
| `pages/module-serial/module-serial.html` | 210 | 24 |
| `apps/workbench/static/workbench.html` | 108 | 12 |

**问题 4：全局命名空间污染**

`apps/listener/static/app.js` 和 `apps/workbench/static/pages/listener/app.js` **均未被 IIFE 包裹** —— 79 个函数直接泄漏到 `window`。同页面内 `frames-pro.js` 共享同一全局域，命名冲突风险真实存在。（其余 9 个 JS 文件已正确包裹，说明是遗漏而非不懂。）

**问题 5：功能缺陷（顺带发现）**

`workbench.html` 中 `cancelRun()` 调用 `pollRun(runId)` 但未清除前一轮的 `setTimeout` 句柄 —— 取消操作会产生**双轮询**，两个 timer 链同时跑。

---

### 3.3 工程化成熟度 — 7/20

**测试现状**：601 行 Python 契约测试，覆盖 shell 导航、串口配置、模块日志、侦听台布局。

测试质量其实不差：

```python
test_shell_keeps_lazy_iframes_instead_of_reassigning_one_frame()
test_hash_routing_written_and_read()
test_drawer_keyboard_and_overlay_close()
```

这些断言精准锁定了架构意图。**但有一个测试暴露了系统性错误**：

```python
def test_both_javascript_copies_parse()
```

**这个测试在给"两份重复 JS"背书。** 技术债被测试固化成了契约——将来想删副本，测试会先红。这是比没有测试更糟的状态：错误的现状被保护起来了。

**缺失**：构建流程 / lint / 格式化 / 前端单测 / E2E / CI 门禁 / 依赖审计，全部为零。

---

### 3.4 性能与可维护性 — 9/20

**正面**
- iframe 保活策略正确：切换页面不重载，9 个子页状态不丢失。
- 无第三方库，无 bundle 体积问题。

**问题**

| 问题 | 数据 | 影响 |
| --- | --- | --- |
| 主题样板重复 | 防闪跳脚本 × 10 处（60 行）；主题白名单数组 × 9 处；THEMES 映射 × 2 处 | **改一个主题名要动 9~11 处** |
| `esc()` 各写各的 | dict / frames-pro / scenario / simcon / trace 各自定义，**且实现不一致**（4 处 `var esc = function`，1 处 `const esc = () =>`） | 转义规则不统一，安全基线不一致 |
| 死代码 | tokens.css 定义 156 变量，仅 127 个被引用，**95 个从未使用** | Token 体系可信度下降 |
| 归档膨胀 | 26 个归档前端文件 / 12,720 行（占活跃代码 43%） | 检索干扰，误改风险 |

`esc()` 覆盖度极不均衡：

| 文件 | innerHTML 赋值 | esc 相关 | 风险 |
| --- | --- | --- | --- |
| `dict.js` | 10 | 36 | 低 |
| `simcon.js` | 19 | 29 | 低 |
| `trace.js` | 15 | 35 | 低 |
| `module-serial.js` | 14 | **2** | **高** |
| `pages/listener/app.js` | 3 | **0** | **高** |

---

### 3.5 规范一致性 — 8/15

**`tokens.css` 是本项目最好的资产**（364 行、156 变量、三层结构）：

```css
/* ---- Status · inconclusive（REQS-0011 §4.6 铁律）----
   inconclusive = 证据缺失（≠失败），必须是独立紫色相，不得复用 warn 琥珀。
   语义色不属于品牌色，故定义在通用区：四套主题共用，不随主题漂移。 */
--st-inconclusive: #a371f7;
```

间距走 4px 基准网格、字号有 scale、动效有 duration/easing 变量、连 `color-scheme` 都考虑了原生控件。**这份文件的作者懂设计系统。**

**但覆盖残缺**：

| 页面 | 硬编码色 | var() | Token 率 |
| --- | --- | --- | --- |
| `maintenance.html` | 0 | 21 | 100% |
| `workbench.html` | 0 | 57 | 100% |
| `serial-profile.html` | 5 | 46 | 90.2% |
| `simcon.html` | 24 | 136 | 85.0% |
| `dict.html` | 15 | 83 | 84.7% |
| `scenario.html` | 23 | 89 | 79.5% |
| `pages/listener/index.html` | 20 | 63 | 75.9% |
| **`module-serial.html`** | **105** | 88 | **45.6%** ← 主题改造漏网 |
| **`preview/index.html`** | **88** | 42 | **32.3%** ← 独立实现，已分叉 |
| 合计 | 304 | 785 | 72.1% |

**规范与实现脱节（最值得警惕）**：

> REQS-0010 P5 规定主题为 **2 套**：`theme-midnight`（墨夜深色）/ `theme-daylight`（晴昼浅色）。
> 代码中实际是 **4 套旧主题**：`theme-deepblue` / `theme-emerald` / `theme-charcoal` / `theme-indigo`。

`theme-midnight` 和 `theme-daylight` 在全仓**出现 0 次**。需求写了，代码没跟上——这正是"规范没有强制力"的直接后果。

另外 `preview/index.html` 是**平行的第二套实现**：用 `theme-btn` 而非 `theme-dot`，带 4 处内联 `onclick="switchTheme(...)"`，Token 率仅 32.3%。它与正式 shell 已经完全分叉。

---

## 四、重构优先级

### P0 — 阻断级（不做则技术债持续放大）

| # | 改进项 | 预期收益 | 工时 |
| --- | --- | --- | --- |
| P0-1a | **合并 A 类纯冗余**：blob 完全相同的 3 组（listener/styles.css、module-serial.js、module_log/styles.css），workbench 端改为引用同一静态路径 | -3,752 行，零行为变更 | 0.5 人日 |
| P0-1b | **B 类前缀注入**：`app.js` / `frames-pro.js` 抽取 `API_BASE` 常量（或后端模板注入），消除 122 行前缀差异 —— **两个入口都保留** | 业务逻辑回归单点维护 | 1 人日 |
| P0-2 | **抽取 `theme-boot.js` 公共模块**：统一防闪跳 + 主题白名单 + THEMES 映射 | 主题名改动点 9~11 处 → **1 处** | 0.5 人日 |
| P0-3 | **拆分 `pages/listener/app.js`**：按 6 个业务域切成 `picker.js` / `frames.js` / `minute.js` / `serial.js` / `network.js` / `chart.js` | 单文件 2136 行 → 6 个 ≤400 行 | 2 人日 |
| P0-4 | **修 `workbench.html` 双轮询**：`cancelRun` 前 `clearTimeout` 上一轮 timer | 消除隐性性能泄漏 | 0.2 人日 |

> **注意（勘误后修正）**：初版曾建议"消灭 listener/app.js 双份 fork"，方向有误 —— 两个入口都必须保留，正确解法是**抽共享 + 注入前缀**，不是删文件。
> - P0-1a 独立于任何改动，随时可执行，是投入产出比最高的一项（0.5 人日 / -3,752 行）。
> - P0-1b 与 P0-3 有依赖：先做前缀注入再拆上帝文件，否则同一份逻辑要拆两遍。

### P1 — 高优先（补齐规范落地）

| # | 改进项 | 预期收益 | 工时 |
| --- | --- | --- | --- |
| P1-1 | **落地 REQS-0010 P5 双主题**：`theme-midnight` / `theme-daylight` 替换现有 4 套 | 需求与实现对齐 | 2 人日 |
| P1-2 | **Token 覆盖补全**：`module-serial`（105 硬色）、`preview`（88 硬色） | Token 率 72% → 95%+ | 1.5 人日 |
| P1-3 | **统一 `esc()` 到公共 util**：`module-serial.js`、`listener/app.js` 必须接入 | 消除 XSS 面差异 | 0.5 人日 |
| P1-4 | **IIFE 包裹 `listener/app.js`** | 79 个全局函数收拢 | 0.2 人日 |
| P1-5 | **清理**：95 个死 Token + 26 个归档文件（12,720 行） | 检索信噪比 | 0.5 人日 |

### P2 — 中长期（建立机制，防止复发）

| # | 改进项 | 预期收益 | 工时 |
| --- | --- | --- | --- |
| P2-1 | **引入原生 ES Module**（`<script type="module">`）：不换框架、不加构建，仅用浏览器原生模块 | 显式依赖，告别 script 顺序脆弱性 | 2 人日 |
| P2-2 | **加轻量工程化**：`package.json` + ESLint + Prettier（**只加约束，不改构建**） | 规范从"人记"变"机器管" | 1 人日 |
| P2-3 | **内联 style 抽离**：22 个 HTML 的 26~484 行内联 CSS 移入独立文件 | 样式可复用、可审查 | 1.5 人日 |
| P2-4 | **改造契约测试**：让 `test_both_javascript_copies_parse` 失效，改为断言"单一实现 + 多前缀" | 测试不再为技术债背书 | 0.5 人日 |
| P2-5 | **ui-demo 与正式实现的同步机制**：3702 行 demo 已与正式代码分叉（264 处内联 style） | 原型与实现不再各行其是 | 1 人日 |

---

## 五、总结

**回答你的两个问题：**

1. **架构是否过于耦合？** 是，但不是"设计错了"，是**约束缺位**。Shell 层（路由/保活/导航/双入口代理挂载）的设计是对的，问题出在页面层：零模块化、上帝文件、业务逻辑与部署前缀物理耦合。改一个主题名要动 9 处，这是最硬的耦合证据。

2. **是否没有规范风格？** 不是没有，是**规范没有被强制执行**。`tokens.css` 水准很高，但它管不到 45.6% Token 率的 `module-serial.html`，也管不到各自造轮子的 `esc()`。REQS-0010 写了双主题，代码里还是四套旧主题——文档和代码之间没有任何机制保证同步。

**最小投入最大收益的三件事**：

| 顺序 | 动作 | 工时 |
| --- | --- | --- |
| 1 | P0-1a 合并 A 类纯冗余（3,752 行，blob 全同，零行为变更） | 0.5 人日 |
| 2 | P0-2 抽出 `theme-boot.js`（主题改动点 9~11 处 → 1 处） | 0.5 人日 |
| 3 | P2-2 加 ESLint（规范从"人记"变"机器管"） | 1 人日 |

合计 **2 人日**，能把这个项目从"靠人记规范"推进到"靠机制管规范"。

**需业务确认的遗留项**：`module-serial.html` 两个入口的串口默认值不一致（独立版 `115200` / 嵌入版 `9600`），见 §3.2 D 类。这一条不由架构评审单方面定性。

---

> **免责声明**：本报告基于静态分析和经验规则生成，仅供参考，实际重构决策请结合团队情况综合判断。架构没有银弹，合适的才是最好的——本项目"零构建"的取舍在特定场景下是合理的，不应盲目套用主流前端工程化方案。
