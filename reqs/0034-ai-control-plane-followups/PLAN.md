# REQS-0034 — PLAN（依据 REQS.md 基线 v1.1，2026-09-21）

> 由 writing-plans 生成。细粒度步骤与验证命令唯一来源；执行时勾选，不复制进 TODO.md。
> 环境：仓库根 `/01-workfile-ai/01-zzt/ZZT_SELF`；测试 `python3 -m pytest <路径> -q`。
> **范围注释（基线 v1.1，变更 2）**：当前仅执行 **P0（工作台使用优化：BR-1/BR-2/BR-3）**；
> **P1（mclt 三改进，涉 CCO 日志/06F230 集中器侧）与 P2（BR-5 README 首屏）暂缓**，另行排期。
> 步骤内容未变（与 v1.0 相同），仅执行顺序/范围按变更 2 收口。

---

## P0 ｜ 启动与 API 门面收口（工作台侧）【当前执行】

### T-P0.1 BR-1 启动横幅前置检查（RED → 实现 → GREEN）
- [x] 文件：`apps/workbench/run.py`
- [x] 现状（前置检查）：`run.py` 无环境变量检测横幅；`ai_v2_api.py:31-38` 401 文案为「缺少 Bearer token」
- [x] 实现：`__main__` 块在 `_ensure_serial_nodes()` 后打印前置检查清单（纯 print，不动逻辑）：
  ```
  [workbench] WORKBENCH_LOCAL_FULL_ACCESS=…  （缺省提示：v2 将需 token，本机可设 =1 免 token）
  [workbench] HPLC_OPEN_WORKBENCH=…  （headless/无图形建议 =0，免 xdg-open 噪音）
  [workbench] 健康检查：GET http://127.0.0.1:8790/api/health
  ```
- [x] 验证：`python3 apps/workbench/run.py`（或 import 冒烟）输出含三行前置检查（不依赖启动成功）
  —— 新增 `apps/workbench/test_run_preflight.py`（2 用例，capsys 断言三行 + 缺省提示），import 冒烟实测输出正确

### T-P0.2 BR-1 401 文案区分（RED → 实现 → GREEN）
- [x] 文件：`apps/workbench/ai_v2_api.py` `_bearer_grant`（:30-38）
- [x] RED：新增用例——无 Authorization 且未开 local_full 时，断言 detail 含「WORKBENCH_LOCAL_FULL_ACCESS」
- [x] 实现：`_bearer_grant` 的"缺少 Bearer token"分支文案改为：
  `缺少 Bearer token；本机 loopback 可在启动时设 WORKBENCH_LOCAL_FULL_ACCESS=1 免 token`
  （无效/过期/撤销分支文案不变）
- [x] GREEN：新用例 1 passed；`test_v2_loopback_without_flag_requires_a_bearer_grant` 仍通过
  （其断言是 status_code 401 + code 字段，不含 detail 精确匹配——实现前确认，若匹配则同步改断言）
  —— 确认原断言不含 detail 精确匹配，无需改动；另补 `test_v2_with_invalid_token_keeps_original_401_message` 守住原文案分支

### T-P0.3 BR-2 investigation 响应"必读 job"提示（RED → 实现 → GREEN）
- [x] 文件：`apps/workbench/ai_contracts.py`（JobEnvelope）+ `apps/workbench/ai_capability_service.py`（_envelope）
- [x] 决策：**首选显式字段** `follow_up: str | None = None`（JobEnvelope）——语义清晰、契约自描述；
  `_envelope` 中 investigation 且非终态时填 `"GET /api/ai/v2/jobs/{job_id}"`
- [x] RED：新增用例——investigation 创建响应 `follow_up` 含 `/jobs/` 与 job_id；非 investigation 为 null
- [x] GREEN：新用例通过；既有 investigation 用例（信封/evidence）不破坏
  —— 测试用 live 窗口构造非终态信封（`timeout_seconds=5` 收紧，避免后台 worker
  拖慢解释器退出；创建响应在受理时同步构建，恒为 queued，不受影响）

### T-P0.4 BR-3 L3 ref 格式错误带示例（RED → 实现 → GREEN）
- [x] 文件：`apps/workbench/ai_capability_service.py` `_l3_items`（:506-508）
- [x] RED：新增用例——`ref=bad` 的 evidence 请求 422，detail 含 `listener:<index_id>:<frame_id>`
- [x] 实现：格式错误分支文案改为：
  `L3 ref 格式错误：{ref_text}；合法格式 listener:<index_id>:<frame_id>，如 listener:idx-xxx:123`
