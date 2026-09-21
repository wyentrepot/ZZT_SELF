# REQS-0033 — TODO

> 基线：REQS.md v1.0（2026-09-21）。细粒度实现步骤见 PLAN.md（动工前由 writing-plans 生成）。

## 阶段划分

### P0：技能使用边界优化（文档类）
- [x] BR-1 在线/离线判定序：SKILL.md 顶部三档判定 + 路由表 mclt 专项行
- [x] BR-2 启动/常驻双环境姿势：SKILL.md「实测校准·工作台启动」改双环境 + 环境变量前置 + 停止姿势
- [x] BR-3 行为红线固化：红线 7（禁 pkill -f 自匹配）+ 8（agent 沙箱禁 nohup 常驻）
- [x] BR-4 使用经验定位边界：SKILL.md「路径根解析」补使用经验定位条款
- [x] BR-5 离线排查数据源归档清单：offline-analysis.md 新增 §0「问题归档导出清单」
- [x] BR-6 离线日志索引入口：listener.md 新增「离线日志索引」节（8790 网关路径 + 409 互斥）
- [x] SKILL.md 版本 v2.6.0 → v2.7.0

### P1：v2 capabilities 自描述最小调用链（代码类）
- [x] BR-7 capabilities 响应自带 call_examples（ai_contracts.py + ai_v2_api.py + 单测 local_full/lan_scoped 双用例）

### P2：同步与登记
- [x] 单测全绿（test_ai_v2_api.py 27 passed；全量回归 344 passed，7 failed 全为存量）
- [ ] 技能校验 verify_api_inventory.py 通过
- [ ] 全局库 git 提交留痕；REQS-INDEX.md 0033 登记（登记已完成，提交待 P1 收口）
