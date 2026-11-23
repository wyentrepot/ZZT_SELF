# PLAN.md — 需求 0041 执行计划（v3）

> **依据基线**：`REQS.md` **v3**（2026-11-23，变更 3 + §10 四层模型）。
> 基线若变更且本计划未重新生成，本计划即失效，不得执行。
>
> **历史说明**：本文件原为 v2 计划（Case→Suite→Run + CaseBackend + UI 优先，P0-P6），
> 因变更 3 重定主线，**v2 计划已整体失效**，不再保留可执行内容。旧 P0 已完成项见 `DONE.md`。
>
> 目标：建立 **Block（原子块）→ Fact（事实）→ Case（编排）→ Suite（方案）** 四层模型，
> 先铺齐 23 个原子块 + 8 个事实，再编排用例，UI 本轮做。
>
> 环境：WSL/Linux + Python3；工作台 8790（改后需重启生效）。
> 统一测试命令前缀：`cd /01-workfile-ai/01-zzt/ZZT_SELF && PYTHONPATH=apps:libs python3 -m pytest <target> -q`

---

## 阶段前必读：v3 已固化的技术事实

全部来自本会话实测，详细证据见 `REQS.md` §10.7。

| 事实 | 结论 | 对实现的影响 |
|---|---|---|
| **D** | 30 个 `(AFN,FN)` 下行无参可构帧；10 个块带参构帧已实测出 hex | BL-2/3/6 全部可为 |
| **E** | 上行 02H-F1 内嵌 645 递归解析通过，能取内层地址域 | `sta.meter_addr_known` / `meter.addr_probe` 可落地 |
| **F** | `join_anhui`/`search_meter`/`open_close` 引用的 task 文件**不存在** | 组网不能复用 scenarios，须由 BL-3 重建 |
| **G** | `network_assert.evaluate()` 抛 `NotImplementedError` | 非目标，不接侦听台则不需要 |
| **H** | 改前基线有 1 条既有红 `test_10f104_up_unsupported`（HEAD 已红，因 `846a7c1` 漏更测试） | P0 门禁以此为准，本需求**不修**它 |
| **I** | `11H-F5` 需 `time6+duration+retry+slices` 四参才能构帧 | 块参数显式化，不设隐式默认 |
| **J** | `15H-F1` 能构单帧，但仓库无分片状态机 | BL-6 需新增实现 |
| **K** | 22 例依赖：simcon 19 / simeter 3 / `shell_ro` 外部脚本 1 / 完全离线 1 | CS-2/CS-3 改写范围明确 |

---

## P0：开工门禁（改前基线固化）

- [ ] **P0-1 确认基线未变更**
  - `grep -n '变更 [0-9]' reqs/0041-test-case-catalog-and-suite/REQS.md | tail -1`
  - 期望：`变更 3`。不一致 → 停止，重新生成计划。

- [ ] **P0-2 固化改前测试基线（含已知红）**
  - `PYTHONPATH=apps:libs python3 -m pytest libs/test_automation libs/case_library libs/gw_cass_migrate libs/parser_lib libs/sim_meter -q`
  - 期望：`550 passed, 1 failed, 66 skipped`，**唯一红 = `test_10f104_up_unsupported`**
  - 记录：若出现该条之外的 fail，先解决它，不在坏基线上开工。

- [ ] **P0-3 确认工作台与台架可启动**
  - `curl -s -m 3 http://127.0.0.1:8790/api/autotest/status | head -c 200` → 含 `"available"`
  - `curl -s -m 3 http://127.0.0.1:8790/api/simeter/status | head -c 200` → 模拟电表服务在线
  - 失败不阻塞 P1（P1 纯离线），但阻塞 P2 的块自检。

- [ ] **P0-4 确认分层基线未破**
  - `grep -rn '^from apps\|^import apps' libs/ --include=*.py | grep -v __pycache__ | wc -l`
  - 期望：`0`

