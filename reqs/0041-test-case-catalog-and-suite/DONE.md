# DONE.md — 需求 0041 完成日志（只追加，最新在上）

## 2026-11-23 ｜ 基线重定 v3（用户口述定稿：拆最小块 + 事实前置）

**完成内容**
- `REQS.md` 追加**变更 3** → 基线 **v3**；新增 §10「原子块 + 事实 + 编排」四层模型为当前生效正文，
  并逐条列明被取代的 v2 条目与继续有效条目。
- `PLAN.md` **整体重写**为 v3（旧 v2 计划标失效、不留可执行内容）；
  `TODO.md` 按 v3 的 P0-P6 重排。

**用户三项关键决策（本会话拍板）**
1. **模型**：测试用例拆分到最小块，用例只做编排；采纳「块 + 事实」两层（用户选「推荐」项）。
2. **组网判据**：**只用 1376.2 帧**（10H-F1 在网数 + 02H-F1 内层 645 地址），**不接入侦听台**。
3. **实施顺序**：**先铺齐全部原子块，再编排用例**；22 例一次性全迁；UI 本轮做。
   另修正前一轮判断：执行面为**在线为主**（用户原话「离线的话没串口怎么做」）。

**本次实测取证（全部写入 REQS §10.7，非推测）**
- **事实 D**：`encode_app_data` 枚举出 **30 个**下行无参可构帧 `(AFN,FN)`；10 个原子块带参构帧实跑出 hex
  （如加档案 `6817004300000000000111010001010000000000025A16`）。
- **事实 E**：**上行 02H-F1 内嵌 645 递归解析通过**，能取到内层地址域 `000000000001`
  → 「STA 拿到表地址」有了帧级判据，**无需侦听台**。这是本次最关键的能力确认。
- **事实 F**：`join_anhui`/`search_meter`/`open_close` 三个场景引用的 `tasks/*.json` **不存在**（`os.path.exists` 实测 False）
  → 组网编排断链，不能复用现有 scenarios。
- **事实 G**：`network_assert.evaluate()` 直接 `raise NotImplementedError`；因不接侦听台转为**非目标**。
- **事实 H**：改前基线 `550 passed / 1 failed`，唯一红是 `test_10f104_up_unsupported`，
  源于提交 `846a7c1` 改 10H-F104 编码后漏更测试（HEAD 已红），**与 0041 无关，本需求不修**。
- **事实 I**：`11H-F5` 构帧必填 `time6/duration/retry/slices` 四参。
- **事实 J**：`15H-F1` 能构单帧，但仓库**无分片状态机**（recipes/scenarios/UI 皆无升级编排）→ BL-6 为新增实现。
- **事实 K**：22 例依赖分布 —— simcon 19 / simeter 3 / `shell_ro` 外部脚本 1 / 完全离线 1。

**架构决策**
- 四层：Block（原子块）→ Fact（事实）→ Case（编排）→ Suite（方案）；结论模型复用
  `test_automation.models.AssertionResult`，**不新造第三套**。
- 防耦合落为**三条可测铁律**（`test_layering.py` AST 断言）：内核不 import 能力/领域库；
  能力适配器互不 import；`libs/` 不 import `apps/`（当前 0 命中，须保持）。
- 块/事实/用例/方案**均为 JSON**：加块 = 加 JSON，Python 零改动。
- 入口层零测试逻辑；UI 只消费 `/api/cases/*`。

**边界（写入 REQS §10.6）**
- 不接侦听台、不接模块日志事件作组网判据
- 升级只做 15H-F1 分片编排 + 帧级回验，**不做真固件烧录/激活**（属 `wb-flash` 领域）
- 不实现 `network_assert.evaluate()`
- 97 条 gw_cass 声明式用例的 `declarative` 断言求值**降为可选**（v2 技术核心不再属本需求主线）
- 不改 `case_library` / `test_automation` / `/api/autotest/*`

**遗留 / 风险**
- 块粒度需在 P2 实操中校准，避免块数膨胀（REQS §10.9）。
- 15H-F1 分片时序（等段确认/超时重发）先做无重发最小闭环，重发策略作为块参数。
- P1-P6 待实施，见 `TODO.md`；5 个检查点见 `PLAN.md` 尾部。

---

## 2026-11-23 ｜ 立项收口（P0 完成）

**完成内容**
- `REQS.md` 基线 **v2** + `PLAN.md`（355 行，P0-P6 分阶段）+ `TODO.md`（P0 已勾）+ 本 `DONE.md`；`REQS-INDEX.md` 已登记。
- **基线演进**：v1 立项 → 写 PLAN 时实测发现三条硬事实，按只追加纪律追加「变更 2」升 v2，并新增 RU-7 能力分级。

**关键实测（决定了实现方式，非推测）**
- 事实 A：迁移出的 97 条 `CasePackage` **没有激励字段**（`source/firmware/flash/boot/observe/cleanup` 全 None），`CasePackage` 模型本身也无 `stimulus`。→ assertions 只说「期望看到什么」，不说「该发什么」。
- 事实 B：激励可提取 **84/97（86.6%）**；有 assertions **70/97（72.2%）**；13 条无法提取（如 `gw_cass_79 抄表功能`、`gw_cass_67 负荷曲线`）。→ 必须做能力分级，禁止假 PASS。
- 事实 C：`pytest libs/gw_cass_migrate` = `10 passed` 已实测；路由沿用 `app.py:492` 工厂模式；`grep '^from apps' libs/` = `0` 已达标。

**架构决策**
- 三层模型 Case→Suite→Run；`libs/case_catalog` 只做数据与校验，执行一律经 `backends/`（可插拔）。
- 方案存 JSON（`data/case_suites/*.json`）不存 DB，理由：需在 git 中可 diff「谁把哪个用例加进了哪个方案」。
- `case_library`（只读字典 279 条）不改，与 `case_catalog`（可执行目录）职责分离。
- `declarative` 后端拆为「激励提取器 + 断言求值器」两个可独立测试单元（v2 修正）。

**遗留 / 风险**
- `declarative` 断言表达力依赖从中文「预期结果」正则提取，易失真 → PLAN 已定「先做样板人工校准，失败标 unsupported」。
- 两套 API（`/api/autotest` 与 `/api/cases`）将并存，PLAN 已定 `/api/cases` 为新权威、下一需求移除旧 API。
- P1-P6 待实施，见 `TODO.md`；5 个检查点见 `PLAN.md` 尾部。
