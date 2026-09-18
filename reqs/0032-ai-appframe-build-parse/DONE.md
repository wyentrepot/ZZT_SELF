# REQS-0032 — DONE（完成日志，只追加，最新在上）

## 2026-09-17 ｜ P3-P4 交付：技能轻量档 + 三副本同步（需求全部完成 ✅）

**版本调和结论（TODO P3 首项，留痕）**：全局库 v2.5.0 为**陈旧未提交态**（缺
2026-09-14 实测校准、cursor_range 口径、not_seen 闭合窗、shutdown/recipes 路由行），
以工作区 v2.4.0 为合并基；全局库仅有 2 处 repo-true 增量被拣入工作区
（listener `/api/concurrent/stats` 行——apps/listener/app.py:590 核实；
features 周期统计/深化应用两行——sim_concentrator/api.py 核实）。

**改动（工作区副本，版本 2.4.0 → 2.6.0）**：
- SKILL.md：新增「用途路由（渐进式加载）」表（给帧解析/构帧/回验 → 只读轻量档，
  不触 references）+「日常轻量档」自包含小节（三条命令 + 覆盖范围 + 双模式容错 +
  退出码 + 库级 API）；frontmatter description/argument-hint 扩展帧工具触发词；
  applies-to 补注 D:\019-wy-tool\ZZT_SELF（解析网关明文区）。原有 v2/v1 内容零删改。
- references/api-contract.md：§3.2 拣入 `/api/concurrent/stats` 行；
- references/features.md：拣入两行 + 版本引用更新 v2.6.0。

**同步与验证**：
- 三副本一致（diff -r 为空）：工作区 `.agents` ↔ 全局库
  `/home/02-skill-fc/skills/shared/hardware-in-the-loop` ↔ 运行时 `/root/.dsh/skills`；
- 全局库提交：`ad92506`（技能 v2.6.0）+ `31343a2`（reqs/0003 交叉登记），
  仅暂存本技能路径，未触碰其工作树其他未提交改动；
- 技能校验：`verify_api_inventory.py` **PASS**（exit 0；环境缺 `regex` 已补装）；
- 本会话技能目录实时加载出 v2.6.0 新 description——渐进装载链路实测生效；
- 回归：`pytest libs/parser_lib -q` 321 passed（P4 复核）。

**遗留**：无。645/698 更广语义构帧集（SET/代理等）按需扩展（链路层 build_frame 已就绪）。

---

## 2026-09-17 ｜ P0-P2 交付：离线解析/构帧/回验工具（基线 v1.2）

**产物（全部新增文件，零改动既有代码）**：

| 文件 | 说明 |
| --- | --- |
| `libs/parser_lib/facade.py` | decode/build/verify 门面：日常路由仅 {645, 698.45, 1376.2}；自动模式严格（阈值 0.8，垃圾帧输出"无法识别+得分"，拦截现路由兜底"双模4-3"的缺陷）；指定模式宽容（允许错误帧，校验失败走 warnings，异常捕获不裸崩） |
| `libs/parser_lib/cli.py` | parse/build/verify 子命令，JSON stdout；退出码 0/1/2（业务失败/用法错误） |
| `libs/parser_lib/test_facade.py` | 16 用例：三协议自动识别、嵌套递归、错帧双模式、645 DI 构帧 round-trip、698 GET-RequestNormal、**1103 并发抄表 F1H-F1 构帧样本**（simcon batch.py 同口径 appdata） |
| `libs/parser_lib/test_cli.py` | 8 用例：含 `tools/scripts/appframe.py` 启动器 subprocess 冒烟 |
| `tools/scripts/appframe.py` | AI 免配置入口（自插 apps:libs sys.path，模式同 compare_hplc_parser_runtimes.py） |

**验证证据**：
- `python3 -m pytest libs/parser_lib -q` → **321 passed, 66 skipped**（含新增 24 用例，存量零回归）；
- 启动器实测三场景：1376.2 自动解析（confidence 1.0，nested 645 递归解出）、
  645 构帧（`68123456789012681104333334331B6816`，CS 0x68 正确转义 1B68）、
  垃圾帧自动拒绝（scores={645:0.4,...} + hint 引导 --protocol）；
- 1103 样本：build("645") → AFN=F1/F1 包裹 → verify 自动回验 `verified=true`、nested=1。

**构帧能力口径（PLAN 降级预案未触发）**：1376.2 = build_frame_json 全量语义透传；
645 = addr(12位BCD传输序)+control+di(小端+33H)/data 语义层（0x03/0x08 按协议不加33H，
统一 1BH 转义）；698 = SA 完整域 + oad→GET-RequestNormal(05 01+OAD+无时标) / apdu 透传。
645/698 更广语义集（SET/代理等）留待按需扩展，链路层 `build_frame` 已就绪。

**遗留（→ P3/P4，另行推进）**：ai-control-plane 轻量档 + 按用途渐进加载路由表 +
工作区/全局库（v2.4.0 vs v2.5.0 漂移调和）双副本同步。