- [ ] **P0-5 确认 `libs/case_runner/data/` 不被 gitignore**
  - `git check-ignore -v libs/case_runner/data/blocks/x.json || echo NOT_IGNORED`
  - 期望：`NOT_IGNORED`。若被忽略 → 在 `.gitignore` 加 `!libs/case_runner/data/` 例外。

---

## P1：内核 `libs/case_runner/core/`（零领域依赖）

> 职责边界：本阶段所有代码**不得** import 任何领域库，也不得 import `capabilities/`。
> 全部靠 fake 注入测试，不碰串口/HTTP。

### P1-1 模型层

- [ ] **P1-1a 写失败测试** `libs/case_runner/test_models.py`
  - 用例 A：`BlockSpec` 缺 `action` → 抛 `ValidationError`。
  - 用例 B：`CaseSpec` 的 `blocks` 为空 → 抛 `ValidationError`。
  - 用例 C：`CaseSpec` 的 `steps[]` 每项必须有 `do` 与 `expect`（UI 展示口径）。
  - 命令：`PYTHONPATH=apps:libs python3 -m pytest libs/case_runner/test_models.py -q`
  - 期望失败：`ModuleNotFoundError: No module named 'case_runner'`

- [ ] **P1-1b 最小实现** `libs/case_runner/core/models.py`
  - 契约（与 REQS §10.2 一致，字段名不得改）：
    ```python
    class CaseStep(BaseModel):
        no: int = Field(ge=1)
        do: str = Field(min_length=1)
        expect: str = Field(min_length=1)

    class FactSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(min_length=1)
        title: str = Field(min_length=1)
        probe: str = Field(min_length=1)          # 注册名，如 "archive.contains"
        params_schema: dict[str, Any] = Field(default_factory=dict)

    class BlockSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(min_length=1)
        title: str = Field(min_length=1)
        needs: list[str] = Field(default_factory=list)      # fact ids
        provides: list[str] = Field(default_factory=list)   # fact ids
        action: dict[str, Any]                              # afn/fn/params | recipe | semantic
        assert_: list[dict[str, Any]] = Field(default_factory=list, alias="assert")
        cleanup: list[dict[str, Any]] = Field(default_factory=list)
        tags: list[str] = Field(default_factory=list)

    class CaseSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(min_length=1)
        title: str = Field(min_length=1)
        steps: list[CaseStep] = Field(min_length=1)   # UI 展示：用例名 + 步骤说明
        needs: list[str] = Field(default_factory=list)
        blocks: list[str] = Field(min_length=1)       # block ids，顺序即执行序
        asserts: list[dict[str, Any]] = Field(default_factory=list)
        tags: list[str] = Field(default_factory=list)

    class SuiteSpec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str = Field(min_length=1)
        name: str = Field(min_length=1)
        case_ids: list[str] = Field(min_length=1)
        stop_on_fail: bool = False
        notes: str = ""
    ```
  - 另建 `libs/case_runner/__init__.py`、`core/__init__.py` 导出类型。
  - 命令：同 P1-1a → 期望 `passed`

### P1-2 注册表

- [ ] **P1-2a 写失败测试** `libs/case_runner/test_registry.py`
  - `register_block` / `get_block` 往返一致；未注册 id → `NotFound` 且消息含 id；重复注册 → 报错。

- [ ] **P1-2b 最小实现** `core/registry.py`
  - `BLOCK_REGISTRY: dict[str, BlockExec]`、`FACT_REGISTRY: dict[str, FactProbe]`
  - `register_block(id, fn)` / `register_fact(id, fn)` / `get_block(id)` / `get_fact(id)`
  - 注：注册的是**执行体**（可调用），声明是 JSON（`data/blocks/*.json`），两者按 id 对应。

### P1-3 规划器（事实 DAG）

