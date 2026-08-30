# 工作台主题系统改造方案

> 配套文档：`ASSESSMENT.md`（七维度诊断）
> 日期：2026-08-30
> 约束：零构建、纯静态、iframe 保活架构、9 个页面

---

## 1. 目标架构：三层 Token

```
┌─────────────────────────────────────────────────────┐
│  L1 · Primitive  原始色板（不随主题变，或整体换一套）  │
│     --p-blue-500: #22d3ee                            │
│     --p-slate-200: #e8f0f7                           │
│     --p-slate-900: #090c10                           │
└────────────────────┬────────────────────────────────┘
                     ↓  只在 L2 被引用
┌─────────────────────────────────────────────────────┐
│  L2 · Semantic   语义角色（随主题切换改变指向）        │
│     --color-bg-canvas:   var(--p-slate-900)          │
│     --color-fg-default:  var(--p-slate-200)          │
│     --color-accent:      var(--p-blue-500)           │
│     --color-status-warn: var(--p-amber-500)          │
│     --color-status-inconclusive: var(--p-purple-500) │
└────────────────────┬────────────────────────────────┘
                     ↓  只在 L3 被引用
┌─────────────────────────────────────────────────────┐
│  L3 · Component  组件槽位（业务组件只准用这层）        │
│     --btn-primary-bg:  var(--color-accent)           │
│     --card-bg:         var(--color-bg-surface)       │
│     --table-header-fg: var(--color-fg-muted)         │
└─────────────────────────────────────────────────────┘
```

**这条链的价值**：想改"主按钮的蓝"而不动"链接的蓝"，只需在 L3 改一个槽位；想换整套配色，只需在 L1 换一套色板。当前架构这两件事都做不到。

---

## 2. 五个关键设计决策

### D1 · 主题定义用 CSS 变量，不用 JS 注入

零构建环境下，把主题写死在 CSS 里最稳：**JS 挂了主题仍在**（当前 `postMessage` 方案一旦 JS 异常，子页面主题直接失效，无兜底）。

JS 只负责"切换 `data-theme` 属性"，不负责"产生颜色值"。

### D2 · 用 `data-theme` 属性，不用 `className`

```html
<html lang="zh-CN" data-theme="midnight">
```

```css
[data-theme="midnight"] { --color-bg-canvas: #090c10; ... }
[data-theme="daylight"] { --color-bg-canvas: #f7f9fb; ... }
```

**为什么**：当前 `app.js:214` 的 `document.documentElement.className = theme` 会清空 `<html>` 上所有其他 class，而 `postTheme()` 反过来又读 `className` 当 key —— 双向脆弱。属性选择器天然与其他 class 共存。

### D3 · 引入「方言别名层」，让 6 个游离页面零改动接入 ⭐

**这是本方案的核心，也是风险最低的迁移路径。**

6 个私有方言页面（B 系 4 个 + C 系 2 个）的迁移，如果逐个改变量名，工作量是 2-3 天且极易出错。
改用别名层，每个页面只需**引入一个 CSS 文件**，代码一行不改：

```css
/* compat-dialects.css —— 方言兼容层 */
:root, [data-theme] {
  /* B 系（设计稿方言）→ 标准语义层 */
  --bg-0: var(--color-bg-canvas);
  --bg-1: var(--color-bg-surface);
  --bg-2: var(--color-bg-raised);
  --bg-3: var(--color-bg-elevated);
  --bg-4: var(--color-bg-hover);
  --tx-1: var(--color-fg-default);
  --tx-2: var(--color-fg-muted);
  --tx-3: var(--color-fg-subtle);
  --tx-4: var(--color-fg-dim);
  --ac:   var(--color-accent);
  --am:   var(--color-status-warn);

  /* C 系（侦听台方言）→ 标准语义层 */
  --canvas: var(--color-bg-canvas);
  --canvas-2: var(--color-bg-surface);
  --panel:  var(--color-bg-surface);
  --panel-raised: var(--color-bg-raised);
  --ink:   var(--color-fg-default);
  --muted: var(--color-fg-muted);
  --faint: var(--color-fg-subtle);
  --cyan:  var(--color-accent);
}
```

