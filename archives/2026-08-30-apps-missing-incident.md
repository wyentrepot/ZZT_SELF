# 事故记录：`apps/` 目录消失（2026-08-30）

> 记录时间：2026-08-30 18:45 左右
> 记录人：WorkBuddy（在推进 REQS-0010 P5 期间发现）
> **状态：未解决，等待人工介入**

---

## 一、现象

REQS-0010 P5（主题体系重构）T402–T408 全部做完后，准备跑 `pytest apps/workbench` 回归时，发现
**`D:\2-侦听台改造\apps\` 整个目录不存在**，其下 131 个 git 追踪文件全部被 `git status` 标记为 `D`（deleted）。

同时消失的还有**本轮 P5 的全部未提交改动**（项目习惯为"做完待 commit"，故 HEAD 里只有 P5 之前的版本）。

## 二、影响范围

| 项目 | 内容 |
|------|------|
| 丢失目录 | `apps/`（含 `workbench`、`listener`、`module_log` 三个子应用） |
| git 追踪文件 | 131 个（130 个 unstaged `D` + 1 个 staged `D`） |
| 分布 | `apps/workbench` 79、`apps/listener` 30、`apps/module_log` 18，另有中文名文件若干 |
| 完整清单 | 见同目录 `2026-08-30-apps-lost-files.txt` |
| **未丢失** | `reqs/`、`ui-demo/`、`docs/`、`dist/`、`libs/`、`tools/`、`REQS-INDEX.md` 等其他 15 个顶层目录均正常 |

## 三、已排除的原因

| 排查项 | 结果 |
|--------|------|
| 我自己执行的删除命令 | 仅两条：`rm -rf _tmp_p5_tokenize`（临时目录，路径明确）与 `git rm apps/workbench/static/preview/index.html`（单文件）。均无法解释整个 `apps/` 消失 |
| 改名 / 移动 / 隔离目录 | 仓库根目录 48 个条目全枚举过，无 `apps*`、`*bak*`、`*quarant*` 等痕迹 |
| shell 可见性故障 | 三条独立 I/O 路径一致：Git Bash `ls`、Python `os.path.exists`、git 本身，均报告不存在 |
| git worktree 副本 | `.worktrees/p1-ai-observation/apps/` 存在，但 `tokens.css` 为 12301 字节 / Aug 23 / 0 处 `theme-midnight` → **P5 之前旧版**，救不回本轮改动 |
| `.build_plain/`（E-SafeNet 明文导出区） | `apps/` 存在，但 `tokens.css` 同样为 12301 字节 / 0 处 `theme-midnight` → **P5 之前旧版** |
| git stash | 空 |
| git 悬空对象 | 8 blob / 16 tree / 2 commit；逐个 `git cat-file` 搜索 `theme-midnight`，**0 命中**（因 P5 全程未 `git add`） |
| Windows Defender 隔离区 | 无内容 |
| 系统临时目录 | 无 `apps*` 残留 |

**未排除**：E-SafeNet 透明加密驱动异常（该环境特性：磁盘 `.py` 为密文，由驱动透明加解密）。
`apps/` 是本仓 `.py` 主目录，正是加密驱动管辖范围；`git rm` 删除其下文件时可能触发驱动的保护/异常处理，
导致整个目录解除映射。用户态未发现明确的 E-SafeNet 进程（可能在内核/服务层，`tasklist` 过滤不到）。

## 四、本轮丢失的 P5 改动清单（重建时需要）

> 基线：`reqs/0010-workbench-ui-landing/REQS.md` 变更 1（2026-08-30 用户拍板）
> 进度已同步到 `reqs/0010-workbench-ui-landing/TODO.md`（T402–T408 已勾选）与 `REQS-INDEX.md`

| 文件 | 改动要点 |
|------|----------|
| `apps/workbench/static/tokens.css` | **完全重写**，16405 字节 / 247 个变量。五段结构：0 通用尺度 / 1 `theme-midnight` / 2 `theme-daylight` / 3 组件令牌 / 4 别名层 / 5 工具类。深色 `--bg-page:#090c10`、`--accent:#22d3ee`；浅色 `--bg-page:#f2f5f9`、`--accent:#0e7490`、`--accent-fg:#ffffff`。`--c-inconclusive` 深 `#a371f7` / 浅 `#7c3aed`（跨需求铁律，REQS-0011 §4.6）。报文语义五色 `--seg-offset/addr/ctrl/data/crc`。别名层把 Palette A（`--bg-0`/`--tx-1`/`--ac`…）与 Palette B（`--canvas`/`--panel`/`--ink`/`--cyan`/`--line`…）映射到主令牌；补齐 `--ac-dim`/`--mono`/`--text` 三个此前未定义变量 |
| `apps/workbench/static/app.js` | `THEMES` 收敛为 2 套；新增 `THEME_FALLBACK`（旧 4 值 → `theme-midnight`）；新增 `normalizeTheme()` / `currentTheme()`（从 `classList` 中挑已知主题）；`switchTheme` 改用 `classList` 精确摘除而非整体覆盖 `className`；初始化时回写标准化后的 `localStorage` |
| `apps/workbench/static/index.html` | 主题圆点 4 个 → 2 个（`🌙 theme-midnight` 默认激活 / `☀️ theme-daylight`） |
| `apps/workbench/static/pages/maintenance/maintenance.html` | **修 bug**：`addEventListener("wb-theme-change")` → `addEventListener("message")` 再按 `e.data.type` 过滤（父壳走 `postMessage`，直接监听自定义事件名收不到）；`themeNames` 收敛为 2 套；说明文本 emoji 更新 |
| 6 页接入 tokens.css | `dict.html` / `simcon.html` / `trace.html` / `scenario.html` / `listener/index.html` / `module-serial.html`，均置于各自 `styles.css` **之前**；前 4 页删除了重复的本地 `:root` 调色板 |
| `pages/listener/index.html` | 内联 frames-pro 色板（`--pbg0..4`/`--pline1..3`/`--ptx1..4`/`--pac`/`--pam`…）重定向到全局令牌 |
| `pages/listener/styles.css`、`pages/module-serial/styles.css` | 整块删除原 `:root` 调色板（19 个硬编码变量），改由 tokens.css 别名层供给；批量映射 234 + 211 处，另手工补 12 + 9 处 |
| `pages/module-serial/module-serial.html` | 手工判定 47 处（语义分歧：青绿=`--c-rx` 接收、琥珀=`--c-tx` 发送、`#4cc2ff`=`--ch-cco`、`#f0a45a`=`--ch-sta`）；`rgba(...)` 转 `color-mix(in srgb, var(--x) n%, transparent)` |
| `pages/serial-profile/serial-profile.html` | 6 处：`#c0392b`/`#e74c3c` → `--c-fail`，`#27ae60` → `--c-pass` |
| 7 页注入主题跟随脚本 | dict / simcon / trace / scenario / listener / module-serial / serial-profile，位于 `tokens.css` 的 `<link>` 之后 |