- [ ] **P1-3a 写失败测试** `libs/case_runner/test_planner.py`
  - 用例 A：`needs` 中某事实探测返回 `no` → 该块及下游块 `skipped`，且 `reason` 含事实 id。
  - 用例 B：事实探测返回 `unknown` → 同样 `skipped`（**不折算为 pass**）。
  - 用例 C：同 run 内同一事实只探测一次（缓存命中，假的 probe 计数 = 1）。

- [ ] **P1-3b 最小实现** `core/planner.py`
  - `resolve(needs, facts_ctx) -> dict[str, FactValue]`；`FactValue = {state: yes|no|unknown, detail, evidence}`
  - `plan(case, facts_ctx) -> list[PlannedBlock]`，每项带 `runnable: bool` + `skip_reason`

### P1-4 执行器

- [ ] **P1-4a 写失败测试** `libs/case_runner/test_executor.py`
  - 用例 A：注入 fake block，单块 pass → `CaseOutcome.verdict == "pass"`。
  - 用例 B：单块 fail → `verdict == "fail"`，`failed_block == <id>`。
  - 用例 C：`stop_on_fail=True` 时首块 fail → 下游全 `skipped`（非 pass、非 fail）。
  - 用例 D：结果里 `unknown`/`skipped` 的块**不计入** PASS 计数。

- [ ] **P1-4b 最小实现** `core/executor.py`
  - `execute_case(case, ctx) -> CaseRunResult`；内部：`plan()` → 逐块 `get_block(id)(params, ctx)` → 聚合
  - 结论落 `test_automation.models.AssertionResult`（`outcome ∈ pass|fail|inconclusive`），**不新造模型**
  - `CaseRunResult.to_dict()` 含：`verdict` / `failed_block` / `blocks[]`（每块 `state`/`detail`/`facts`）
  - 命令：`PYTHONPATH=apps:libs python3 -m pytest libs/case_runner -q` → 期望 `passed`

### P1-5 分层门禁（本需求核心防耦合手段）

- [ ] **P1-5a 写测试** `libs/case_runner/test_layering.py`
  - **铁律 1**：`core/` 下任何 `.py` 不得 import `capabilities`、`parser_lib`、`sim_concentrator`、
    `sim_meter`、`loghooks`、`minute_assert`、`apps.*` —— 用 `ast` 解析全部 `Import`/`ImportFrom`。
  - **铁律 2**：`capabilities/*.py` 之间互不 import —— 解析每个文件的 import 集合，断言不含同级模块名。
  - **铁律 3**：`libs/case_runner/` 下不得出现 `from apps` / `import apps`。
  - 验收：三条断言全绿；人为加一条违规 import 时对应断言必须变红（在测试里用临时文件验证一次即可）。

- [ ] **P1-6 阶段门禁**
  - `PYTHONPATH=apps:libs python3 -m pytest libs/case_runner -q` → 全绿
  - `grep -rn '^from apps' libs/ --include=*.py | grep -v __pycache__ | wc -l` → `0`

---

## P2：铺齐原子块与事实（按用户决策：先铺块，后编排）

> **第一批范围收窄（REQS §10.11，2026-11-23 用户决策）**：本阶段只做**抄表链路**的 12 块 + 6 事实，
> 前置顺序 **复位 → 档案 → 组网 → 抄表**；分钟采集块与升级块推迟到第二批，UI 第三批。
> 台架寻址一律用 `mapping_id`，**不硬编码 COM 号**（见 REQS §10.10 事实 L）。
>
> 每块 = 1 个 JSON 声明（`data/blocks/<id>.json`）+ 1 个执行体（`capabilities/*.py` 里的函数）。
> **执行原语全部复用现有 `simcon/verify`**（`send`/`expect`/`recv_only`/`wait_mclt_stored`/`read_mclt_11e3`），
> 构帧走 `encode_app_data`，解析走 `facade.decode` / `matcher`，**零复制**。

### P2-0 复位手段调研（第一批新增前置）

