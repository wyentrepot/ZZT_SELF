# TODO — REQS-0012 工作台主题系统重构

> 每次进度变更**只追加不覆盖**；完成项打 `[x]` 并注明日期。
> 详细诊断见 `ASSESSMENT.md`，改造步骤见 `REFACTOR-PLAN.md`。

## 阶段 0 · 评估（✅ 2026-08-30 已完成）

- [x] 通读 `tokens.css` / `styles.css` / `app.js` / `workbench.html` / `index.html`
- [x] 扫描全量硬编码颜色与 `:root` 定义点，定位 3 套方言
- [x] WCAG 2.1 对比度实测（21 组，4 FAIL + 2 WARN）
- [x] 产出 `ASSESSMENT.md`（七维度评分，总分 3.4/10）
- [x] 产出 `REFACTOR-PLAN.md`（三层架构 + 别名层迁移 + 分期）
- [x] 建单并登记 `REQS-INDEX.md`

## 阶段 1 · P0 立竿见影（✅ 已完成 2026-08-30 19:37）

> 不触碰架构，可独立验证、独立回滚。**按用户要求逐页 commit**，共 11 个提交。

- [x] **D1 拍板统一主色 = 蔚蓝科技风（`#06b6d4`）** ✅ 已定
      — 即 A 方言 `theme-deepblue`「深蓝科技」调性，P1 统一三方言时以此为锚
- [x] 消除双层渐变：`tokens.css` `--frame-bg` 由 `transparent` → `var(--bg-page)`（`460f0e8`）
- [x] 消除双层渐变：`styles.css` 父页 body → `--bg-page`（`6dce91e`）
- [x] 消除双层渐变：`workbench.html` 子页 body → `--bg-page`（`bf61e4a`）
- [x] 消除双层渐变：`maintenance.html` / `serial-profile.html`（`9a92757` / `fc89667`）
- [x] 9 个页面 + 外壳补 `color-scheme: dark`（`tokens.css` 通用区 + 各私有 `:root`）
- [x] inconclusive 独立紫色相 `#a371f7`（`--st-inconclusive*`，兑 REQS-0011 §4.6 铁律，`460f0e8`）
- [x] FOUC 修复：10 个 HTML 的 `<head>` 内联防闪跳脚本（带主题白名单校验）
- [x] 静态校验：`verify-p0.js`（已归入本需求目录）— 11 页 / 语法全 OK / 覆盖率全 Y

### P0 提交清单（逐页，便于单页回滚）

| # | commit | 内容 |
|---|--------|------|
| 1 | `460f0e8` | tokens.css：frame-bg 实心化 + inconclusive 独立紫 + color-scheme |
| 2 | `6dce91e` | 外壳：styles.css 去渐变 + index.html 防闪跳 |
| 3 | `bf61e4a` | workbench.html：去渐变 + color-scheme + 防闪跳 |
| 4 | `b306cb5` | trace.html（B 系） |
| 5 | `48a97bc` | dict.html（B 系） |
| 6 | `dfb25c4` | scenario.html（B 系） |
| 7 | `147db57` | simcon.html（B 系） |
| 8 | `57cde2c` | listener/index.html（C 系，仅加脚本未动逻辑） |
| 9 | `f35b5c6` | module-serial.html（C 系，仅加脚本未动逻辑） |
| 10 | `9a92757` | maintenance.html（去渐变 + scheme + 防闪跳） |
| 11 | `fc89667` | serial-profile.html（去渐变 + scheme + 防闪跳） |

### P0 遗留待决

- [x] **目视验收**（用户本地起服务确认效果）—— ✅ 2026-08-30 已批准
- [x] `preview/index.html` 不纳入产品交付：保留为本地独立样板，`preview/` 已从 Git 跟踪与生产静态校验中排除。
- [x] `tokens.css` 残留 4 处 `--bg-gradient` 定义（已无引用），P1 清理（`c6505a6`）

## 阶段 2 · P1 架构地基（~1 天）

- [x] 建 `tokens-v2.css`：primitive 层（原始色板）（`b355fc2`）
- [x] 建 `tokens-v2.css`：semantic 层（`--color-*` 语义角色）（`b355fc2`）
- [x] 建 `tokens-v2.css`：component 层（`--btn-*` / `--card-*` / `--table-*` 槽位）（`b355fc2`）
- [x] 建 `compat-dialects.css`：B 系（`--bg-0..4` / `--tx-1..4` / `--ac` / `--am`）别名映射（`54ba9df`）
- [x] 建 `compat-dialects.css`：C 系（`--canvas` / `--panel` / `--ink` / `--cyan`）别名映射（`54ba9df`）
- [x] 别名层头部标注废弃计划与删除条件（`54ba9df`）
- [x] 主题机制 `className` → `data-theme` 属性（`app.js:214` / `workbench.html:395`）（`873d543`、`48185bd`）
- [x] `postTheme()` 不再反向读 `className`（`app.js:55`）（`873d543`）
- [x] 对比度修正：`--fg-dim` 3.44 → ≥4.5（`b355fc2`）
- [x] 对比度修正：`--tx-4` 2.36 → ≥4.5（`b355fc2`）
- [x] 对比度修正：`--faint` 3.04 / 3.17 → ≥4.5（`b355fc2`）
- [x] `styles.css` 的 7 处魔数收编进 spacing / font-size token（`2cf7e20`）
- [x] 对比度回归：`python contrast-v2.py` 门禁 50 组 / FAIL 0（`f72e93e`；GBK 控制台回归由 `47e758c` 覆盖）