### T407 验证结论（改动仍在时测得，重建后需复验）

- CSS 括号平衡 ✓；JS 语法 0 失败（独立文件 + 10 段内联）✓；悬空变量 0 ✓
- 9 个子页 HTTP 200 ✓；服务端返回内容 9 页均含 `tokens.css` 引用与 `wb-theme-change` 监听，旧主题名 0 残留 ✓
- **无头 Chrome 实测 41 个令牌在两套主题下全部翻转**（唯一不变的是 `--mono` 字体栈，属预期）

### T408 处置结论（**这部分未丢失**，已落盘）

- **保留**：`app.js` 的 `THEME_FALLBACK` 回退表（活代码，老用户 `localStorage` 旧值依赖它）——随 apps 丢失，恢复后需重建
- **删除**：`apps/workbench/static/preview/index.html`（全仓零引用的旧 4 套主题样板间，`git rm` 已 stage）——随 apps 丢失，但该文件本就计划删除，**恢复后需重新删除**
- **保留 + 标注**：`ui-demo/` —— 复核后确认**不是死代码**，而是 REQS-0011 的验收物证（`功能清单.md` 94 项是唯一设计基线，`_check.js`/`_smoke.js` 是验收证据）。且 Demo 为**自持单文件**、不引用生产 `tokens.css`，故其 4 套主题断言**仍全部有效**（`_smoke.js:281-283` 链路自洽：543 行圆点 → 1562 行 `THEMES` 表 → 1569 行写 `className` → 47 行 CSS 定义）。已在 `_smoke.js`、`_check.js`、`workbench-ui-demo.html`、`功能核对表.md`、`overview.md` 五处加「历史快照」说明 —— **这部分改动已落盘，未丢失**
- **不动**：`dist/工作台/_internal/`（打包产物，P5 范围排除）、`reqs/0011/` 历史记录（只追加不覆盖）

## 五、⚠️ 当前 git 状态与禁止动作

```
M  REQS-INDEX.md
M  reqs/0010-workbench-ui-landing/REQS.md
M  reqs/0010-workbench-ui-landing/TODO.md
M  reqs/0011-workbench-ui-redesign/REQS.md
M  ui-demo/_check.js
M  ui-demo/_smoke.js
M  ui-demo/overview.md
M  ui-demo/workbench-ui-demo.html
M  ui-demo/功能核对表.md
D  apps/workbench/static/preview/index.html        (staged)
 D apps/... ×130                                   (unstaged)
?? archives/2026-08-30-apps-missing-incident.md    (本文件)
?? archives/2026-08-30-apps-lost-files.txt
?? reqs/0010-workbench-ui-landing/DONE.md(?)       → 以实际 status 为准
```

**🚫 严禁执行**（会把 131 个删除写进历史，或覆盖可能尚存的数据）：

- `git commit -a` / `git add -A` / `git add apps`
- `git checkout HEAD -- apps/` —— **若驱动只是临时故障、文件仍在磁盘上，此命令会用 P5 前旧版强行覆盖工作区**；
  且它将不可逆地抹掉任何可能找回的机会
- 任何 `git gc` / `git prune`

**✅ 相对安全**：`git status`、`git diff`、`git show HEAD:apps/...`（只读）

> 提示：普通 `git commit`（不带 `-a`）只会提交已 staged 的内容，当前 staged 的只有 preview 那一条删除。
> 但这仍会把一条"删除"写进历史，建议在 apps 恢复前**完全不要 commit**。

## 六、建议的下一步（待用户拍板）

1. **先人工确认** —— 用 Windows 资源管理器打开 `D:\2-侦听台改造\`，看 `apps` 是否可见。
   若资源管理器（授信进程）能看到而命令行看不到，即可确诊为 E-SafeNet 驱动策略问题。
2. **若为驱动问题** —— 尝试重启 E-SafeNet 客户端 / 重新登录加密策略 / 重启系统，再检查 `apps` 是否回来。
   （涉及系统级动作，按用户既定规则须经本人确认后执行）
3. **确认无法找回后** —— 从 HEAD 恢复 P5 前基线：`git checkout HEAD -- apps/`，
   然后按第四节清单**重做 P5**（其中 `tokens.css` 需完全重写，工作量最大）。
4. **恢复后补做 T408** —— 重新 `git rm apps/workbench/static/preview/index.html`。
