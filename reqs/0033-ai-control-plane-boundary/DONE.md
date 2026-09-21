# REQS-0033 — DONE（完成日志，只追加，最新在上）

## 2026-09-21 ｜ P0+P1 交付：技能边界优化 + capabilities 自描述最小调用链

**P0（文档类，全局库 v2.6.0 → v2.7.0）**：
- SKILL.md：在线/离线判定序 + 路由表 mclt 专项行；启动双环境姿势（agent 沙箱受管后台任务 /
  桌面 nohup）+ 环境变量前置 + 停止姿势；红线 7（禁 pkill -f 自匹配）+ 8（agent 沙箱禁 nohup）；
  「使用经验/ 定位」条款（可固化条款一律进正文，不追加条例）。
- references/offline-analysis.md：§0「问题归档导出清单」（索引库/simcon 帧/模拟集中器库/原始日志/
  CCO 日志/页面 xlsx + 最少三件套）。
- references/listener.md：「离线日志索引」节（`POST /api/listener/logs/open` 8790 网关路径、
  `/api/logs/open` 仅 8765 独立版、串口采集中 409 先 stop）——闭环使用经验 #5 遗留缺口。

**P1（代码类，工作区）BR-7**：
- ai_contracts.py：`Capability` 增加可选字段 `call_examples: list[str]`（默认空数组，契约兼容）。
- ai_v2_api.py：`_CALL_EXAMPLES` 静态映射（10 项能力全覆盖）+ `capability_snapshot()` 填充；
  口径与 SKILL.md「任务 → 最小路径速查」一致，jobs.evidence.read 另含 L2/L3 示例。
- test_ai_v2_api.py：新增 local_full / lan_scoped 双用例（字段存在 + investigations.create 三跳 +
  capabilities.read 单跳）。

**验证**：
- test_ai_v2_api.py 27 passed（4.06s；进程挂起为非 daemon executor 线程所致，非测试失败）。
- 全量回归 apps/workbench/：344 passed / 7 failed——**7 个失败全为存量**：
  test_ai_store_query.py 5 个（simcon store 查询，基线 stash 后同样 5 failed）+
  orchestration/test_profile_loading.py 2 个（REQS-0027 地址域口径变更断言失效，基线同样 2 failed）；
  BR-7 无新增失败。
- 技能校验 verify_api_inventory.py EXIT=0。

**待办**：P2 提交（全局库 + 工作区）与 REQS-INDEX 状态收口。