> P1 状态：✅ 已完成（2026-08-30）。7 个逐步提交为 `b355fc2`、`54ba9df`、`873d543`、`48185bd`、`2cf7e20`、`c6505a6`、`f72e93e`。

## 阶段 3 · P2 全面接入（~2-3 天，高风险，逐页灰度；🚧 进行中）

- [x] 接入 · 外壳 `index.html` + `styles.css`（`af9e2a9`）
- [x] 接入 · 验证工作台 `workbench.html`（`af9e2a9`）
- [x] 接入 · 串口配置 `serial-profile.html`（`0c5094b`）
- [x] 接入 · 工作台状态 `maintenance.html`（`5706922`）
- [x] 接入 · 报文追踪 `trace.html`（B 系）（`6bc0eb7`）
- [x] 接入 · 协议字典 `dict.html`（B 系）（`c248cd6`）
- [x] 接入 · 场景脚本 `scenario.html`（B 系）（`cde81ce`）
- [x] 接入 · 模拟集中器 `simcon.html`（B 系）（`d815650`）
- [x] 接入 · 模块日志 `module-serial.html`（C 系，**高风险**）（`af3f5a8`）
- [x] 接入 · 侦听台 `listener/index.html`（C 系，**最高风险**，最后做）（`2559b9f` 嵌入版 / `e95fc2c` 独立版）
- [x] **P5 语义收发方向已拍板**（不再设置人工语义门禁）
      — RX=青绿（`--color-dir-rx`），TX=琥珀（`--color-dir-tx`）；各生产页面按同一语义执行。
- [x] 🚨 ~~人工确认 · 近黑文字色~~ —— 用户 2026-08-30 拍板：**文字色不重要，能看就行**，不单设人工门禁
- [x] 清理 Palette C：失效兜底值 `var(--x, #兜底)` 全仓扫描已归零（2026-08-30 复核）
- [x] 主题注册改为单一数据源 —— `tokens-v2.css` 的 `--theme-registry`，JS/HTML 均改读它（`0318e7f`）
- [x] 建主题覆盖率断言：切换 `data-theme` 后 body 背景色应变化（`0318e7f`）
- [x] 10 页断言全绿 + commit —— 门禁 `10 个生产页面 / 0 issues`（另有 1 项 color-mix 需人工确认）

### 阶段 3 补充说明

- **simcon 的 5 条 TX issue 系门禁误报**，已查实并处理：
  `.tx-row` 实为 **traffic row**（收发共用的行容器），`simcon.js:277` 对每帧无条件赋值、
  不判方向；方向由行内徽章 `.dir.tx` / `.dir.rx` 承载，二者早已正确引用方向 token。
  若按误报字面塞入 `--color-dir-tx`，会把所有 RX 行也刷成发送色 = 方向误标。
  处理：容器改名 `.tr-row` / `.tr-main` / `.tr-det`，与同文件 `.traffic` / `.tr-h` /
  `.tr-body` 命名族对齐。**规则体一字未动，视觉零变化。**

> 阶段 3 状态：✅ 已完成（2026-08-30）。

## 阶段 4 · P3 收敛（~2 天，并入 REQS-0010 P5；✅ 2026-08-30 已完成）

- [x] 主题收敛为 2 套：墨夜深色（默认 `midnight`）+ 晴昼浅色（`daylight`）
      —— `--theme-registry: "midnight|墨夜深色|🌙,daylight|晴昼浅色|☀️"`
- [x] 晴昼浅色主题覆盖 semantic 层（`tokens-v2.css:208`）
- [x] 支持 `prefers-color-scheme` 自动跟随 —— **11 个页面**（10 生产 + 独立版侦听台）
      head 脚本均已带 `matchMedia` 分支，无本地偏好时按系统深浅自动选主题
- [x] 逐页把方言名替换为标准语义名 —— B 系 / C 系方言变量全仓残留**归零**
- [x] 删除 `compat-dialects.css`（`33d87f4`）—— 零引用死文件，删除条件已满足
- [x] ~~删除旧 `tokens.css`~~ —— 用户 2026-08-30 拍板：**保留文件**。
      理由：`check_assets.py:29` 与 `test_app.py:295` 仍将其列为关键资产，
      删除须同步改这两个 Python 清单，收益不抵风险
- [ ] 与 REQS-0010 P5 合并收口 —— 跨需求，挂起等 P5（关联 D3）

## 阻塞项