- [ ] **P2-0a 调研「STA 复位」的可用手段**，产出结论到 `reqs/0041-test-case-catalog-and-suite/RESEARCH-reset.md`
  - 候选：CCO 控制台命令（`sta reset` 类）/ 1376.2 复位 AFN / STA 模块串口 AT 复位 / 断电重上电
  - 必须回答：**能否软件复位、命令原文、回显判据、复位后 STA 重新入网耗时量级**
  - 验收：给出「推荐手段 + 备选 + 判据 + 风险」，且每条带仓库内证据（文件:行号 或 实测输出）
- [ ] **P2-0b 确认 simcon 端口口径**（REQS §10.10 事实 L 的待确认项）
  - 向用户确认 COM4 vs COM28；确认前块层用 `mapping_id` 寻址

### P2-0c 复位块

- [ ] `reset.sta`：复位 STA（手段按 P2-0a 结论）
  - 判据 = 复位命令应答/回显正确 **且** 复位后 STA 能重新被观测到（不只看命令返回）
  - 作为 `nwk.*` 块的**可选前置**：第一次组网必做，之后按需（由用例 `needs` 声明）


### P2-1 能力适配器骨架

- [ ] **P2-1a 写失败测试** `libs/case_runner/test_capabilities.py`
  - 每个适配器 `probe()` 在依赖缺失时返回 `ok=False` + 原因，不抛异常。

- [ ] **P2-1b 最小实现**（4 个文件，彼此零 import）
  - `capabilities/simcon.py`：HTTP `/api/simcon/*`、`/api/ai/v2/verification-runs`；base_url 由注入，**不写死 8790**
  - `capabilities/simeter.py`：HTTP `/api/simeter/*`
  - `capabilities/frames.py`：`parser_lib.facade`（decode/build/verify）
  - `capabilities/mclt.py`：`libs/minute_assert`

### P2-2 台架块（2）

- [ ] `fix.simeter_session` / `fix.simcon_session`
  - 协议：`probe → {ok, detail}`；不在线 → 块 `skipped` + 原因
  - 测试：fake HTTP 返回 503 → `skipped` 而非崩溃

### P2-3 档案块（3）

- [ ] `archive.add`（11H-F1）、`archive.del`（11H-F2）、`archive.query`（10H-F2）
  - `archive.add` 判据 = **00H-F1 确认 且 10H-F2 能回查到该地址**（双判据，防「确认了但没真加进去」）
  - 参考实测 hex：加档案 `6817004300000000000111010001010000000000025A16`
  - 测试：fake verify 返回确认但 10H-F2 查不到 → **必须 FAIL**（这条是防假 PASS 的关键测试）

### P2-4 组网块（7）

- [ ] `nwk.route_restart`(12H-F1) / `nwk.active_registration`(11H-F5)   ← 本批
- [ ] ⬜ `nwk.route_pause`(12H-F2) / `nwk.topology`(10H-F21) / `nwk.node_call`(04H-F2)   ← 第二批
- [ ] `nwk.onnet_count`(10H-F1) / `nwk.route_status`(10H-F4)   ← 本批
  - `nwk.active_registration` 按事实 I 显式要求 `time6/duration/retry/slices` 四参，缺参报明确错误
  - `nwk.onnet_count` 判据 = 从节点总数量 ≥ 期望值（字段 `从节点总数量`）
  - 测试：每组 1 条构帧断言 + 1 条断言求值断言

### P2-5 抄表块（3）

- [ ] `meter.addr_probe`（02H-F1 透传 645 13H）— **判据 = 内层 645 地址域 == 期望地址**（事实 E）
- [ ] `meter.read_645`（02H-F1 透传 645 11H）— 判据 = 内层 CS 合法 + DI 回显 + 数值可解
- [ ] `meter.brain_local`（simeter `handle_hex`）— 判据 = 应答帧非空 + `note.value` 存在
  - 测试：内层地址不符 → `addr_probe` FAIL 且 detail 写出期望/实际地址

