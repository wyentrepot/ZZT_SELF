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
- [x] 对比度回归：`python contrast.py` 应为 0 FAIL（`f72e93e`；门禁 50 组 / FAIL 0）

> P1 状态：✅ 已完成（2026-08-30）。7 个逐步提交为 `b355fc2`、`54ba9df`、`873d543`、`48185bd`、`2cf7e20`、`c6505a6`、`f72e93e`。

## 阶段 3 · P2 全面接入（~2-3 天，高风险，逐页灰度）

- [ ] 接入 · 外壳 `index.html` + `styles.css`
- [ ] 接入 · 验证工作台 `workbench.html`
- [ ] 接入 · 串口配置 `serial-profile.html`
- [ ] 接入 · 工作台状态 `maintenance.html`
- [ ] 接入 · 报文追踪 `trace.html`（B 系）
- [ ] 接入 · 协议字典 `dict.html`（B 系）
- [ ] 接入 · 场景脚本 `scenario.html`（B 系）
- [ ] 接入 · 模拟集中器 `simcon.html`（B 系）
- [ ] 接入 · 模块日志 `module-serial.html`（C 系，**高风险**）
- [ ] 接入 · 侦听台 `listener/index.html`（C 系，**最高风险**，最后做）
- [ ] 🚨 **人工确认 · 青绿语义收发方向**（禁止脚本批量统一）
      — P5 结论：模块日志页 青绿=接收 / 琥珀=发送，与侦听台**相反**。
      统一错会把发送帧显示成接收帧，属**功能性错误**。详见 `REFACTOR-PLAN.md` §7.2
- [ ] 🚨 **人工确认 · 近黑文字色**（`y < 0.03` 者单独映射到 `--accent-fg`，勿归入 `--fg-faint`）
- [ ] 清理 Palette C：`module-serial.html` 内联 `var(--x, #兜底)` 的失效兜底值
- [ ] 主题注册改为单一数据源（消除 CSS / JS `THEMES` / HTML `.theme-dot` 三处同步）
- [ ] 建主题覆盖率断言：切换 `data-theme` 后 body 背景色应变化
- [ ] 9 页断言全绿 + commit

## 阶段 4 · P3 收敛（~2 天，并入 REQS-0010 P5）

- [ ] 主题收敛为 2 套：墨夜深色（默认）+ 晴昼浅色
- [ ] 晴昼浅色主题覆盖 semantic 层
- [ ] 支持 `prefers-color-scheme` 自动跟随
- [ ] 逐页把方言名替换为标准语义名
- [ ] 删除 `compat-dialects.css`
- [ ] 删除旧 `tokens.css`
- [ ] 与 REQS-0010 P5 合并收口

## 阻塞项

| # | 事项 | 阻塞了 |
|---|------|--------|
| D1 | 统一主色 | ✅ 已拍板为蔚蓝科技风 `#06b6d4`，P1 已按此为锚完成 |
| D2 | P0 目视验收 | ✅ 2026-08-30 用户验收通过，P1 已完成；P2 仍待人工语义门禁 |

## 2026-08-30 对账记录

- P0 目视验收：用户已批准。
- 生产静态校验：10 页，0 issues；`preview/` 为本地忽略的独立样板，不计入生产页面。
- 对比度门禁：50 组 / 0 FAIL（观察区弱对比项留待 P2 人工决策）。