**效果**：因为这层是 `var()` 引用而非拷贝，主题切换时别名会自动跟随。**6 个页面立刻参与主题切换，零行代码改动。**

**后续**：别名层是过渡手段，不是终点。页面后续正常迭代时，逐步把 `--bg-0` 改成 `--color-bg-canvas`，别名层随最后一个引用消失而删除。

### D4 · 统一主色（**需你拍板**）

当前三种"青"必须收敛为一种。我的建议与理由：

| 候选 | 来源 | 对比度（on bg-1） | 建议 |
|------|------|------------------|------|
| `#22d3ee` 亮青 | **B 系 = `docs/ui` 设计稿基线** | **10.32:1** | ✅ 推荐 |
| `#06b6d4` 青蓝 | A 系（当前默认） | ~8.5:1 | 备选 |
| `#45e0c2` 青绿 | C 系（侦听台） | 10.92:1 | 不推荐（偏绿，与"工业仪表盘"调性不符） |

**推荐 `#22d3ee`**：它是 `docs/ui/` 设计稿的原生主色（即 REQS-0011 已认可的风格基线），对比度也最高。采用它等于让代码追上设计决策。

> ⚠️ 但我不能替你拍板配色——这是设计决策不是工程决策。请确认。

### D5 · 消除双层渐变

| 位置 | 动作 |
|------|------|
| `styles.css:11` | 父页 `body` 背景由 `var(--bg-gradient)` 改为 `var(--color-bg-canvas)` |
| `tokens.css:183` | `--frame-bg` 由 `transparent` 改为 `var(--color-bg-canvas)`（避免 iframe 穿透） |
| `workbench.html:11` | 子页 `body` 背景去掉渐变，改为 `var(--color-bg-canvas)` |
| 全部页面 | 补 `color-scheme: dark`（对齐 REQS-0011"去掉重玻璃拟态"的决策） |

---

## 3. 迁移策略：三步走

```
Step 1 · 建新不拆旧
  新建 tokens-v2.css（三层体系）+ compat-dialects.css（别名层）
  → 旧 tokens.css 保持不动，零风险期

Step 2 · 批量接入
  9 个页面统一引入 tokens-v2.css + compat-dialects.css
  → 6 个游离页面零改动参与主题切换
  → 此步可逐页灰度、逐页验证

Step 3 · 收敛清理
  页面迭代时逐个把方言名换成标准名
  → 最后一个方言引用消失 → 删除 compat-dialects.css
  → 删除旧 tokens.css
```

**这个顺序的关键价值**：每一步都可独立验证、独立回滚，不存在"改到一半整个系统不可用"的中间态。

---

## 4. 分期计划

### P0 · 立竿见影（合计约 1.5h，零架构风险）

| # | 动作 | 文件 |
|---|------|------|
| 1 | 消除双层渐变（`body` 用纯色，iframe 不再透明） | `styles.css` / `tokens.css` / `workbench.html` |
| 2 | 全部页面补 `color-scheme: dark` | 各页 `:root` |
| 3 | inconclusive 独立紫色相 `#A371F7` | `tokens.css` |
| 4 | FOUC 修复：`<head>` 内联 3 行脚本，DOM 渲染前应用 `data-theme` | `index.html` + 各页 |

**P0 的 4 项都不触碰架构，但能解决用户能直接感知到的绝大部分"丑"。**

### P1 · 架构地基（约 1 天）

| # | 动作 |
|---|------|
| 5 | 建 `tokens-v2.css`：primitive / semantic / component 三层 |
| 6 | 建 `compat-dialects.css`：B/C 方言别名层 |
| 7 | 主题机制由 `className` 改为 `data-theme` 属性 |
| 8 | 修正对比度：`--fg-dim` / `--tx-4` / `--faint` 提亮至 ≥4.5:1 |
| 9 | `styles.css` 的 7 处魔数收编进 spacing/font-size token |