### P2-6 分钟采集块（4）— ⬜ 第二批，本批不做

- [ ] `mclt.task_config`(11H-F231) / `mclt.archive_link`(11H-F232) / `mclt.wait_stored` / `mclt.read_11e3`
  - 后两者复用现有语义步，断言走 `minute_assert`（内层 698 递归）
  - 测试：用现有 `test_mclt_wait_read.py` 的样本帧做断言求值

### P2-7 升级块（4）— 分片状态机 — ⬜ 第二批，本批不做

- [ ] `upg.start`（15H-F1 `attr=0 cmd=0`）→ 等段确认
- [ ] `upg.segment`（15H-F1 ×N）→ 每段确认 + `seg_id` 递增 + 超时重发有界（重发次数为块参数）
- [ ] `upg.end`（15H-F1 `attr=1`）→ 确认
- [ ] `upg.version_query`（03H-F1）→ 版本字段 ≠ 旧版本
  - 参考实测：`681C00430000000000011501000300000200000000010200AABBC716`
  - **范围红线**：只做分片编排 + 帧级回验，**不做真固件烧录/激活**（REQS §10.6 第 6 条）
  - 测试：fake 段确认缺失 → 重发达上限后 FAIL，detail 写明第几段

### P2-8 事实层（8）

- [ ] `bench.simeter_online` / `bench.simcon_online` / `archive.contains(addr)` / `nwk.onnet_count_min(n)`
- [ ] `nwk.route_running` / `sta.meter_addr_known(addr)` / `mclt.task_configured(task_no)` / `upg.version(module)`
  - **FT-2 要求**：事实探测复用块的断言函数，不另写判定逻辑
  - **FT-3**：三态 `yes`/`no`/`unknown`；`unknown` 永不折算为 `yes`
  - 测试：每事实 1 条 fake 探测单测，覆盖三态

### P2-9 块自检

- [ ] **P2-9a 最小实现** `tools/scripts/case_run.py probe --all`
  - 无台架：全部输出 `unknown` + 原因；有台架：逐块 `pass`/`fail`/`unknown`
  - 验收：`PYTHONPATH=apps:libs python3 tools/scripts/case_run.py probe --all --json` 输出合法 JSON，
    且**没有任何块在缺台架时被判 pass**。
- [ ] **P2-10 阶段门禁**
  - 第一批块 JSON 计数：`ls libs/case_runner/data/blocks/*.json | wc -l` → `12`
  - 第一批事实 JSON 计数：`ls libs/case_runner/data/facts/*.json | wc -l` → `6`
  - （全量目标 23 块 + 8 事实，第二批补齐；见 REQS §10.11）
  - `PYTHONPATH=apps:libs python3 -m pytest libs/case_runner -q` → 全绿
  - `test_layering.py` 仍全绿

---

## P3：用例资产迁入与编排

### P3-1 迁入 22 例

- [ ] `mkdir -p libs/case_runner/data/cases && cp /01-workfile-ai/01-zzt/autotest/cases/*.py libs/case_runner/data/cases/`
  - 校验：`ls libs/case_runner/data/cases/*.py | wc -l` → `22`
  - 原目录 `/01-workfile-ai/01-zzt/autotest/` **保留只读副本，不删**

### P3-2 改写为块编排

- [ ] 先做 **1 例样板**：`simeter_handle_hex_645`（完全离线，依赖 `meter.brain_local`）
  - 验收：样板用例 JSON 通过，实跑 PASS
- [ ] **19 例 simcon 组**批量改写（结构高度相似：`ctx.verify([...])` → `blocks: [...]`）
- [ ] **3 例 simeter 组**改写
- [ ] `sta_main_mclt_list_query`：`shell_ro` 外部脚本调用 → 改走 module 控制台 API（CS-3）
  - 验收：`grep -rn 'shell_ro' libs/case_runner/` → `0`

