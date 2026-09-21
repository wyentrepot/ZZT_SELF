# REQS-0033 — PLAN（依据 REQS.md 基线 v1.0，2026-09-21）

> 由 writing-plans 生成。细粒度步骤与验证命令唯一来源；执行时勾选，不复制进 TODO.md。
> 目标：① P0 技能边界优化（已完成，本 PLAN 含验证）；② P1 capabilities 自描述最小调用链（代码）；
> ③ P2 同步登记。改后端行为零，仅加响应字段与文档。

---

## P0 ｜ 技能使用边界优化（已完成，验证项）

> 改动已落在全局库 `/home/02-skill-fc/skills/shared/hardware-in-the-loop/ai-control-plane/`
> （唯一事实源；工作区 `.agents/skills/ai-control-plane` 与运行时 `/root/.dsh/skills/ai-control-plane`
> 均为指向此处的软链，无需单独同步）。

### T-P0.1 验证 SKILL.md 边界条款齐备
- [x] 文件：`SKILL.md`
- [x] 检查点：
  - [x] 顶部含「先判定在线还是离线」三档判定序（BR-1）
  - [x] 用途路由表含「分钟采集离线分析 → mclt-collect-analysis 专项技能」行（BR-1）
  - [x] 「实测校准·工作台启动」为双环境姿势（agent 沙箱受管后台任务 / 桌面 nohup）+ 环境变量前置 + 停止姿势（BR-2）
  - [x] 红线 7（禁 pkill -f 自匹配）+ 8（agent 沙箱禁 nohup 常驻）（BR-3）
  - [x] 「路径根解析」段含「使用经验/ 定位」条款（BR-4）
  - [x] frontmatter `version: "2.7.0"`（BR 版本）
- [x] 验证：`grep -c "mclt-collect-analysis" SKILL.md` ≥ 2；`grep -n "version: \"2.7.0\"" SKILL.md` 命中

### T-P0.2 验证离线数据源清单
- [x] 文件：`references/offline-analysis.md`
- [x] 检查点：§0「问题归档导出清单」表格含 索引库/原始日志/模拟集中器库/simcon 帧/CCO 日志/页面 xlsx，含最少三件套说明（BR-5）
- [x] 验证：`grep -n "问题归档导出清单" references/offline-analysis.md` 命中

### T-P0.3 验证离线日志索引入口
- [x] 文件：`references/listener.md`
- [x] 检查点：「离线日志索引」节含 `POST /api/listener/logs/open`（8790 网关路径）、
  `/api/logs/open` 仅 8765 独立版可用（网关 404）、串口采集中 409 先 stop（BR-6）
- [x] 验证：`grep -n "离线日志索引" references/listener.md` 命中

---

## P1 ｜ v2 capabilities 自描述最小调用链（代码，待实现）

> 落点：`apps/workbench/ai_contracts.py`（契约字段）、`apps/workbench/ai_v2_api.py`
> （`capability_snapshot()` 填充）、`apps/workbench/test_ai_v2_api.py`（断言）。
> 环境：仓库根 `/01-workfile-ai/01-zzt/ZZT_SELF`，`pytest apps/workbench/test_ai_v2_api.py`。

### T-P1.0 前置失败检查（RED）
- [x] 命令：`cd /01-workfile-ai/01-zzt/ZZT_SELF && python3 -m pytest apps/workbench/test_ai_v2_api.py::test_v2_capabilities_local_full_is_typed_and_does_not_leak_secrets -q`
- [x] 预期失败：无——现有测试通过（本步是基线确认，非红灯；红灯由 T-P1.1 新增用例承担）

### T-P1.1 新增失败用例（RED）
- [x] 文件：`apps/workbench/test_ai_v2_api.py`，在 `test_v2_capabilities_local_full_...` 附近新增：
  ```python
  def test_v2_capabilities_carry_call_examples_local_full(monkeypatch, tmp_path):
      monkeypatch.setenv("WORKBENCH_AI_STORAGE_DIR", str(tmp_path / "ai-control"))
      monkeypatch.setenv("WORKBENCH_LOCAL_FULL_ACCESS", "1")
      app = _app()
      client = TestClient(app)
      body = client.get("/api/ai/v2/capabilities").json()
      caps = {item["name"]: item for item in body["capabilities"]}
      assert "investigations.create" in caps
      examples = caps["investigations.create"]["call_examples"]
      assert any("/api/ai/v2/investigations" in ex and ex.startswith("POST") for ex in examples)
      assert any("/api/ai/v2/jobs/" in ex for ex in examples)
      # 所有 capability 都带 call_examples（可为空数组，但键存在）
      assert all("call_examples" in item for item in body["capabilities"])
  ```
- [x] 命令：`python3 -m pytest apps/workbench/test_ai_v2_api.py::test_v2_capabilities_carry_call_examples_local_full -q`
- [x] 预期失败：`KeyError: 'call_examples'`（字段尚未存在）——红灯确认（实测 2 failed：local_full + lan_scoped 双用例）

### T-P1.2 契约加字段（最小实现）
- [x] 文件：`apps/workbench/ai_contracts.py`，`class Capability` 增加：
  ```python
  call_examples: list[str] = Field(default_factory=list)
  ```
