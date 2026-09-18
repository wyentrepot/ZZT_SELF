# REQS-0032 — TODO

> 基线：REQS.md v1.2（2026-09-17）。细粒度实现步骤见 PLAN.md（动工前由 writing-plans 生成）。

## 阶段划分

### P0：方案评审与现状盘点
- [x] CLI 形态定稿：**门面+CLI 主体落 `libs/parser_lib/`（facade.py + cli.py，
      单测随库 test_facade.py）**；AI 免配置入口落 `tools/scripts/appframe.py`
      （薄启动器，自插 sys.path 转调 parser_lib.cli，模式同 compare_hplc_parser_runtimes.py）；
      parse/build/verify 子命令、JSON 输出到 stdout、`--protocol` 可选（不指定=自动嗅探）
- [x] 自动识别阈值规则（2026-09-17 实测发现）：CS/FCS 非法的帧三协议全 0 分时，
      现路由会兜底给"双模4-3"（0.4 松头部分）——自动模式必须改为输出
      「无法识别 + 各协议得分 + 建议 --protocol 重试」，禁止兜底硬解；
      **指定模式（--protocol）允许错误帧**：强制 decode、校验失败走 warnings
      （适配器已实测支持），异常帧由门面捕获输出错误 JSON
- [x] 构帧现状盘点：1376.2 adapter build / scenario_codec / simcon 构帧能力清单；
      645、698 应用层构帧缺口清单（常用帧集定稿）
- [x] 解析出口盘点：decode_13762 / 三适配器 decode / ProtocolRouter 嗅探——确认统一
      门面只做薄包装
- [x] 技能轻量档形态拍板：SKILL.md 内嵌小节 + 脚本入口（倾向），或独立技能
- [x] 回验 JSON 契约定稿（ok / 字段 diff / warnings / 意图比对）

### P1：应用层解析接口（免工作台）
- [x] 统一 decode 门面（hex/bytes → JSON）：1376.2（nested 698/645 递归）、698、645
- [x] 自动嗅探 + 手动指定协议两种模式
- [x] 单测：真实样本回放（至少含 1103 并发抄表样本）+ 三协议各自用例

### P2：应用层构帧接口 + 帧验收（回验）
- [x] 统一 build 门面（语义参数 → 帧 hex），依据知识库蒸馏卡口径
- [x] 统一 verify 门面（帧 hex → 解析回验 JSON：ok/差异清单/warnings）
- [x] round-trip 全绿：构帧产物全部回验通过
- [x] 单测：构帧用例（对照蒸馏卡帧格式逐字段断言）+ TDD 场景示例

### P3：ai-control-plane 技能范围修改（轻量档 + 渐进加载 + 双副本同步）
- [x] 版本调和：工作区 v2.4.0 与全局库 v2.5.0 漂移核对，定合并基（留痕于 PLAN/DONE）
- [x] SKILL.md 新增「日常轻量档」+ **按用途渐进式加载路由表**
      （给帧解析/构帧/回验 → 只读轻量档；全功能任务 → 对应 reference）
- [x] 全功能档（v2 门面/v1 专家路径）保留并标注启用条件，不删不改语义
- [x] 技能文档补最小用法三例（解析/构帧/回验）+ 仓库根与 PYTHONPATH 约定
- [x] 与既有 reference 的交叉引用（listener/network 文档标注"网络层帧走全功能档"）
- [x] 双副本同步：工作区副本 + 全局技能库副本
      `/home/02-skill-fc/skills/shared/hardware-in-the-loop/ai-control-plane`
      同版本落地；全局库独立提交并按其仓库规范登记（reqs/0003 关联）

### P4：验收与收口
- [x] REQS.md 验收清单逐项核对
- [x] 双副本一致性校验：version 一致 + diff 结论写入 DONE.md
- [x] 渐进加载演练：仅"给帧解析/构帧"场景按路由表走通，不触全功能 references
- [x] 回归：相关既有测试全绿，无口径分叉
- [x] DONE.md 记录 + REQS-INDEX.md 状态更新
