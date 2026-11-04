# minute_collect — HPLC 分钟采集台架验证脚本索引（req006 / REQS-0037）

> **分钟采集台架验证先查这里，不要重新造脚本。**
> 本目录是 req006 / REQS-0037（重庆 STA 分钟采集 11E3 补抄 + 698 分帧拉块）
> 端到端验证沉淀的可复用脚本。流程与语义见
> `/root/.dsh/skills/wb-simcon/SKILL.md` §4.1 与 `/root/.dsh/skills/wb-simeter/SKILL.md` §4.1；
> 场景定义在 `apps/workbench/scenarios/`（引用见下表）。

## 脚本清单

| 脚本 | 用途 | 在线依赖 |
| --- | --- | --- |
| `mclt_list_query.py` | 经 CCO 串口会话发 `sta show mclt_list` 并解析 n 与表体行（固化此前手搓 curl+python 流程） | **要**（工作台 8790 + cco-main 可用） |
| `build_8505_block.py` | 698 内层 85 05 分块响应帧构造器：PIID-ACD→lastblock→block→CHOICE→data→9B 尾，链路层复用 `parser_lib.adapters.adapter_698.build_frame`（SA=0x05+6B、CA、HCS/FCS 与台架证据帧逐段同构） | 否（纯离线） |
| `build_link_frames.py` | 链路层分帧序列构造器：完整 APDU → 起始 00 / 中间 11 / 末尾 01 分片（2B 分帧格式域，`--mtu` 控片长）。**只出 APDU 片，实机注入用 `build_split_frames_full.py`**（坑 6） | 否（纯离线） |
| `build_split_frames_full.py` | 698 链路层分帧**完整帧**构造器：APDU → 分片 → `68\|L\|C=0xE3\|SA 7B\|CA\|HCS\|2B 分帧格式域\|片\|FCS\|16` 整帧；golden 自检（22:30 已验证三帧字节级基准，MISMATCH 即 exit 1）+ HCS/FCS 回验 + 分片合并检查；默认 APDU 即 23:30 轮实机通过内容 | 否（纯离线） |
| `inject_8505_blocks.py` | 85 05 分块注入编排：等 simeter /frames 出现 85 05 GET 请求 → build_8505_block 逐块构帧 → `POST /api/simeter/send_hex` 按序注入 → 核对 tx 方向应答落帧 | **要**（simeter 串口已 open） |
| `watch_inject.py` | 分帧应答守望注入：等 simeter /frames 出现 STA 698 请求（默认前缀 68310043）→ +0.30s 按序 send_hex 注入 `--frames` 帧文件（单轮触发，超时 exit 2） | **要**（工作台 8790 + simeter 串口已 open） |

## 最短调用路径（下次验证分钟采集分帧拉块）