### P2 · 全面接入（约 2-3 天，**高风险、需逐页验证**）

| # | 动作 |
|---|------|
| 10 | 9 个页面接入 `tokens-v2.css` + `compat-dialects.css`（逐页灰度） |
| 11 | 主题注册改为单一数据源（消除 CSS/JS/HTML 三处同步） |
| 12 | 主题覆盖率自动化断言（见 §6） |

### P3 · 收敛（约 2 天，并入 P5 决策）

| # | 动作 |
|---|------|
| 13 | 主题收敛为 2 套：墨夜深色（默认）+ 晴昼浅色 |
| 14 | 支持 `prefers-color-scheme` 自动跟随 |
| 15 | 删除方言别名层与旧 `tokens.css` |

---

## 5. 风险与应对

| 风险 | 等级 | 应对 |
|------|------|------|
| P2 逐页接入时改坏高频页面（侦听台 2135 行 JS / 模块日志 1130 行 JS） | **高** | 别名层保证零 JS 改动；逐页灰度 + 逐页截图比对；每页验证后单独 commit |
| 统一主色后，某页面视觉回归 | 中 | 先切外壳与小页面，侦听台/模块日志最后动 |
| `apps/` 目录的 E-SafeNet 加密环境风险（见项目记忆 2026-08-30 事故） | **高** | **每完成一页立即 commit**，或先把 `tokens-v2.css` 落到非 `apps/` 路径验证；杜绝 `git rm` + `git checkout -- apps/` 组合操作 |
| 浅色主题（P3）工作量被低估 | 中 | 三层分层建成后，浅色主题只需覆盖 L2 语义层；但如果页面里有硬编码颜色没清干净，浅色下会暴露——故 P2 必须先做透 |
| 三方方言别名层长期不清理，变成新的技术债 | 中 | 在 `compat-dialects.css` 头部标注废弃计划与删除条件 |

---

## 6. 验收标准与自动化

### 主题覆盖率断言（**必须用无头 Chrome，jsdom 不行**）

对每个页面断言：**切换 `data-theme` 后，body 背景色的 computed value 确实发生变化。**

> ⚠️ **踩过的坑（2026-08-30 P5 实战结论，直接复用）**：
> **jsdom 不解析 CSS 自定义属性**，`getComputedStyle().backgroundColor` 永远拿到空串或原值，
> 用它做断言会得到 100% 假通过。必须起服务后用无头 Chrome 读真实 computed value：
>
> ```bash
> chrome --headless=new --dump-dom http://127.0.0.1:PORT/static/pages/trace/trace.html
> ```
>
> 当时实测 41 个令牌在两套主题下全部翻转，才敢判定主题真生效。
> 另注：起服务**不要走 `.build_plain/`**（那是 `git archive HEAD` 导出的旧副本），
> 静态资源是明文，直接从工作区启动即可。

**这个断言能防止新页面重蹈覆辙**——当前 6/9 页面不生效，如果当时有这条断言，第一天就会被拦下。

### 静态检查

```bash
# 方言定义点应逐步收敛到 1 个
grep -rn "^\s*:root\s*{" apps/workbench/static/ | wc -l

# 未接入公共 token 的页面应为 0
comm -23 <(ls apps/workbench/static/pages/*/[a-z]*.html | sort) \
         <(grep -rl "tokens-v2.css" apps/workbench/static/ | sort)

# 组件层不得直接引用 primitive（只允许经由 semantic）
grep -rn "var(--p-" apps/workbench/static/ \
  | grep -v "tokens-v2.css" | grep -v "compat-dialects.css"
```

### 对比度回归

```bash
cd _tmp_theme_audit && python contrast.py   # 应为 0 FAIL
```

---

## 7. 可复用的既有资产：P5 遗留知识