### P3-3 3 条端到端用例

- [ ] `upgrade_15f1`（升级链）：`needs=[meter_online, archive_contains, onnet_count_min, sta_meter_addr_known]`
      → `blocks=[upg.start … upg.version_query]`
- [ ] `nwk_join`（档案→组网）：`needs=[meter_online]` → `blocks=[archive.add, nwk.route_restart,
      nwk.onnet_count, nwk.route_status, archive.query]`
- [ ] `mclt_collect`（分钟采集）：`blocks=[archive.add, mclt.task_config, mclt.archive_link,
      mclt.wait_stored, mclt.read_11e3]`
- [ ] 验收：台架齐全时 3 条 PASS；故意去掉某事实时，结果**明确指出断在哪个块、哪个事实**

### P3-4 路径解耦校验

- [ ] `grep -rn '/01-workfile-ai/01-zzt/autotest' libs/case_runner/ --include=*.py | grep -v __pycache__ | wc -l` → `0`
- [ ] `PYTHONPATH=apps:libs python3 -m pytest libs/case_runner -q` → 全绿

---

## P4：API 层

### P4-1 路由骨架

- [ ] **P4-1a 写失败测试** `apps/workbench/test_cases_api.py`
  - `TestClient` 断言 `GET /api/cases` 返回 200 且 `len(cases) > 0`
  - 期望失败：`404 Not Found`

- [ ] **P4-1b 最小实现** `apps/workbench/cases_api.py`
  - 工厂 `create_cases_router(storage_dir: Path) -> APIRouter`，`prefix="/api/cases"`
  - 在 `app.py`（**紧接 `app.py:493` autotest 挂载之后**）：
    ```python
    from .cases_api import create_cases_router
    app.include_router(create_cases_router(storage_dir=storage_dir))
    ```

### P4-2 端点

- [ ] `GET  /api/cases`                    用例列表（含 `steps`/`blocks`/`needs`）
- [ ] `GET  /api/cases/blocks`             原子块目录（含 needs/provides/assert 摘要）
- [ ] `GET  /api/cases/facts`              事实目录
- [ ] `GET  /api/cases/suites`             方案列表
- [ ] `POST /api/cases/suites`             保存方案（body = `SuiteSpec`）
- [ ] `DELETE /api/cases/suites/{id}`      删除方案
- [ ] `POST /api/cases/run`                运行：`{suite_id}` 或 `{case_ids:[…]}`
- [ ] `GET  /api/cases/runs/{run_id}`      结果（含 `failed_block` 与块级明细）
- [ ] `GET  /api/cases/runs/compare?suite_id=..&a=..&b=..`  两次运行差异（RU-4）
- [ ] 资源未满足 409 + 明确原因（RU-1）
- [ ] 单测：9 类端点各 1 例

- [ ] **P4-3 兼容性回归**
  - `PYTHONPATH=apps:libs python3 -m pytest apps/workbench/test_cases_api.py apps/workbench/test_autotest_api.py apps/workbench/test_ai_v2_api.py -q`
  - 期望：全绿；`/api/autotest/*` 行为不变

---

## P5：UI

- [ ] **P5-1 页面骨架** `apps/workbench/static/pages/cases/{cases.html,cases.js}`
  - 列表列：勾选框 / **用例名** / **块清单摘要**（点击展开：每块 title + needs + assert）/ tags / 最近结果
  - 在 `static/app.js` 导航表加一条（group 归「辅助」）
  - 验收：`curl -s -m 3 http://127.0.0.1:8790/static/pages/cases/cases.html -o /dev/null -w '%{http_code}'` → `200`

- [ ] **P5-2 勾选 + 方案联动**
  - 勾选由 `selectedSuite` 驱动；「存为方案」→ `POST /api/cases/suites`
  - **刷新恢复**：加载时读方案 → 回填勾选框
  - 验收：手工勾 2 例 → 存为「smoke-2」→ F5 → 勾选仍在

