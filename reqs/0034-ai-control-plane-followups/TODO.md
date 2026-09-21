# REQS-0034 — TODO

> 基线：REQS.md v1.1（2026-09-21，变更 2 执行范围收口）。细粒度实现步骤见 PLAN.md。
> 当前仅实施 P0（工作台使用优化：BR-1/2/3）；P1（mclt 三改进，涉 CCO 日志/06F230 集中器侧）与
> P2（BR-5 README 首屏）**暂缓**，另行排期。

## 阶段划分

### P0：启动与 API 门面收口（工作台侧）【当前执行】
- [x] BR-1 启动前置检查（run.py 横幅）+ 401 文案区分（ai_v2_api._bearer_grant）
- [x] BR-2 investigation 响应带"必读 job"提示（ai_contracts.JobEnvelope.follow_up，显式字段方案）
- [x] BR-3 L3 ref 格式错误 422 带合法示例（ai_capability_service._l3_items）

### P1：mclt 工具三改进（tools/taiti/分钟采集）【暂缓】
- [ ] BR-4.1 miss --freeze 页面空集告警
- [ ] BR-4.2 cco 输出 per-freeze 06F230 分布
- [ ] BR-4.3 --json 结构化输出

### P2：文档与登记【暂缓】
- [ ] BR-5 README 首屏启动门槛 + 常驻方式
- [ ] 单测全绿 + 全量回归无新增失败
- [ ] 技能校验 + REQS-INDEX.md 0034 登记 + 提交