2026-08-30 那轮 P5 重构的代码虽已随 `apps/` 事故丢失，但**设计知识完整保存在项目记忆中**。
本轮重做应站在这些结论上，而不是从零开始。

### 7.1 调色板命名对照

| 本方案命名 | P5 记忆命名 | 覆盖范围 |
|-----------|------------|----------|
| A · 工作台 | （P5 未单列） | 外壳 + workbench + maintenance + serial-profile |
| B · 设计稿系 | **Palette A** | dict / simcon / trace / scenario |
| C · 侦听台系 | **Palette B** | listener + module-serial 的 `styles.css` |
| — | **Palette C** | module-serial.html 内联（`var(--x, #兜底)` 写法） |

> ⚠️ 注意 Palette C：本次 `:root` 扫描**没有抓到它**，因为它不是 `:root` 块，而是散落在内联样式里的
> `var(--x, #fallback)` 兜底写法。令牌齐全后这些兜底值永不生效，属噪音；
> 但清理时必须识别出来，否则会误判为"未接入"。

### 7.2 🚨 最高危：青绿语义三分歧（**会导致功能性错误，不只是美观**）

记忆原文：

> **青绿语义三分歧**（必须人工判定，不能脚本统一）：
> - B 系 = 交互主色 `--accent`
> - A 系 = 接收方向色 `--c-rx`
> - **模块日志页 = 接收；琥珀 = 发送（与侦听台相反）**

**这意味着**：模块日志页的发送/接收配色与侦听台**是反的**。

如果统一 token 时按侦听台语义一刀切（青绿=接收、琥珀=发送），
模块日志页的收发指示会**完全颠倒**——用户会把发送帧读成接收帧。

**这是本轮改造中唯一一处"改错会造成误判现场数据"的风险点。**
对策：语义合并必须由你人工逐页确认，禁止脚本批量统一。TODO.md 中该步已单独标记为需人工确认。

### 7.3 色值映射算法（若要写脚本批量处理）

记忆中已踩过并修正的三条：

1. **饱和度必须用 HSV 的 `(max-min)/max`**。HLS 饱和度对浅色/极暗色虚高，
   会把 `#cbd8e3`（浅灰）、`#081420`（近黑底）、`#142433`（描边）误判成蓝色。另加 `value < 70` 亮度门槛。
2. **绿/青绿分界取 165°**。取 150° 会把通过色 `#34d399` 误判为接收色。
3. **脚本绝不能处理 `:root` 里的自定义属性定义行** —— 否则会把 `--ink`（主文字色）映射成背景色，
   造成"深背景上的深字"完全不可读。
4. **近黑文字色**（如 `#04222b`，亮色渐变按钮上的反色字）需单独规则：`y < 0.03 → --accent-fg`，
   否则会被归到 `--fg-faint` 几乎不可读。

### 7.4 主题广播机制

子页必须监听 **`"message"` 事件，再按 `e.data.type` 过滤**。
直接 `addEventListener("wb-theme-change")` 收不到 —— 这是 `maintenance.html` 切主题不跟随的根因，已在 P5 定位。

---

## 8. 建议的下一步

1. **先拍板 D4 主色**（`#22d3ee` / `#06b6d4` / `#45e0c2`）——这决定后续所有配色的锚点
2. **批准 P0 四项**（约 1.5h）——不动架构，立竿见影，且能为 P1/P2 验证方向是否正确
3. P0 上线后目视确认效果，再决定是否投入 P1/P2

**我不建议跳过 P0 直接做 P1/P2**——架构重构是 3-5 天的投入，应该先用 1.5 小时验证"去掉渐变 + 统一主色"这个方向是否真的是你想要的。方向错了，返工成本是十倍。

**另一个必须先做的决定**：本轮是否沿用「做完待 commit」的习惯？
上一轮 P5 就是因为这个习惯 + `apps/` 事故，导致一整天的工作归零。
如果本轮改为逐页 commit，即使再出事故，损失也可控在一页之内。
