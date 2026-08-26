---
name: hf-collect-analysis
description: 'Analyze HPLC high-frequency collection failures in ZZT_SELF. Given three HPLC logs (台体 / CCO / 侦听台), cross-locate which meters fail to respond on time (超时未回、补抄超 3 次) and the root cause. Pure log-file analysis on the local repo using tools/taiti/高频采集; read-only over files, never touches serial/hardware. Use when user asks to analyze 高频采集失败/抄读超时/补抄超限/失败表定位 with logs, e.g. "分析这三份日志里哪块表抄读失败"、"定位高频采集补抄超过3次的表".'
metadata:
  author: reasonix
  version: "2.0.0"
  applies-to: /01-workfile-ai/01-zzt/ZZT_SELF
---

# 高频采集失败分析技能（hf-collect-analysis）

项目内、**显式调用**的 HPLC 高频采集失败分析技能。底层分析器在
`tools/taiti/高频采集/`（扩展系列，按日志来源分目录：`台体/`、`CCO/`、`侦听台/`，
纯 Python，复用 `libs/parser_lib` 与 `libs/sim_concentrator.frame_codec`）。
本文件是**执行步骤**：只读本地日志文件，不碰串口/硬件/烧录。

## 边界（先读）

- 本技能**只读日志文件**：不提供、不隐藏 `ensure/start/stop/send/flash/烧录/串口打开`。
- 输入是**既有日志文件路径**（台体 / CCO / 侦听台），不新建、不启动任何采集来源。
- 分析输出到终端/文件；默认不写入源日志。
- 只能通过 `$hf-collect-analysis` 显式调用；`allow_implicit_invocation: false`。

## 前置条件

1. 仓库可运行 Python（`python -m pytest` 可跑通
   `tools/taiti/高频采集/test_hf_collect_analyze.py`）。
2. 有分析所需日志（或其子集）：
   - **台体日志**：GBK 编码，含 `send cmd to cco` / `ReadMeter Success/Fail` / `read fail`
   - **CCO 日志**：UTF-8（带 BOM），行格式 `[YYYYMMDD_HH:MM:SS_mmm]#...`
   - **侦听台报文**：UTF-8，行格式 `[     1][HH:MM:SS.mmm]7E FF ...`
3. 三份日志时钟不同步时，**时间线一律以侦听台（纯硬件抓包）为基准**：
   侦听台最准；CCO `ApsReadRecord` 为上层处理时刻（比实际到达滞后 30~50s）；
   台体时间不可靠，只作"动作序列"识别。

## 四命令

| 命令 | 说明 |
|---|---|
| `taish <台体日志>` | 台体日志分析：采集帧、ReadMeter 成败、补抄次数、建档案帧、最终判定 |
| `cco <CCO日志> <表地址>... [--start ..] [--end ..]` | CCO 日志二次证据：命中/ApsReadRecord/aps tx |
| `sniff <侦听台报文> <表地址>... [--start ..] [--end ..]` | 侦听台 HPLC 报文二次证据：请求/应答帧 |
| `cross <CCO日志> <侦听台报文> <表地址>... [--start ..] [--end ..] [--cco-only\|--sniff-only]` | 失败表二次证据：CCO + 侦听台交叉验证 |

## 第一步：跑台体分析（先定位失败表）

```bash
cd tools/taiti/高频采集
python run.py taish "path/to/台体日志.log"
```

输出包含：
- `send 采集帧总数 / 涉及表`：哪些表被抄读、几次
- `ReadMeter Success 表` 与 `ReadMeter Fail`
- `补抄次数分布`：找 send>=3 且从未成功的表（= 失败表候选）
- `档案表`：建档案帧里的全部表地址（确认表在网）
- `最终判定`：如 `read fail(4)`、`read cycle reach max(3)`

## 第二步：交叉验证失败表（二次证据）

对台体分析出的失败表（如 `010000012201`、`020000012201`），用 cross 命令在
CCO 与侦听台日志中找证据：

```bash
cd tools/taiti/高频采集
python run.py cross "path/to/cco.log" "path/to/sniff.txt" \
  010000012201 020000012201 --start 16:42:00 --end 16:44:50
```

- CCO 侧：目标表命中行数、`ApsReadRecord` 成功回读次数、`aps tx` 发送次数
- 侦听台侧：目标表相关 HPLC 帧（请求/应答）及时间
- **判定口径**：
  - 只有请求帧/aps tx、无 ApsReadRecord/应答数据 → STA/电表未响应
  - 大量 retry/队列积压 → 网络拥堵
  - 时间窗：以侦听台时间为准，CCO/台体滞后量需折算

## 自带精简样例（自测/演示）

各子目录 `samples/` 有精简样例（台体/cco/侦听台三份，GBK 与 UTF-8 编码保留），
单测 `tools/taiti/高频采集/test_hf_collect_analyze.py` 用它验证。可直接当示例跑：

```bash
cd tools/taiti/高频采集
python run.py taish 台体/samples/台体日志_精简.log
python run.py cross CCO/samples/cco日志_精简.log 侦听台/samples/侦听台报文_精简.txt \
  010000012201 020000012201 --start 16:42:00 --end 16:44:50
```

## 分析口径（2026-08-20 实测结论，供复现）

失败表 `010000012201`/`020000012201`（0x22 网关下挂 698 电表）的完整证据链：
- 侦听台：012201 自采集开始被反复抄读 7+ 次，持续 ~2.5 分钟无应答，16:44:48 才首次回读
- 台体：`read cycle reach max(3)` 后放弃（对应侦听台约 16:44:0x），早于电表回读约 40+ 秒
- CCO：采集期无 012201 的 `ApsReadRecord`；16:43:55 才向 tei 0022 发送且 tx_cnt 3；回读滞后 30~50s
- 根因：**012201（0x22 网关）入网晚、采集时响应时延大，台体补抄窗口不足 → 超时判失败；非网络拥堵**（其余 92+ 只表均秒级成功）

## 错误处理

- 日志路径不存在 / 编码不对 → 脚本安全报错，非零退出。
- 时间窗参数格式必须 `HH:MM:SS`；不传则全量。
- 表地址必须 12 位 hex（如 `010000012201`）；脚本内部自动转小端在 CCO/侦听台侧匹配。