- [x] GREEN：新用例通过；既有 evidence 用例不破坏

---

## P1 ｜ mclt 工具三改进（tools/taiti/分钟采集）【暂缓：变更 2 收口，涉 CCO 日志/06F230 集中器侧】

### T-P1.1 BR-4.1 miss --freeze 页面空集告警（RED → 实现 → GREEN）
- [ ] 文件：`tools/taiti/分钟采集/lib/analyze.py`（analyze_miss）+ `run.py`（cmd_miss）
- [ ] RED：`test_mclt_analyze.py` 新增——page 不含目标 freeze 时，result[fk]["page_missing"] is True
- [ ] 实现：`analyze_miss` 中 `recv = page.get(fk, set())` 后，若 fk 不在 page：`result[fk]` 增加
  `"page_missing": True`；`cmd_miss` 打印 `[告警] 页面无该冻结周期 {fk}，无法对账（仅机制推断）`
- [ ] GREEN：新用例通过；miss 既有用例不破坏

### T-P1.2 BR-4.2 cco 输出 per-freeze 06F230 分布（RED → 实现 → GREEN）
- [ ] 文件：`tools/taiti/分钟采集/lib/analyze.py`（analyze_cco）+ `run.py`（cmd_cco）
- [ ] RED：`test_mclt_analyze.py` 新增——analyze_cco 返回含 `f230_per_freeze` dict，按 fr 聚合计数
- [ ] 实现：`analyze_cco` 从 `f230` 列表按冻结分钟聚合 `f230_per_freeze`；`cmd_cco` 打印
  `06F230 按冻结周期: {…}`
- [ ] GREEN：新用例通过；cco 既有用例不破坏

### T-P1.3 BR-4.3 --json 结构化输出（RED → 实现 → GREEN）
- [ ] 文件：`tools/taiti/分钟采集/run.py`（main + 各 cmd_*）
- [ ] RED：`test_mclt_analyze.py` 或新 test_run 新增——`--json` 时 stdout 可 json.loads，含核心键
- [ ] 实现：`main` 加全局 `--json`；各 `cmd_*` 改为先组装 dict、`--json` 时
  `json.dumps(res, ensure_ascii=False, indent=2)` 输出并 return 0，否则原人类可读打印
  （最小改动：优先 sniff/cco/miss 三命令，page/pair/utcalib 顺带）
- [ ] GREEN：新用例通过；非 --json 输出回归不破坏

---

## P2 ｜ 文档与登记【暂缓：变更 2 收口，BR-5 README 首屏另行排期】

### T-P2.1 BR-5 README 首屏
- [ ] 文件：`README.md`「快速运行」段
- [ ] 实现：补三行——`WORKBENCH_LOCAL_FULL_ACCESS=1`（v2 免 token，漏设 capabilities 401）、
  headless 加 `HPLC_OPEN_WORKBENCH=0`、agent 沙箱用受管后台任务 / 桌面 nohup（详见 ai-control-plane 技能）
- [ ] 验证：`grep -n "WORKBENCH_LOCAL_FULL_ACCESS\|HPLC_OPEN_WORKBENCH" README.md` 命中

### T-P2.2 全量回归与登记
- [ ] 命令：`cd /01-workfile-ai/01-zzt/ZZT_SELF && timeout 300 python3 -m pytest apps/workbench/ tools/taiti/分钟采集/ -q`
- [ ] 预期：无新增失败（存量 7 失败除外，见 REQS-0033 DONE）；mclt 新用例全绿
- [ ] REQS-INDEX.md 0034 登记（状态「🚧 进行中」）
- [ ] 提交：工作区一次提交（P0+P1+P2 全部文件）；如技能文档同步（401 文案等）则 skill-fc 另提交

---

## 回滚与风险

- BR-1 横幅/文案、BR-3 文案、BR-4 均为低风险增量；BR-2 新增可选字段默认 null，无迁移成本。
- BR-2 若选 summary 方案则无契约变更；实现时按 REQS.md「二选一并写清取舍」记录在 DONE。
- mclt 工具目录当前为 staged 未提交状态（既有 mclt 技能/工具需求）——**提交前确认归属**，
  避免把其它需求 staged 内容卷进本需求提交（用 pathspec 精确提交）。
- 全量回归含 mclt 测试目录；存量 7 失败（store 5 + profile_loading 2）与 REQS-0033 记录一致。
