# DONE — REQS-0025 网络层知识工程

> 只追加不覆盖，最新在上。

## 2026-09-05 — 收尾：全量回归 + 状态同步（Commit 4）

### 回归结果（范围化逐文件，单文件 90s 超时防串口类挂起）
- ✅ apps/workbench 全部 test_*.py：仅 `test_ai_store_query.py` 5 失败（06H-F230 event_type 命名/库内容断言）——**存量问题**：该文件与 dict_api/case_library/network_assert 零导入关系，失败模式与本需求改动面无交集（0026 未提交期 diff 亦未触及事件命名）。
- ✅ 新增库：libs/case_library 7 测、libs/network_assert 9 测全绿。
- ✅ apps/listener 核心（nwk_service 15 / network_assessment 56 / app 43 / trace_api 8）：全绿（含分支上 0026 已提交代码）。
- ✅ libs/minute_assert 26、loghooks 39、shared 65、gw_cass_migrate 10、parser_lib 301（62 skipped）、apps/module_log 84（2 skipped）。
- ✅ 前端门禁：`workbench.check_assets --strict` 25 资产完整；`node --check dict.js` 语法通过。
- 注：根目录一次性 `pytest -q`（1498 项）18 分钟未止（含串口/长等待用例），改用与历史收尾一致的范围化口径。

### 分支与并行说明
- 本分支 `req/0025-network-knowledge-pack` 按索引约定同时承载 REQS-0026 的提交（65435bc 代码 + cd1ffd9 文档，插在本需求 WP2 与收尾提交之间），两需求随本分支一并合入 master。
- **WP3 产物落点说明**：0026 会话提交 65435bc 时全量暂存了工作区，把本需求的技能改动（SKILL.md 路由行+版本 2.2.0、references/network-diagnostics.md 共 114 行）一并带入该提交——内容完整、与工作区一致（`git diff HEAD` 为空），不改写他人提交，以本条记录为准对账。
- 后续衔接建议（供 0026 合并后跟进）：0026 digest 的内联门限常量可在后续需求中改为引用 `libs/network_assert` 规则定义，消除双源漂移（两处已互加出处注释，短期无碍）。
- 遗留（留后续需求）：① `evaluate()` 求值接入 REQS-0024 事件流；② 269 体系 framework 级 49 条按原始文档补全为独立条目；③ test_ai_store_query 5 项存量失败独立排查。

## 2026-09-05 — 工作包 3/3 + 收尾：AI 诊断知识包 + 抽查验证（Commit 3）

### 交付
- `.agents/skills/ai-control-plane/references/network-diagnostics.md`：取证路径（network/status→events→beacons/assessment）、组网状态机速查（UNASSOC/OFFLINE/WAITON/ONNET + 9 种关联结果码表 + 退避档位）、门限速查表（与 libs/network_assert 8 条断言同口径）、信标/时隙速查、故障特征→根因映射 20 条（覆盖 TODO 要求的 8 类症状）、13 号差异记录精选 8 条。
- SKILL.md v1 路由表新增「排查组网问题」行；版本 2.1.0 → **2.2.0**。

### Step 3.4 抽查验证（0020 真机困难点 → 知识包走查，3/3 命中）
1. **症状「档案地址 0133 系列与 CCO 上行 645 内嵌地址 23121400XXX 不符」**（0020 第 2 轮）→ 映射表 #1：rslt=1 不在白名单，根因「档案地址与 STA 实际 MAC 不符；白名单池上电分批装载」——命中，与真机"读档案才拿到真实地址"的处理一致。
2. **症状「上报无确认 → rptlist_length:1024 暴涨、大量 frame duplicate」**（0020 第 5 轮）→ 映射表 #18：重复帧判重四要素（源 MAC+MSDU 序号+重启次数，128 项环形表）+ #19 缓存/队列生命周期——命中，解释了"无确认→CCO 缓存重发"的链路层表现。
3. **症状「侦听台抓到 HPLC 帧而非 1376.2 下行」**（0020 第 3 轮）→ §0 取证路径：HPLC 链路侧证据走 `/api/network/events`（group=信标/组网），集中器 1376.2 走 485 应用层——命中，AI 可据此选对数据源。

## 2026-09-05 — 工作包 2/3：网络层断言规则库（Commit 2，4730a5d）

- `libs/network_assert/`：schema.py 静态校验器（结构/枚举/阈值 unit+meaning/出处白名单——南网文档出处会被拒绝）+ core.py（load_rules/validate/describe/evaluate）。
- `rules/` 8 条断言声明（TODO Step 2.2 七门限族，成功率+离线率三级合并一条）：
  关联拒绝退避分档（40/60/600s，121s/1800s 边界，指数退避 16×2^cnt）、心跳 4 周期离网+10s 延迟删除、
  信标周期合法域（协议 1~10s / 评估参数 500ms~120s 双口径）、路由周期自适应 [50,720]s、
  心跳周期=路由周期×2/×4、CSMA 占用 60%/80% 两级、成功率 98/90% + 离线率 2/10% 三级、
  冲突仲裁时序（NID 307ms/30min、RF 20.7s/30min、t2=5min、切换 100s+确认 102s、邻居表 31、上电 60s 不检测）。
- 每条含触发条件/观测窗口/判定/阈值（unit+meaning）/出处（doc+section+quote）。
- `evaluate()` 预留接口：NotImplementedError 桩 + 契约 docstring（输入=REQS-0024 事件流/评估快照，verdict 允许 inconclusive）。
- 单测 9 项全绿。

## 2026-09-05 — 工作包 1/3：269 项检测用例库入库（Commit 1，efc6011）

- `libs/case_library/generate.py`：半自动转换蒸馏库 `06_测试用例.md`（国网口径，`南网/` 未引用）。
  解析器覆盖：§2.2 清单/§2.3~2.6 各测试表/§3 抄控器消息与流程/§4.1.3-4.1.4 扩展命令/§5 河南流水线/§6 各省对比。
- `data/cases.json`（随库分发）：**279 条 = 237 检测条目 + 42 参数表行**；
  分类计数 hplc-perf 7 / wireless-perf 11 / hplc-consistency 142 / wireless-consistency 39 / interop 11 / tester-protocol 22 / henan-pipeline 5 / params 42。
- 诚实口径：meta.declared 记录来源文档 269 项体系及其构成；蒸馏文档 §7.1 自述原文 286K 字符仅展开代表性条目，
  故 `detail_level=framework` 标注 49 条名称级条目（含 7 条无线对称组占位），其余 230 条 detailed 带目的/帧类型/步骤/判定/条款号/来源小节。
- Step 1.3 人工核对抽查：每分类随机 2 条（seed=7）核对名称/帧类型/步骤数与蒸馏 md 原文一致；
  生成器内置解析失败即 SystemExit（如检测线小节解析不出 OAD 字段）。
- `/api/dict/cases?category=&type=&q=` + `/api/dict/cases/{entry_id}`；字典页第 5 本字典卡 + 分类下拉 + 步骤/判定/参数详情渲染。
- 单测 12 项全绿（库级 7 + API 3 + 存量 test_dict_list 断言更新）。
