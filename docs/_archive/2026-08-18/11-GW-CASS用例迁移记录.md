# GW-CASS 用例迁移记录（2026-08-17）

> 本记录供查阅：任务 3 的 GW-CASS 集中器用例迁移内容、验证证据、决策记录。

## 1. 背景

- 用户指令：执行任务 3，用例迁移项目 `\\wsl.localhost\Ubuntu-22.04\01-workfile-ai\GW-CASS`，里面有测试用例（集中器的一类）。
- 用户决策：**97 个用例全迁**，**迁移成 CasePackage（统一验证契约）**。

## 2. GW-CASS 项目结构

| 文件/目录 | 内容 |
|---|---|
| `TestSce.json` | 测试场景：`TEST_PARA`（参数）+ `TEST_CASE_LIST`（按 AFN 分组的用例清单）+ `TEST_CASE_CONTENT`（**97 个用例**：标题/步骤-操作+预期结果/合并行） |
| `BasicFeature.py` | 376.2/645/698 帧构造与解析工具（`Creat_3762_Frame`/`Analysis_3762_Frame` 等） |
| `ResultStats.py` | `TestStats`：测试结果统计（total/passed/failed/success_rate/failure_distribution） |
| `web_gateway/` | web 执行引擎（app.py + ToolThread.py），`Test_AFN_xxFyy` 用例函数驱动真实 CCO |

## 3. 迁移设计

**方案**：97 个用例（TEST_CASE_CONTENT）每个转成一个 `libs/test_automation` 的 CasePackage（统一验证契约，docs/03 §3/§4）。

| CasePackage 字段 | 来源 |
|---|---|
| `case_id` | `gw_cass_<序号>`（如 gw_cass_01） |
| `name` | 用例标题（如 "AFN=00H-F1确认"） |
| `description` | "GW-CASS 用例迁移 #N：标题" |
| `parameters.source` | `"gw_cass"` |
| `parameters.index` | 用例序号 |
| `parameters.afn_group` | 标题提取的 AFN 分组（00H~15H 或 "场景"） |
| `parameters.steps` | 结构化步骤（action + expected，保留原文追溯） |
| `parameters.needs_hardware` | 是否依赖真实硬件（继电器/真实电表/多 CCO/噪声等） |
| `device` | serial_port COM24（模拟集中器） |
| `assertions` | 从预期结果提取（present/equals），source=gw_cass kind=frame |

**断言提取规则**（`_ASSERTION_PATTERNS`）：
- "应答确认帧/00H-F1" → present afn_fn=00H-F1
- "应答否认帧/00H-F2" → present afn_fn=00H-F2
- "主动上报 06H" → present afn=06H
- "返回 <AFN>-F<FN>" → present afn_fn=<AFN>-F<FN>
- "数量=0" → equals count=0

## 4. 新增代码

| 文件 | 内容 |
|---|---|
| `libs/gw_cass_migrate/migrator.py` | `_detect_afn_group`/`_needs_hardware`/`_extract_assertions`/`migrate_case`/`load_gw_cass_cases`/`dump_cases_json` |
| `libs/gw_cass_migrate/__init__.py` | 包入口 |
| `libs/gw_cass_migrate/test_migrator.py` | 10 个单测（AFN 分组/硬件检测/断言提取/序列化/fingerprint） |

## 5. 验证证据

```text
pytest libs/gw_cass_migrate          → 10 passed
pytest gw_cass_migrate+minute_assert+test_automation → 83 passed
全量回归（apps/* + libs/*）          → 425 passed / 66 skipped / 13 failed
13 failed 均为既知 WSL Windows 资源缺失（DLL/启动工具.bat/样本数据），与本次改动无关。
```

**真实集成验证**（对 GW-CASS/TestSce.json）：
- 97 个用例**全部迁移成功**，fingerprint 全部可算
- **302 条断言**提取
- **31 个用例**标记依赖硬件（继电器/真实电表/多 CCO 组网/噪声等）
- AFN 分组分布：00H×2、01H×3、02H×1、03H×14、04H×3、05H×11、10H×16、11H×4、12H×3、13H×1、14H×3、15H×1、场景×35
- 产物：`docs/_evidence/gw_cass_migrated_cases.json`

## 6. 任务状态更新（docs/04-任务安排.md）

- 任务 3 明细：加 GW-CASS 用例迁移完成 + 证据
- 推进重点注记：加 gw_cass_migrate
- 矩阵 FR-4：更新为"GW-CASS 用例迁移完成"

## 7. 决策记录

| 决策 | 选择 | 理由 |
|---|---|---|
| 迁移范围 | 全部 97 个（用户确认） | 完整覆盖 GW-CASS 集中器用例 |
| 迁移形式 | CasePackage 统一契约（用户确认） | 命中任务 3 验收出口"一个 Run 消费三源" |
| 迁移工具 | 新建 `libs/gw_cass_migrate/` | 独立迁移领域，不污染既有模块 |
| 迁移产物 | `docs/_evidence/gw_cass_migrated_cases.json`（不入库） | 派生产物，遵循 evidence 不入库约定 |
| 断言提取 | 正则从预期结果提取（present/equals） | GW-CASS 用例为自然语言，无机器帧定义 |

## 8. 未完成/后续

- 任务 3：侦听台帧、模拟集中器步骤接入统一 Evidence 契约；`libs/loghooks` 补 Evidence 适配测试
- 迁移用例的机器可执行帧定义（当前为语义断言，精确帧需从 ToolThread.py `Test_AFN_xxFyy` 提取或实机录制）
- 全量测试 13 个 WSL 失败项需 Windows + DLL 环境终验（既有基线）