| # | 事项 | 阻塞了 |
|---|------|--------|
| D1 | 统一主色 | ✅ 已拍板为蔚蓝科技风 `#06b6d4`，P1 已按此为锚完成 |
| D2 | P0 目视验收 | ✅ 2026-08-30 用户验收通过，P1 已完成；P2/P3 按当前计划推进 |

## 2026-08-30 对账记录

- P0 目视验收：用户已批准。
- P0 历史记录：生产静态校验 10 页，0 issues；`preview/` 为本地忽略的独立样板，不计入生产页面。
- 当前 P2 覆盖率门禁：待 9 个子页面完成接入后复验；当前仍有遗留变量、旧样式引入与 raw color issues，不能据此宣布 P2/P3 完成。
- 对比度门禁（P1 历史记录）：50 组 / 0 FAIL（观察区弱对比项留待 P2 处理）。
- P5 语义结论：RX=青绿（`--color-dir-rx`），TX=琥珀（`--color-dir-tx`）；不再保留人工语义门禁。

## 2026-08-30 收口对账（P2 / P3 完成）

**门禁**：`10 个生产页面 / 0 issues`，exit 0。另有 1 项需人工确认：
`pages/module-serial` 的 body 光晕层含 `color-mix()`，无法静态求值；底色层已判定
随主题变化（midnight `#0a1628` / daylight `#f4f6f9`）。

**对比度回归**：`contrast-v2.py` **50 组 / FAIL 0**（观察区 24 组 / ⚠ 7，不计入门禁）。
P2 近千行改动未破坏可达性。

### 发现并修掉的真实 bug

1. **侦听台嵌入版防闪跳脚本已与体系脱节**：白名单是 4 套废弃主题名
   （`theme-deepblue` / `theme-emerald` / `theme-charcoal` / `theme-indigo`）+
   `className` 机制。新存的 `midnight` / `daylight` 进不了白名单 → 防闪跳形同虚设；
   旧值反而会挂上一个新体系根本不读的垃圾 class。已改为 registry 驱动 +
   `data-theme` 属性 + `prefers-color-scheme` 跟随。
2. **simcon `.tx-row` 命名歧义**：实为 traffic row（收发共用），导致门禁误报 5 条，
   且字面误导后来人。已改名 `.tr-row` 族，与同文件 `.traffic` / `.tr-h` / `.tr-body` 对齐。

### 提交清单

| commit | 内容 |
|--------|------|
| `af9e2a9` | 外壳（index / styles / workbench）接入 |
| `6bc0eb7` `c248cd6` `cde81ce` | trace / dict / scenario（B 系） |
| `0c5094b` `5706922` | serial-profile / maintenance |
| `af3f5a8` | module-serial（C 系） |
| `d815650` | simcon 接入 + traffic row 改名 |
| `33d87f4` | 删除 compat-dialects.css |
| `2559b9f` `e95fc2c` | 侦听台嵌入版 / 独立版接入 |
| `0318e7f` | 门禁脚本与测试纳入版本控制 |

### 遗留（不阻塞，待定夺）

| # | 事项 | 说明 |
|---|------|------|
| L1 | 侦听台 `app.js` 内 16 处硬编码色 | canvas 趋势图与两处内联状态色在晴昼浅色下不跟随主题（深色底 + 亮线观感不佳）。修法是 JS 读 `getComputedStyle` 取 token 值再喂给 canvas，属 JS 逻辑改动，已越过本次「只改 CSS/HTML」红线 |
| L2 | `rating-degraded` / `legend-dot.pending` 用 `--color-dir-tx` | 语义上属「健康度 / 待定状态」，严格该用 `--color-status-warn`。当前保与 module-serial 的视觉一致（同为琥珀）。改 3 行（1222 / 1228 / 1285），但会与 module-serial 产生视觉差异，建议两页一起改 |
| L3 | `simcon.html:133` `.s-A` 用 `--color-dir-rx` | 是十六进制报文**地址域**的语法高亮色，非收发方向。系 P2 忠实 1:1 迁移（原 `--rx-c`），非新引入错误。宜新增语法色 token，超出本次范围 |
| L4 | `apps/listener/test_ui_layout.py` 3 条失败 | 已用 HEAD 版本双向验证为**改造前既有**，与本次无关：① 要 `class="operation-panel"`，实际 `operation-panel loader-panel`；② 要 `?v=serial-v2`，实际 `?v=frames-pro`；③ 要 `height: calc(100vh`，而 styles.css 中 0 次。属测试与实现脱节 |
| L5 | `_tmp_theme_audit/` `_tmp_frontend_audit/` 未被 .gitignore 覆盖 | `.gitignore` 只有 `tmp/`，匹配不到 `_tmp_` 前缀。属临时审计产物，会污染 `git status`。改 .gitignore 是全仓共享配置，未擅自改 |

### 工程提醒

本仓 CSS 为 **CRLF**。用脚本做多行精确匹配（模板字符串）会全部失配，必须先统一
`\r\n → \n`，写回前再转回。本次侦听台迁移在此坑上返工过一轮。
