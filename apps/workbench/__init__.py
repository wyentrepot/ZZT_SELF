"""workbench —— AI 闭环研发验证平台（统一集成程序）。

对应需求 FR-6（统一集成程序）：一个桌面入口 + 一个统一后端 + 一个页签式
界面，把侦听台、模块日志/烧录、对照解析、模拟集中器、AI 验证工作台集成
在一起，全链路（编码 → 烧录 → 运行监控 → 激励验证 → 结论报告）在一个
程序内闭环。

设计要点（DECISIONS.md ADR-17）：
- 包名用 workbench 而非 platform：Python 标准库自带 platform 模块，apps/
  在 sys.path 最前时常规包 platform 会遮蔽标准库，导致 uvicorn/fastapi
  内部 import platform 崩溃；workbench 无冲突。
- 不合并底层代码：listener / module_log / sim_concentrator / loghooks
  保持独立包，各自可独立运行、独立测试；本包只做挂载 + 编排（FR-6.4）。
- 编排能力零重实现：Run/报告/比对/反馈全部复用 loghooks 引擎、
  sim_concentrator runner、listener 解析链（FR-6.4 第三条）。
"""