- [ ] **P5-3 两级运行入口**
  - 「▶ 立即运行」单例（`{case_ids:[one]}`）；「▶ 按方案运行」（`{suite_id}`）
  - job 面板沿用 `autotest.js` 的 `pollJob`（1.5 s）

- [ ] **P5-4 断点可视化（UI-4，本需求重点）**
  - 失败用例：高亮失败块，列出该块 `needs` 中未满足的事实 + 探测结果
  - 未满足依赖的块：置灰 + hover 显示原因

- [ ] **P5-5 主题合规**
  - 沿用 0012 三层 token，仅用 `var(--color-*)`；主题静态校验 0 issue

- [ ] **P5-6 浏览器全链路实测**
  - 勾选 2 例 → 存方案 → 刷新恢复 → 按方案运行 → 见 PASS/FAIL 与**断在哪个块**
  - 单例即时运行 → 见结果
  - 证据留 `evidence/0041-ui/`（截图 + 步骤记录）

---

## P6：收口

- [ ] **P6-1 DoD 9 条逐项验证**（见 REQS §10.8）
  - [ ] `pytest libs/case_runner apps/workbench/test_cases_api.py -q` 全绿
  - [ ] `test_layering.py` 绿 + `grep '^from apps' libs/` = 0
  - [ ] 23 块 + 8 事实有单测；`probe --all` 无台架时全 `unknown`
  - [ ] `git ls-files libs/case_runner/data/cases | wc -l` = 22；`grep shell_ro` = 0
  - [ ] 3 条端到端用例 PASS
  - [ ] 断点可定位（失败块 + 未满足事实 + 原因）
  - [ ] UI 勾选→存方案→刷新→运行→见断点
  - [ ] `/api/autotest/*` 不回归
  - [ ] 除事实 H 的既有红外无新增红

- [ ] **P6-2 更新** `TODO.md`、`DONE.md`、`REQS-INDEX.md`（状态改「✅ 已完成」）
- [ ] **P6-3 补** `docs/api-endpoints-inventory.md` 的 `/api/cases/*` 段落

---

## 回滚方案

| 场景 | 回滚动作 |
|---|---|
| P2 块层跑不通 | 保留 P1 内核 + CLI 只读清单（块可浏览不可跑），降级为「目录 + 单块 probe」 |
| P3 迁移断链 | `git checkout -- libs/case_runner/data/cases/`；原 `autotest/` 始终保留未删，可退回旧路径 |
| P4 API 影响既有 | 新 API 独立文件 + 独立挂载点，删除挂载两行即回滚；`/api/autotest/*` 不受影响 |
| P5 UI 破坏主题 | 新页面独立目录，不影响既有 10 页；最坏仅下线该页入口 |

**破坏性动作声明**：
- P3-1 为**复制**，不删除 `/01-workfile-ai/01-zzt/autotest/`。
- 唯一可能改 `.gitignore` 的动作是 P0-5 的例外规则，且已验证必要性才执行。
- 本需求**不修**事实 H 记录的既有红 `test_10f104_up_unsupported`（属其他责任人提交遗留）。

---

## 检查点（依赖顺序，不得跳过）

1. **CP-1**（P1 完成）：内核单测全绿 + `test_layering.py` 三条铁律绿 → 才可进 P2
2. **CP-2**（P2 完成）：23 块 + 8 事实齐 + `probe --all` 无台架全 `unknown`（**无一被判 pass**）→ 才可进 P3
3. **CP-3**（P3 完成）：22 例入库 + 样板例实跑 PASS + 3 条端到端可给出断点 → 才可进 P4
4. **CP-4**（P4 完成）：API 全端点绿 + autotest 不回归 → 才可进 P5
5. **CP-5**（P5 完成）：浏览器全链路实测通过（含断点可视化）→ 才可 P6 收口
