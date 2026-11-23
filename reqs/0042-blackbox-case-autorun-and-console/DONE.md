# DONE.md — 需求 0042 完成日志（只追加，最新在上）

## 2026-11-23 ｜ 阶段 S1–S5 交付 + S6 收口（基线 v10）

**代码交付**
- `658212d` S1：地址角色化（`meter_addr` 唯一入口，档案侧 `archive_addr_of` / 电表侧不反转）、两层默认配置（`libs/.../data/case_defaults.json` + `config/case_defaults.json`）、单入口服务发现（`data/runtime/workbench.json` 四级解析）、编排模型步骤实例化（`plan:[{id,block,params,on_fail}]`）
- `246493a` S2/S3：档案幂等（已存在→pass 零帧）；`nwk.topology`（`10H-F21`，98% 判据，`master_included=false` 严格口径）+ 三处 `wait_s` 轮询（300/300/180s，达标即停、虚拟时钟可测）；端口占用**不看归属直接抢占**（`close` 后重试一次 + `self_healed[]`，补 `SimconCapability.close()`）；四态 verdict（`skipped→blocked`、`unknown→inconclusive`，优先级 `fail>blocked>inconclusive>pass`，断言层三值契约未动）；零介入不变量（`result.json` 含 `params` 快照 / `decisions[]` / `interventions[]`(恒空) / 时间戳 / 台架快照；退出码 0/1/2/3/4/5；整轮 `--timeout 600s`；预检 <1s 判 blocked；`probe --readonly`）；用例拆分 `meter_daily`（不复位，日常）/ `meter_first_join`（含复位）
- 最终提交：S4 API（10 端点，`/api/cases*` 已挂载）+ S5 UI（「用例库」页接管导航）+ 本文件

**真机证据（零参数、`< /dev/null`、单条命令）**
| 场景 | 结果 |
|---|---|
| `case_run run --suite meter_smoke_min` | `bench_ready 2/2` + `meter_daily 5/5` **PASS**，单轮 **17s**，连跑两次一致 |
| 抄到的值 | 正向有功总电能 **1234.56 kWh**、A 相电压 **220.1 V**（另验过 A 相电流 2.21 A） |
| 四态分类 | 串口桥断开时 `meter_daily` → **BLOCKED / 退出码 5**（不再误判 fail） |
| 复位后链路恢复 | 实测 **≈10 分钟**（8 次探活，17:28:37 转 pass）→ 据此拆用例 |
| 浏览器全链路 | 7 用例 → 台架就绪 → 勾选 2 例 → 存方案 → **刷新后勾选恢复** → 立即运行 → **两条 PASS**；截图 `evidence/0042/s6-ui-e2e.png` |

**门禁**：`pytest libs/case_runner apps/workbench/test_cases_api.py test_autotest_api.py test_app.py test_cases_page.py` → **845 passed / 2 skipped**；`case_run check` problems==[]；分层铁律 0；`/api/autotest/*` 不回归；新页面主题 issue **0 新增**。

**仍待真机补的项（记入后续）**
- `fail` / `inconclusive` 的**真机构造**（今日真机只取到 `pass` 与 `blocked` 两种；另两种由 CLI/API 集成测试覆盖）
- 自愈的**真机占用**演练（单测已覆盖 409→close→重试一次）
- `nwk.nodes_online` / `nwk.route_status` 的真机抽样（当前只有块级单测；按 D8-5 应在收口单独只读探测）
- `meter_first_join` 的**完整长链路**验证（复位后需 ≈10 分钟恢复，单轮会顶到 600s 上限）→ 交由 REQS-0043 长跑覆盖

## 2026-11-23（晚）｜ v11：结论=一句话报告 + 证据限量保留

**用户反馈**：UI 不能逐步列块名（`archive.add` 用户看不懂），跑完直接出报告；证据目录不能无限存储，只留近 30 轮。

**交付**
- `libs/case_runner/core/report.py`：块 id→业务动作（16 块）、失败原因四类人话（超时/判据不成立/台架不具备/说不清）、`summarize_case`/`summarize_run`、块 id 泄漏守卫（55 例测试；13140 次真实 detail 归类 0 泄漏）
- `core/retention.py`：`prune_runs(keep=30)`，名字优先 mtime 兜底，**当前轮永不删**；CLI `--keep-runs`、API `keep_runs`；`pruned` 进结果
- CLI 人读输出改报告式（块表只 `--verbose`），API 增 `summary{headline,reasons,evidence_paths}` 与 `pruned`
- UI：每用例一张报告卡（标题=用例名（结论）/一句话原因/证据路径可复制），逐步块表收进「开发者详情」折叠；台架横幅去内部词（`WORKBENCH_BASE`→「台架正忙」）；行内摘要按**块链**生成（`5 步：档案下发 → 组网 → 绑表 → 抄表`）
- 修：`/api/cases` 对 `plan` 用例 `blocks` 恒空（改用 `effective_steps()`）；端口计数把已禁用口算成不可用；页面重复词典覆盖

**真机验证**
```
CLI：台架就绪自检（PASS） / 抄表日常回归（PASS） → 本轮汇总 PASS，退出码 0
     证据（运行结果）：data/case_runs/20261123-211857-139bf2/result.json
     已清理 30 轮旧证据（保留最近 30 轮）
浏览器：报告卡 2 张均 PASS；台架横幅「5 口全通 · 6 条关键事实成立」；默认区无块 id
```
**门禁**：`946 passed / 2 skipped`；冒烟 6 路径 PASS；`pages/cases` 主题 0 issue。