- [x] 说明：可选字段默认空数组，旧客户端与 OpenAPI 兼容；不破坏 `Capability` 其它字段语义

### T-P1.3 capability_snapshot 填充调用链（最小实现）
- [x] 文件：`apps/workbench/ai_v2_api.py`，`capability_snapshot()` 内加静态映射，构造 `Capability` 时填入：
  ```python
  _CALL_EXAMPLES = {
      "capabilities.read": ["GET /api/ai/v2/capabilities"],
      "investigations.create": [
          "POST /api/ai/v2/investigations",
          "GET /api/ai/v2/jobs/{id}",
          "GET /api/ai/v2/jobs/{id}/evidence?level=L1",
      ],
      "module_actions.ensure": ["POST /api/ai/v2/module-actions", "GET /api/ai/v2/jobs/{id}"],
      "module_actions.send": ["POST /api/ai/v2/module-actions", "GET /api/ai/v2/jobs/{id}"],
      "module_actions.stop": ["POST /api/ai/v2/module-actions", "GET /api/ai/v2/jobs/{id}"],
      "verification_runs.create": ["POST /api/ai/v2/verification-runs", "GET /api/ai/v2/jobs/{id}", "GET /api/ai/v2/jobs/{id}/evidence?level=L1"],
      "flash_jobs.create": ["POST /api/ai/v2/flash-jobs", "GET /api/ai/v2/jobs/{id}"],
      "jobs.read": ["GET /api/ai/v2/jobs/{id}"],
      "jobs.evidence.read": ["GET /api/ai/v2/jobs/{id}/evidence?level=L1"],
      "jobs.cancel": ["POST /api/ai/v2/jobs/{id}/cancel", "GET /api/ai/v2/jobs/{id}"],
  }
  ```
  并在 `capabilities.append(Capability(..., call_examples=_CALL_EXAMPLES.get(name, [])))`
- [x] 口径：与 SKILL.md「任务 → 最小路径速查」表一致（investigations → jobs/{id} → evidence L1）；
      落地版另补 jobs.evidence.read 的 L2/L3 示例（含 `ref=listener:{index_id}:{frame_id}`）

### T-P1.4 绿灯验证（GREEN）
- [x] 命令：`python3 -m pytest apps/workbench/test_ai_v2_api.py::test_v2_capabilities_carry_call_examples_local_full -q`
- [x] 预期成功：1 passed（实测 2 passed：local_full + lan_scoped 双用例）
- [x] 再跑受影响的既有用例：`python3 -m pytest apps/workbench/test_ai_v2_api.py -q`
- [x] 预期成功：全部通过（实测 27 passed in 4.06s；进程挂起为非 daemon executor 线程所致，非测试失败）

### T-P1.5 全量回归（依赖评审检查点）
- [x] 命令：`cd /01-workfile-ai/01-zzt/ZZT_SELF && timeout 300 python3 -m pytest apps/workbench/ -q`
- [x] 预期：与改动前基线相比**无新增失败**——实测 **344 passed, 7 failed**；7 个失败全为存量：
      `test_ai_store_query.py` 5 个（simcon store 查询，基线同样 5 failed）+ `orchestration/test_profile_loading.py`
      2 个（REQS-0027 地址域口径变更导致的断言失效，stash 后基线同样 2 failed）
- [x] 评审检查点：capabilities 响应手工核对——local_full 与 lan_scoped 两用例已断言
      `call_examples` 存在、investigations.create 含三跳调用链、capabilities.read 为单跳；与 SKILL.md 速查表一致

---

## P2 ｜ 同步与登记

### T-P2.1 技能校验
- [ ] 命令：`cd /01-workfile-ai/01-zzt/ZZT_SELF && python3 .agents/skills/ai-control-plane/scripts/verify_api_inventory.py --repo-root /01-workfile-ai/01-zzt/ZZT_SELF`
- [ ] 预期成功：exit 0（PASS；惰性 stub，不开串口/不启动侦听台/不烧录）

### T-P2.2 全局库 git 提交
- [ ] 命令（在 `/home/02-skill-fc/skills`）：
  `git add shared/hardware-in-the-loop/ai-control-plane && git commit -m "feat(skills): ai-control-plane v2.7.0 使用边界优化——在线/离线判定序+启动双环境+红线固化+归档清单+离线日志索引（REQS-0033 P0）"`
- [ ] 只暂存本技能路径，不触碰该仓库工作树其它未提交改动（如 `.superpowers/`）

### T-P2.3 工作区登记与提交
- [ ] 文件：`REQS-INDEX.md` 加 0033 行（状态「🚧 进行中」，分支 master，2026-09-21）
- [ ] 命令：`git add reqs/0033-ai-control-plane-boundary REQS-INDEX.md`（连同 P1 代码改动后提交）
- [ ] commit message 注明 P0 文档 + P1 代码 + 登记

---

## 回滚与风险

- **P1 契约**：`call_examples` 为可选字段默认空数组，前端/旧客户端不读即无影响；若异常直接
  撤销该字段声明与填充即可，无迁移成本。
- **技能文档**：全局库 git 提交前可整路径回退；已提交也可 revert 单 commit。
- **环境约束**：全局库 `/home/02-skill-fc/skills` 需可写（danger-full-access 已确认）；P1 测试
  在仓库根跑，不依赖工作台启动。
