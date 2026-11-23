# TODO.md — 需求 0041 交付/阶段状态

> 基线：`REQS.md` **v3**（2026-11-23，变更 3 + §10「原子块 + 事实 + 编排」四层模型）。
> 一个阶段验收通过才勾 `- [x]`；全部完成追加到 `DONE.md`。
> 细粒度实现步骤见 `PLAN.md`（v3，已按新基线重写），本文件只记交付状态。

> **已知红（不属本需求，不修）**：`libs/parser_lib/adapters/adapter_10376/tests/test_10376_full_coverage.py::test_10f104_up_unsupported`
> —— 提交 `846a7c1` 改 10H-F104 编码后漏更测试，HEAD 上已红。P0 门禁以此为准。

## P0：开工门禁（改前基线固化）
- [x] REQS.md v1 落盘；REQS-INDEX 登记
- [x] 现状证据：四层资产不连通、执行器在仓库外、UI 勾选不持久、97 条声明式用例无执行器
- [x] 与 Auto-CI-test 关系核实（本机 GW-CASS/TestSce.json 同源，已有 gw_cass_migrate）
- [x] 变更 3 落盘 → 基线 **v3**（用户口述定稿：拆最小块 + 事实前置）
- [x] PLAN.md 按 v3 重写（旧 v2 计划标失效）
- [x] 改前基线实测取证：事实 D/E/F/G/H/I/J/K 全部写入 REQS §10.7
- [ ] 开工前复核 P0-1 ~ P0-5 门禁命令

## P1：内核 `libs/case_runner/core/`（零领域依赖）
- [ ] `models.py`：`BlockSpec` / `FactSpec` / `CaseSpec` / `SuiteSpec`
- [ ] `registry.py`：块/事实注册表
- [ ] `planner.py`：事实 DAG 求值 → 未满足则下游块 skipped
- [ ] `executor.py`：顺序执行 + 聚合断言 + 落 `AssertionResult`
- [ ] **`test_layering.py`：三条铁律 AST 断言**（本需求防耦合核心）
- [ ] 门禁：`grep -rn '^from apps' libs/` 仍 0 命中

## P2：铺齐原子块与事实（用户决策：先铺块，后编排）

> **第一批范围收窄**（REQS §10.11）：只做抄表链路 **12 块 + 6 事实**，
> 前置顺序 **复位 → 档案 → 组网 → 抄表**；第一次组网必复位 STA。
> 台架寻址用 `mapping_id`，不硬编码 COM 号。
- [ ] 4 个能力适配器骨架（simcon / simeter / frames / mclt，彼此零 import）
- [ ] **P2-0 复位手段调研**（`RESEARCH-reset.md`：能否软件复位/命令原文/回显判据/重入网耗时）
- [ ] 台架块 2：`fix.simeter_session` / `fix.simcon_session`
- [ ] **复位块 1：`reset.sta`**（第一批新增；第一次组网必做，之后按需）
- [ ] 档案块 3：`archive.add` / `archive.del` / `archive.query`（add 双判据）
- [ ] 组网块 4：`nwk.route_restart` / `active_registration` / `onnet_count` / `route_status`
- [ ] 抄表块 3：`meter.addr_probe` / `meter.read_645` / `meter.brain_local`
- [ ] 事实 6：bench×2 / archive.contains / onnet_count_min / route_running / sta.meter_addr_known
- [ ] `case_run.py probe --all` 自检入口
- [ ] 门禁：**第一批 12 块 + 6 事实**；`probe --all` 无台架时**全 unknown，无一判 pass**
- [ ] ⬜ **第二批**：`nwk.route_pause` / `topology` / `node_call` + 分钟采集 4 块 + 升级 4 块 + 2 事实

## P3：用例资产迁入与编排
- [ ] 22 例迁入 `libs/case_runner/data/cases/`（原目录保留只读副本）
- [ ] 样板 1 例改写并实跑 PASS（`simeter_handle_hex_645`）
- [ ] 19 例 simcon 组批量改写
- [ ] 3 例 simeter 组改写
- [ ] `sta_main_mclt_list_query` 的 `shell_ro` 改走 module 控制台 API
- [ ] 3 条端到端用例：`upgrade_15f1` / `nwk_join` / `mclt_collect`
- [ ] 门禁：`grep shell_ro` = 0；外部路径残留 = 0

## P4：API 层 —— ⏭️ 已移入 REQS-0042（2026-11-23 用户决定 API+UI 归下一个 req）
- [ ] `cases_api.py` 工厂 + `app.py` 挂载（紧邻 autotest）
- [ ] 9 类端点：cases / blocks / facts / suites(3) / run / runs / compare
- [ ] 资源未满足 409 + 明确原因
- [ ] 单测 + `/api/autotest/*` 不回归

## P5：UI —— ⏭️ 已移入 REQS-0042（同上）
- [ ] 新页面「用例库」：用例名 + 块清单（可展开）
- [ ] 勾选 + 方案下拉 + 存为方案；刷新后勾选随方案恢复
- [ ] 「立即运行」单例 + 「按方案运行」两入口
- [ ] **断点可视化**：失败块高亮 + 未满足事实与原因
- [ ] 主题令牌合规（0 issue）
- [ ] 浏览器实测：勾选→存方案→刷新→运行→见断点

## P6：收口 —— ⏭️ 收口条件随 P4/P5 一并移入 REQS-0042；本需求代码交付物已入库

> **交付状态（2026-11-23 收口记录）**
> - 已入库 `71551b6`：`libs/case_runner/`（67 文件 / 19050 行 + 31 JSON 声明）+ `tools/scripts/case_run.py`
> - 测试：`pytest libs/case_runner -q` → **444 passed / 2 skipped**；`test_layering.py` 10 passed
> - 真实台架：`meter_read` 3/3 PASS（1234.56 kWh）、`reset.sta` 真实复位成功、`archive.add` 双判据成立
> - 资产：15 块 / 8 事实 / 5 用例 / 3 方案，`loader problems == []`
> - API + UI 未做，按用户决定整体移入 `reqs/0042-blackbox-case-autorun-and-console/`
> - 遗留待办（0042 一并处理）：`meter.py` 错误码表与 `common.verify_failure_state` 口径统一

## 原 P6 收口清单（已移入 0042）
- [ ] DoD 9 条逐项验证留证
- [ ] `docs/api-endpoints-inventory.md` 补 `/api/cases/*`
- [ ] DONE.md + REQS-INDEX 状态更新
