# REQS-0027 DONE

## 2026-09-06 ｜ 全量完成（G1-G6）

**提交**：req/0027-simcon-rxtx-matching 分支
- `8700631` docs(reqs-0027): 立项（基线 v2 + TODO v2 + 索引登记）
- `ed48229` feat(simcon): 规则库+超时+滑窗调度+聚合（工作包 1-3）
- feat(simcon-ui): 结果面板前端（工作包 4）
- fix(simcon-api): BatchReadRequest 提升至模块级（future annotations 坑）
- docs: 收尾同步

**后端**：
- `libs/sim_concentrator/expect_rules.json/.py`：8 条 AFN/Fn→应答形态规则（02H-F1 单抄/03H/10H 查询→同帧回源 table；05H/11H/12H→00H-F1 确认/否认）、否认码语义（6D 超最大并发/6E 超条数/6F 正在抄读中 + 0-12 错误状态字）、超时档位（default 5s / single_read 59s / batch_read 99s / 重发间隔 6500/14000/18000/25000ms，出处=蒸馏库 aps_conf.h 实测值）
- `matcher.py` deny_info：00H-F2 错误状态字 → 码值+人话
- `runner.py`：run_single_step(auto_expect)——expect 未显式给出时按规则生成；expect_timeout=None 按档位；等待窗口旁听帧归类 result.bystanders；否认帧特判（00H-F2 与 fn=1 字面不匹配也立即判 fail 并附 deny）
- `batch.py` BatchReadJob：滑窗调度（在途=最大并发，回一帧补一发；成功/否认/超时/错误均释放槽位）；否认帧归属=地址域匹配优先+无地址认领去重；表地址匹配排除信息域 R 伪命中（seq 字节曾误判归属，实查后改按嵌套 645 地址域/地址域 A 判定）
- `aggregate.py`：collect_readings（成功率口径：超时算失败、应答=成功+否认）+ report_buckets（F1-F5 + 停复电 04H 单列）
- `api.py`：/step 配对结构、/expect_rules、/batch_read 三端点、/readings、/report_buckets

**前端**（workbench simcon 页单副本；listener/static 无 simcon 页，无需双副本同步）：
- 结果面板四页签：收发配对（下发↔应答卡片、倒计时进度条、档位 tooltip、确认/否认徽标+人话、数据表格、旁听帧列表）、抄读汇总（统计行+成功率+否认码细分+筛选）、并发抄表（最大并发/模式/规约 + 在途/排队/完成/成功率 + 1s 轮询明细 + 停止）、主动上报（F1-F5 分表+计数、停复电停电/复电徽标）
- 脚本版本号 v3-0027 防浏览器缓存

**验证**：
- 单测 test_expect_rules.py 14 项全绿（规则/档位/否认码/auto_expect/显式覆盖/滑窗峰值并发/槽位三路径/聚合口径）
- simcon 全量回归：142 passed；3 失败为存量（干净树同样失败：api_cli 2 项端口映射 + responder_matcher 14F2 时钟），real_com_pair 4 skipped 需硬件
- 浏览器实测（COM1 虚拟对，无对端）：03H-F4 下发→等待卡（default·5s 倒计时）→超时卡（预期应答未到+规则 er.03 出处）；并发任务 4 表最大并发 2 → 在途 2/排队 2 实时、59s 档超时释放、抄读汇总成功率 0% + timeout 细分正确
- 真机链路（真实 CCO 应答/否认/停复电事件）待硬件在位时按 TODO 阶段 5 清单补测

**坑记录**：
1. `from __future__ import annotations` 下，FastAPI 路由的 Pydantic 模型必须定义在模块级——函数内定义的类无法被 get_type_hints 解析，会被当成 query 参数（报 "Field required"）。
2. 表地址按帧原始字节子串匹配会被信息域 R 的 seq/时间戳字节伪命中（如 seq=1 → "000000000001"），必须按解析结构（嵌套 645 地址域 / 链路层地址域 A）匹配，或至少扣除信息域后再搜。
