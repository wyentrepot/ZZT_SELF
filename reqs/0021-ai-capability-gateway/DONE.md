# REQS-0021 DONE

> 完成记录只追加，不覆盖未完成阶段的 `TODO.md`。

## 2026-09-02 ｜ P0 文档基线完成

- **做了什么**：登记 REQS-0021；冻结 v2 任务门面、本机 loopback 全权限、v1 兼容、
  局域网旧路由延期、三源独立证据、L1/L2/L3、资源 owned_only 和 P1--P4 阶段门；
  更新总需求、骨架设计与任务安排。
- **没有做什么**：没有创建或修改 Python、FastAPI 路由、Pydantic 模型、配置、技能、
  测试、运行时数据或硬件会话；没有改变任何现有接口或局域网行为；没有提交或推送。
- **任务边界**：下一步只能在用户明确指定“启动 P1”后创建 v2 契约、访问域和
  capabilities。P2/P3/P4 仍需各自独立确认。
- **恢复入口**：`reqs/0021-ai-capability-gateway/REQS.md` §2、§4、§6 和
  `TODO.md` 的 P1；架构边界见 `docs/03-骨架设计.md` §6.4。
- **验证证据**：P0 完成后执行 Markdown 内部链接检查、标题结构检查、
  `git diff --check` 与限定变更文件检查；不运行开发/硬件测试。

## 2026-09-02 ｜ P1 契约、访问域与 capability 完成

- **做了什么**：新增 `ai_contracts.py`、`ai_access.py`、`ai_v2_api.py` 和独立 v2
  测试；在应用工厂注册唯一 P1 路径 `GET /api/ai/v2/capabilities`。本机全权限同时要求
  显式开关和真实 loopback 对端；LAN 必须使用 Bearer grant，并按 scope、逻辑资源源和
  安全别名过滤 capability。管理员 grant 仍仅限 v1 的本机 + admin key。
- **没有做什么**：未注册 investigations、jobs/evidence、module action、verification 或
  flash 的 v2 路径；未改 v1、页面/子应用路由、grant 存储语义、旧局域网策略或硬件会话；
  未提交或推送。
- **任务边界**：P2 只能在用户明确指定“启动 P2”后实现只读 investigations/jobs/evidence。
  P3/P4 仍需各自独立确认。
- **恢复入口**：实现入口为 `apps/workbench/ai_v2_api.py`；访问策略为
  `apps/workbench/ai_access.py`；合同为 `apps/workbench/ai_contracts.py`；P2 清单见
  `TODO.md` 的 P2。
- **验证证据**：先观察到缺失模块/路由导致 9 个预期 RED；完成后执行
  `$env:PYTHONPATH="apps;libs"; python -m pytest apps/workbench/test_ai_v2_contracts.py apps/workbench/test_ai_v2_api.py apps/workbench/test_ai_api.py apps/workbench/test_ai_operations.py -q`，结果 `63 passed`；`compileall` 通过。独立审查发现资源类型绑定、路径/串口别名泄漏和 source_health 契约偏差，均已增加回归测试并修复。

## 2026-09-02 ｜ P4 文档、skill 与库存校验子交付

- **做了什么**：新增 `tools/scripts/verify_api_inventory.py`，以惰性 stub 构造工作台
  OpenAPI，校验 v2 八条公开路径、命名请求/响应 schema，并输出全路由、AI capability
  和 v1/v2/legacy 兼容分层清单；更新 `docs/api-contract.md`、`docs/16-AI操作指南.md`
  和 `.agents/skills/ai-control-plane/SKILL.md`，默认路径迁到 v2，保留 v1 专家说明。
- **没有做什么**：未修改用户原有未跟踪的 `docs/api-endpoints-inventory.md`；未启动串口、
  侦听台或真实烧录；未删除或重命名任何 v1 路由。
- **验证证据**：`python tools/scripts/verify_api_inventory.py` 输出 `PASS`、v2 `8/8`、
  26 个命名 schema；`--json` 输出机器可读清单；Python 编译检查和 `git diff --check` 通过。
- **边界**：这是 P4 文档/库存子交付；v2 全量业务回归、真实调用统计和最终提交仍由总验收处理。

## 2026-09-02 ｜ P2-P4 总验收完成

- **做了什么**：完成 v2 全部八条任务路径：capabilities、investigations、verification-runs、
  module-actions、flash-jobs、jobs 读取/取消/evidence；完成分层证据、LAN grant、本机
  全权限、写任务幂等、owned_only、烧录根目录白名单、库存校验、API 文档与控制面 skill 迁移。
- **验证证据**：`python tools/scripts/verify_api_inventory.py` 输出 PASS（8/8 v2 路径、26 个命名 schema）；
  相关 v2/v1 AI、simcon、trace、store 回归 `108 passed`；`compileall` 与 `git diff --check` 通过。
- **没有做什么**：未删除或修改 v1/页面旧路由，未收敛旧 LAN 路由，未启动真实串口、模拟集中器或烧录；
  未提交、未推送。真实烧录仅保留为人工验收，不是软件交付阻塞项。