```bash
cd /01-workfile-ai/01-zzt/ZZT_SELF

# 0) 前置：工作台在线（8790）；开 simeter 口（wb-simeter §4）
# curl -X POST http://127.0.0.1:8790/api/simeter/open \
#   -H "Content-Type: application/json" -d '{"mapping_id":"listener"}'

# 1) 查 CCO 侧分钟采集列表（谁在网、req_ssn/last_ssn、冻结时刻）
python3 tools/scripts/minute_collect/mclt_list_query.py cco-main --json

# 2) 配任务 + 等存数 + 11E3 读数（场景化跑法，见 scenarios/chongqing_mclt_recheck_0037.json）
#   经 wb-simcon verify 任务面提交（skill 里 §4.1 组合示例），或：
#   curl -X POST http://127.0.0.1:8790/api/ai/v1/simcon/verify -d @<场景 steps>

# 3) 离线构 85 05 分块应答帧（block0 非末 / block1 末块）
python3 tools/scripts/minute_collect/build_8505_block.py \
    --piid 00 --lastblock 0 --block 0 --data 1120
python3 tools/scripts/minute_collect/build_8505_block.py \
    --piid 00 --lastblock 1 --block 1 --data 3301

# 3b) APDU 超长时分帧（可选；单帧装得下时不需要）
#   注意：这里出的是 APDU 片（1B 段标记+片），实机 send_hex 注入要用 3c 的完整帧（坑 6）
python3 tools/scripts/minute_collect/build_link_frames.py --apdu <APDU hex> --mtu 200

# 3c) 链路层分帧注入复测（698 分帧拉块；2026-11-03 23:30 轮实机通过，坑 5/6/7）
#   ① 出帧：默认 APDU 即 23:30 轮已验证内容（OAD=20000201/20010201 与任务配置匹配）
python3 tools/scripts/minute_collect/build_split_frames_full.py --out /tmp/frames_split.txt
#   ② 置 missing（simeter 脑静默不回，迫使 STA 请求走注入路径，坑 7）
curl -X POST http://127.0.0.1:8790/api/simeter/oad \
    -H "Content-Type: application/json" \
    -d '{"entries":[{"oad":"20000201","missing":true},{"oad":"20010201","missing":true}]}'
#   ③ 后台守望注入（等 STA 68310043 请求 → 按序注入，单轮触发）
python3 tools/scripts/minute_collect/watch_inject.py \
    --frames /tmp/frames_split.txt --timeout 1200 > /tmp/inject_split.log 2>&1 &
#   ④ 等 15min 冻结边界 → 查 STA 日志出现 "Freeze data for task 1 stored successfully"
#     （链路层看 STA 回 2 帧类型 10 确认帧 + 末帧合并，simeter /frames 里 rx 可见）
#   ⑤ 恢复 OAD（清运行时层，回落文件层）
curl -X POST http://127.0.0.1:8790/api/simeter/oad \
    -H "Content-Type: application/json" -d '{"reset":true}'

# 4) 一键注入编排（等 85 05 GET 请求 → 按块序注入 → 核对）
python3 tools/scripts/minute_collect/inject_8505_blocks.py \
    --blocks 1120 --data 3301 --wait-req 120

# 5) 断言/核对（离线）：libs/minute_assert（11E3/06H-F230/内层 698 全链路）
#   python3 -m pytest libs/minute_assert/ -q   # 63 项自检
```

## 与场景文件的关系

| 场景 | 内容 | 本目录配合点 |
| --- | --- | --- |
| `apps/workbench/scenarios/chongqing_mclt_recheck_0037.json` | 0037 复测：配 698 任务（F231/F232）→ `wait_mclt_stored` → `read_mclt_11e3` 按 0/1/15 任务读数 | 断言层走 `libs/minute_assert`；本目录脚本补"内层 698 深度分块"环节 |
| `apps/workbench/scenarios/minute_collect.json` | 安徽版分钟采集基础场景 | 同上 |
| （新增建议）`chongqing_mclt_698_split.json` | 698 分帧拉块专项：配任务 → 等存数 → 85 05 分块应答（simeter 无分块配置模式时用本目录 `inject_8505_blocks.py` send_hex 注入）→ 断言 STA 05 05 回帧与拼块落盘 | 直接调用本目录全部脚本 |

## 字段口径速查

- **85 05 APDU**：`PIID-ACD(1B) → lastblock(1B, 0/1) → block(1B) → CHOICE(01) → data`，
  依据 DL/T 698.45 表 76"读块"结构；后面挂 9B 尾（`00 00 01 00 04` + 4B 时间戳占位，
  `more_frame_698_ack_struct` 同款，`minute_assert/mclt_698.py` 的
  `apdu_len_has_tail` 口径）。
- **链路层**：`68 | L(2B LE) | C | SA(0x05+6B addr) | CA(1B) | HCS(L..CA) |
  APDU | FCS(L..APDU) | 16`；C=0xC3 应答 / 0x43 请求；SA 表地址 000000000001
  → `05 00 00 00 00 00 01`（BCD 低位在前），与台架证据帧
  `683400c3...`（`minute_assert/test_mclt_698.py` INNER_698_TASK1）逐段同构。
  HCS/FCS 覆盖窗口以 `parser_lib.adapters.adapter_698._check_fcs` 为准
  （HCS=frame[1:hcs_pos]，FCS=frame[1:-3]）。
