# REQS-0034 — TODO

> 基线：REQS.md v1.0（2026-09-21）。细粒度实现步骤见 PLAN.md（动工前由 writing-plans 生成）。

## 阶段划分

### P0：启动与 API 门面收口（工作台侧）
- [ ] BR-1 启动前置检查（run.py 横幅）+ 401 文案区分（ai_v2_api._bearer_grant）
- [ ] BR-2 investigation 响应带"必读 job"提示（ai_contracts.JobEnvelope 或 summary，二选一）
- [ ] BR-3 L3 ref 格式错误 422 带合法示例（ai_capability_service._l3_items）

### P1：mclt 工具三改进（tools/taiti/分钟采集）
- [ ] BR-4.1 miss --freeze 页面空集告警
- [ ] BR-4.2 cco 输出 per-freeze 06F230 分布
- [ ] BR-4.3 --json 结构化输出

### P2：文档与登记
- [ ] BR-5 README 首屏启动门槛 + 常驻方式
- [ ] 单测全绿 + 全量回归无新增失败
- [ ] 技能校验 + REQS-INDEX.md 0034 登记 + 提交
