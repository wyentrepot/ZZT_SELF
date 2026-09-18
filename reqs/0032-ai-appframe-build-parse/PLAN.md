# PLAN — REQS-0032 P0-P2：离线解析/构帧/回验工具（本轮执行）

> 依据基线：REQS.md **v1.2**（2026-09-17，变更 3）。状态：**P0-P2 已执行完毕（2026-09-17）**，回归 321 passed。P3（技能双副本同步）不在本计划，
> 待本计划验收后另行规划。

## 目标与产物

AI 日常应用层帧工具：1376.2（内嵌 698/645 递归）/698.45/645-2007 的
**解析（hex→JSON）/构帧（语义→hex）/回验（round-trip 验收 JSON）**，
免工作台、免 8700/8790，脚本直用，JSON 输出到 stdout。

| 产物 | 路径 | 职责 |
| --- | --- | --- |
| 门面 | `libs/parser_lib/facade.py` | decode/build/verify 统一入口；双模式容错（自动=严格阈值 0.8；指定=宽容允许错误帧）；仅路由 {645, 698.45, 1376.2}，不含双模/GW |
| CLI | `libs/parser_lib/cli.py` | `python3 -m parser_lib.cli parse\|build\|verify`，argparse，JSON stdout |
| 单测 | `libs/parser_lib/test_facade.py`、`libs/parser_lib/test_cli.py` | RED→GREEN 全程驱动 |
| AI 入口 | `tools/scripts/appframe.py` | 薄启动器：自解析仓库根、自插 apps:libs sys.path，转调 `parser_lib.cli.main()` |

## 现状盘点结论（P0，已实测 2026-09-17）

- 解析：`build_adapters()` + `ProtocolRouter` 可用；真实帧自动识别
  645/698.45/1376.2 全部得分 1.0 选中正确；**全 0 分时现路由兜底"双模4-3"（0.4）——门面必须设阈值拦截**。
- confidence 取值域：645 {1.0, 0.4(CS错), 0.3, 0}；698 {1.0, 0.85, 0.5, 0.3, 0}；
  → **阈值定 0.8**（0.5/0.4/0.3 的校验失败帧在自动模式拒绝，指定模式放行）。
- 构帧：1376.2 有完整语义构帧 `adapter_10376.build_frame_json(req)`（复用）；
  645 仅链路层 `build_frame/_escaped(addr,control,data_domain)`（门面包语义层：
  地址 BCD 串反序、DI 小端 + 33H、控制码透传）；698 仅链路层
  `build_frame(apdu,addr,ca,control)`（门面包最小语义层：Get-RequestNormal
  0x05 0x01 + OAD + 无时标，APDU hex 透传兜底）。
- 错帧宽容：`adapter.decode` 对错 CS/残缺帧不抛异常、warnings 表达（已实测）。

## 任务清单

### T1 RED：门面解析单测（自动+指定+错帧）
- [x] 写 `libs/parser_lib/test_facade.py`：auto 645/698/1376.2(nested≥1) 命中正确协议；
      垃圾帧（645 错 CS、截断 698）→ `ok=false, error=unrecognized, scores={...}, hint`；
      指定模式错帧 → `ok=true` + warnings 非空；1376.2 未知协议请求 → 明确报错。
- [x] 命令：`python3 -m pytest libs/parser_lib/test_facade.py -q` → **预期全红**
     （`ModuleNotFoundError: parser_lib.facade`）。

### T2 GREEN：facade.py
- [x] 实现 `decode(frame, protocol=None, auto_min=0.8)` → dict；
      ProtocolFrame→JSON 通用转换（fields/items/nested 递归、warnings）；
      自动模式只在 {645, 698.45, 1376.2} 内选，低于阈值输出 scores；
      指定模式强制 decode，异常捕获为 `{"ok": false, "error": ...}`。
- [x] 验证：同命令 → 全绿。

### T3 RED→GREEN：构帧 + 回验
- [x] RED：test_facade.py 增 build/verify 用例——645 读 DI（构→解回 DI/CS 一致）；
      1376.2 `build_frame_json` 透传（AFN=10/F1 构帧成功）；
      698 Get-RequestNormal（构→解回 APDU 服务标签 0x05）；
      verify 对构帧产物 round-trip `verified=true`；expect 字段比对含
      matched/mismatched 两分支。→ 预期红（AttributeError: decode 系列缺 build/verify）。
- [x] GREEN：facade.py 增 `build(protocol, params)`、`verify(frame, protocol=None, expect=None)`；
      验证同命令全绿。

### T4 RED→GREEN：CLI + 启动器
- [x] RED：`libs/parser_lib/test_cli.py`——`main(["parse","--hex",...])` 返回 0 且
      stdout JSON ok=true；`--protocol` 传错帧得 warnings；build/verify 子命令可达；
      非法 JSON 参数 exit≠0 且 stderr 有因。→ 预期红（无 cli 模块）。
- [x] GREEN：`libs/parser_lib/cli.py`（argparse 子命令，hex 归一化复用
      `shared.parser_service.normalize_hex_frame` 的宽松口径或本地等价实现——
      **不得 import shared**（库层禁依赖 apps/shared），本地实现 strip/去空格/大写）。
- [x] `tools/scripts/appframe.py` 启动器 + 冒烟：
      `python3 tools/scripts/appframe.py parse --hex "6812345678901268910833333433AB896745CC16"`
      → JSON `ok=true, protocol=645`。

### T5 回归与收口
- [x] 全量回归：`python3 -m pytest libs/parser_lib -q` 全绿（存量测试无回归）；
      `python3 -m pytest apps/parser_service -q` 无关不跑（不动其代码）。
- [x] 错帧双模式验收项（REQS §5）逐条核对；TODO.md 勾选；DONE.md 追加；
      REQS-INDEX.md 0032 状态更新。

## 回滚

全部为新增文件（4 个），不改任何既有代码；回滚 = 删除新增文件。
若 T3 发现 645/698 语义构帧缺口过大，降级口径：build 仅交付 1376.2 语义构帧 +
645/698 链路层透传（`data`/`apdu` hex 直构），语义缺口记入 DONE.md 遗留。
