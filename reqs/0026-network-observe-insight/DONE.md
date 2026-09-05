# DONE — REQS-0026 组网观测价值化

> 只追加，最新在上。

## 2026-09-05 ｜ REQS-0026 全部完成（G1-G5）

**后端（apps/listener/nwk_service.py + app.py）**
- 事件三级分级 `classify_level`：alarm（入网被拒 rslt∉{0,0xA} / 离网指示 / NID·信道冲突 / 路由错误 / BPCS 失败）、watch（代理变更 / 网间协调 / 成功率上报 <90%）、常规（其余）；门限出处（蒸馏库 07-NWK/08-MAC）已注释标明；parser_lib 保持纯协议未动。
- 人话翻译 `_humanize` + `_station_label`：TEI 档案（从 assoc_req/70/76 的 fields_json 实时聚合，无新表）把 TEI 翻成表号/MAC，如「表 300009049239 入网被拒：不在白名单，退避 600s 后重试」；list_events 输出附 level/human/src_label/dst_label，新增 `level` 过滤参数。
- `GET /api/network/digest`（≤4KB）：verdict 一句话判定 + 异常/关注清单（类型/计数/首末时间/人话示例）+ 网络概要 + 质量计数 + 自适应时间桶（≤30min 用 1 分钟粒度，更大归并 ≤60 桶，只返回非空桶，桶内含异常数）。
- `GET /api/network/events/{frame_id}/brief`（≤2KB）：adapter_dualmac 现场解单帧，分层中文摘要（GW 封装/MAC 头/管理表·信标/选择确认/业务载荷，管理字段中文名映射）+ 事件人话 + warnings；404/422 语义。
- 存量库迁移：nwk_events 补 level 列 + 全量回填（老索引直接可用）。

**前端双副本（独立版 8765 + workbench listener 页，同步脚本参数化 API 前缀）**
- 顶部印象结论卡：verdict（按 level 着色）+ 异常/关注 chips（点击聚焦事件类型）+「信道质量分 → 网络承载评估」分工跳转。
- 时间桶导航条：桶高=事件数、异常桶标红显示异常数；点击桶锁定时间窗（12 字符毫秒精度，规避 _time_range_bound 字符串比较丢毫秒问题）、再点取消；空时段不渲染。
- 事件表降噪：默认只显异常+关注，「显示常规」开关；新增级别列（分级色+行左缘色条）；摘要列用 human，源/目的列用翻译标签。
- 行内粗略解析：点击事件行展开 brief 面板（懒加载 + 60 条缓存，一次只展开一行），替代原 JSON dump 底部面板。
- 卡片去重（G5）：删除「实测信标周期」卡（评估页已有），保留事件侧统计 + 新增「异常事件」卡。
- 评估页周期表：非健康周期行加「下钻排查」按钮，带周期时间窗跳转组网观测（仅 fault/degraded 显示）。

**AI 消费路径**
- ai-control-plane v2.3.0：SKILL.md 路由表改为 digest→events(level)→brief 低 token 排查路径；references/network-diagnostics.md §0 取证路径重写为 L1 digest / L2 events / L3 brief / L4 评估，并声明 digest 分级为轻量内联版（门限同 §2 断言库口径）。
- docs/api-contract.md §3.4a、docs/features.md 同步（digest/brief 契约 + events level 参数 + 页面分工说明）。

**验证**
- test_nwk_service.py 15 用例全绿（新增 8 用例：分级纯函数 / 翻译装饰 / level 过滤 / digest 无异常·有异常 / 时间桶自适应·跨午夜 / brief 分层·体积·404 / 老库迁移回填）。
- 全量回归：listener + parser_lib 571 passed / 62 skipped / 3 failed——3 个失败均为 test_ui_layout 存量（0024 基线复现同名）。
- 真实链路（300 帧真机样例日志）：digest 958B、brief 674B、level 过滤、时间桶锁定、降噪开关（193 常规+7 关注）、分工跳转均浏览器实测通过；评估页 4 周期全「健康」时下钻按钮按设计不显示。
- 双副本一致性：app.js（去前缀后逐字节一致）/ styles.css / 组网 section 校验通过；node --check 语法通过。

**遗留 / 边界**
- digest 的 alarm/watch 上限 6/3 类（体积口径），更多类型按计数截断。
- 「显示常规」关闭时事件表上限 200 条提示总数。