- **分帧格式域**：实机验证的完整帧口径（`build_split_frames_full.py`）为
  2B：b0 = seq 低 8 位，b1 高 2 位 = 帧类型（00 起始 / C0 中间 / 40 末尾）+
  低 4 位 = seq 高位；`build_link_frames.py` 输出的是 1B 段标记（00/11/01）+
  APDU 片，仅语义对照用，不能直接注入（坑 6）。
- **11E3 APS 域构帧**：已有 `libs/sim_concentrator/mclt_e3.py`（24B 下行 +
  上行解析），不重复造；本目录只在"内层 698 分块"上补位。

## 已核实/踩过的坑

1. **11E3 不可由 simcon 交互口注入**（2026-11-03 台架×2 实测）：CCO 冻结后约
   9min 自主发起；正确姿势 = 配任务 → `wait_mclt_stored` → 等 CCO 自主补抄。
2. **85 02 ≠ 85 05**：`minute_assert.parse_inner_698` 只认 `85 02 01`（GET-Response
   WithList，即采集应答），85 05（读块）要走本目录构造器 + `_check_fcs` 回验，
   不能直接套那个解析器。
3. **SA 域是 7B 不是 6B**：首字节 0x05 是地址长度/类型头，后面才是 6B 地址；
   少算 2 字节会把 HCS/SEC/APDU 全体错位（第一版构造器踩过）。
4. **`sta show mclt_list` 回显落日志有延迟**：send 返回≠表体已打印，
   `mclt_list_query.py` 里 `REPLY_WAIT_SECONDS=3` + 重试就是为此。
5. **分帧注入帧只过 HCS/FCS 不够**：APDU 内容（OAD/计数）必须与 CCO 任务配置
   匹配。2026-11-03 22:30 轮旧帧 OAD=22000201/计数=01 与任务
   （20000201/20010201）不匹配 → 链路层 ACK 正常，但末帧合并后解析跳过、
   无 Freeze stored；23:30 轮用正确 APDU（`build_split_frames_full.py`
   默认值）复测通过（STA 回 2 帧类型 10 确认 + 末帧合并 +
   `Freeze data for task 1 stored successfully`）。
6. **`build_link_frames.py` 输出的是 APDU 片（1B 段标记+片），不是完整 698
   帧**；实机 send_hex 注入要用 `build_split_frames_full.py` 出的完整帧
   （含 68 头/HCS/FCS/2B 分帧格式域）。
7. **simeter 脑已支持 698 采集 OAD 自动应答**（`POST /api/simeter/oad`
   配数据集，missing=true 静默）——"脑不会自动应答 698" 的旧结论已过时
   （REQS-0037）；要复测注入路径就把采集 OAD 置 missing，测完 reset 恢复。

## 维护约定

- 本目录脚本遵循 `tools/scripts/` 现有风格：python3 / argparse / `--json` 可选 /
  可执行权限；纯离线脚本不 import 工作台，联调脚本统一 `WORKBENCH_BASE` 环境变量。
- 构帧事实源：链路层 = `libs/parser_lib/adapters/adapter_698/build_frame`；
  11E3 = `libs/sim_concentrator/mclt_e3.py`；断言 = `libs/minute_assert`。
  本目录不复制它们的代码，只 import。
- 改了构帧口径 → 先跑 `python3 -m py_compile tools/scripts/minute_collect/*.py`
  + `libs/minute_assert` 全测（63 项）+ 本 README 的"最短调用路径"手工过一遍。
- 2026-11-03 新增的 `build_split_frames_full.py` / `watch_inject.py` 同样遵循
  上述约定：离线构帧只 import `adapter_698`（不复制构帧代码，golden 自检兜底
  字节级一致性）；联调脚本统一 `WORKBENCH_BASE` 环境变量；改口径先过 golden
  再实机复测，实机验证帧留档 `/tmp/req006_evidence/`。
