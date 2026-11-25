# REQS-0038 — 工作台静态页未定义背景令牌（透明叠加）存量收口 + 令牌静态校验 + UI E2E 串口前置

> 状态：🚧 已立项（v1.0，2026-11-05）
> 创建：2026-11-05
> 来源：用户审阅"自动测试"页截图发现抽屉文字拥堵叠影（06-drawer-result.png）；排查定性为
> CSS 令牌硬 bug 而非截图伪影（静置 1.5s 复现同一叠影，/tmp/req-ui-evidence/x-drawer-steady-2.png）。
> 用户决策：**只记录问题，暂时不改，用新 req 记录**（autotest 页 8 处为排查过程中已顺带修复并复验，
> 不回退；其余全部挂起待排期）。
> 关联：REQS-0012（tokens-v2 主题系统，背景令牌改名 canvas/surface/raised/elevated/overlay）、
> REQS-0031（软件化外壳）、本次会话沉淀的 tools/ui_e2e/（浏览器真实操作套件）、
> apps/workbench/static/pages/autotest/（新页面，另案）

## 1. 问题清单（全部只记录、未实施）

### P1 · 存量页面引用未定义令牌 `--color-bg-default`（同款透明 bug 遗留）

- 根因：REQS-0012 把背景令牌改为五级实心（canvas/surface/raised/elevated/overlay），
  **`--color-bg-default` 已不存在**；`var()` 引用未定义变量在 computed-value 阶段失效，
  background 回退 **transparent**，叠加层（position:fixed 抽屉、sticky 面板）出现下层内容透显。
- 全仓核对（2026-11-05 逐页 grep）：`apps/workbench/static/pages/*/*.html` 中
  - autotest.html：7 处 default + 1 处 `--color-bg-inset`（同为未定义）→ **本次已修**（surface/raised/overlay/hover 分层替换，墨夜+晴昼双主题浏览器复验通过，证据 x-drawer-midnight-fixed.png）；
  - **simeter.html：2 处 `var(--color-bg-default)` 未修**（topbar 等），是本 req 的主要待办；
  - 其余 8 页（simcon/maintenance/scenario/serial-profile/dict/listener/trace/module-serial）：0 处，干净。
- 风险面：simeter 页 topbar 为 sticky，滚动时表内容会透出顶栏；页内如有叠加面板同理。
  当前仅因无强叠加场景未被发现，属**潜伏渲染缺陷**。

### P2 · 令牌静态校验缺失（防同类回归）

- 现状：无任何检查保证 `static/pages/*.html|*.js` 中 `var(--x)` 均在 tokens-v2.css 定义；
  未定义变量**静默透明/失效**，无报错、无测试拦截，本次靠人眼截图才发现。
- 建议方向（二选一或并行）：
  a) 新增 `tools/scripts/verify_static_tokens.py`：提取页面全部 `var(--x)` 与 tokens-v2.css
     定义集比对，未定义且无回退 → 报错（沿用 verify_skills.py 的输出/退出码风格）；
  b) 接入现有 pytest/CI（如 test_app.py 增一条静态扫描用例）。
- 附带建议：把 `--color-bg-default` 加入令牌表作为显式 alias 并注释"已废弃，勿用"，
  或不加——由校验器拦截即可（倾向后者，避免给废弃名续命）。

### P3 · UI E2E 前置依赖 simcon 开串口，与"串口用完即关"红线冲突

- 现象：tools/ui_e2e/ 套件的 CCO 类步骤依赖 simcon 已开串口；上一轮收尾按红线
  `POST /api/simcon/close` 关闭后，本轮重跑实测 `cco_03f1_vendor_version` 0/1、
  组跑 smoke 2/3 FAIL（simeter_handle_hex_645 不依赖串口所以 PASS）。UI 如实展示 FAIL，
  行为正确，但**套件不自洽：跑过的环境收尾后无法直接复跑**。
- 建议方向：ui_e2e 增加 preflight——开跑前查 `/api/simcon/status`，未开则提示或自动
  open，套件结束恢复进入前状态（开了才关）；README 前置一节同步更新。
- 本轮重跑被用户中止，simcon 维持关闭状态；10 步套件级证据（01-10 截图）为串口开启期
  所摄，抽屉修复后的套件级复验待 P3 落地时一并补。

## 2. 用户诉求（原话归纳）

"只记录问题，暂时不改，用新 req 记录。"——即：立本 req 收口上述三类问题；除已完成的
autotest 页修复外，**不实施任何代码改动**；simcon 不重开、套件不重跑。

## 3. 验收标准（排期时逐条对账）

1. simeter.html 2 处未定义令牌替换为正确分层令牌，双主题浏览器截图复验无透显；
2. 令牌静态校验落地（脚本或 pytest 用例），对全部 static/pages 生效，且当前全仓 0 违规；
3. ui_e2e preflight 落地：simcon 关闭状态下可直接跑通全套 10 步 + 组跑，结束态与进入前一致；
4. REQS-INDEX 本行状态更新为 ✅ 并在 DONE.md 留证据路径。

## 4. 证据索引

- 叠影首现：/tmp/req-ui-evidence/06-drawer-result.png（⚠️ /tmp 易失，排期时先拷贝归档）
- 静置复现（排除截图时机）：/tmp/req-ui-evidence/x-drawer-steady-2.png
- autotest 修复后（墨夜实色）：/tmp/req-ui-evidence/x-drawer-midnight-fixed.png
- P3 失败实测：ui_test.py "汇总 0/1 PASS · exit=1"、ui_group_test.py "汇总 2/3 PASS · exit=1"
  （2026-11-05 会话输出）；results 流水 /01-workfile-ai/01-zzt/autotest/results/index.jsonl
- 令牌改名依据：apps/workbench/static/tokens-v2.css §140/§208 两主题背景五级定义
