# tools/taiti/分钟采集 — 分钟采集（MCLT）离线分析工具集

> HPLC 分钟采集（MCLT，15 分钟冻结周期）三端离线分析：侦听台 7E 空中报文、
> CCO 串口日志、页面导出 xlsx。纯文件分析，**只读日志、不碰串口/硬件/烧录**。
> 与 `tools/taiti/高频采集/` 是姊妹工具（高频采集分析失败表定位，本工具分析
> 分钟采集的主动上报时序/补抄/漏报对账/UT 校准参数）。

## 能分析什么（边界一）

| 能力 | 命令 | 输出 |
| --- | --- | --- |
| 11E4 主动上报时序 | `sniff` | 每冻结周期的 jitter 分布（相对周期结束边界：min/P50/均值/P95/max、±3s 占比、提前占比、直方图） |
| 每表上报次数（重传率） | `sniff` | (表,冻结) 对出现 1/2/3/4 次的分布与重传率 |
| CCO 三通道 + 告警 | `cco` | 11E4/11E3请求/11E3响应/06F230 计数；drop stale / rejected / result fail 按分钟直方图 |
| 补抄请求→响应延迟 | `cco` / `pair` | 11E3 请求→响应延迟分布（按回显序号 100% 精确配对）；空中(7E)交叉验证 |
| 两端时钟偏移 | `pair` | 同一 11E4 在 CCO 日志与侦听台捕获的时刻差分布 |
| 页面实收 | `page` | 冻结周期 → 实收表集合（xlsx 归一化） |
| 漏报三端对账 | `miss` | 页面缺失表 × 空中11E4 × CCO收/丢 × 补抄 × 06F230 → 根因证据链 |
| UT 校准参数 | `utcalib` | 一键输出 mclt_ut_sched.h 所需：jitter/重传/响应延迟/时钟偏移/ssn 段 |

## 什么时候用（边界二）

- **适用**：分钟采集（MCLT，15 分钟冻结周期）场景——
  ① 漏报排查（页面缺表，需要三端对账定位根因）；
  ② 主动上报行为核查（STA 是否按时上报、jitter、重传）；
  ③ 补抄机制分析（11E3 请求→响应延迟、响应被拒、耗尽）；
  ④ mclt_ut 单元测试校准参数提取（jitter 分布、UT_STA_RSP_DELAY_MS、cco_offset_s、UT_SSN_BASE）。
- **不适用（请勿使用本工具）**：
  - 高频采集失败分析（抄读超时/补抄超 3 次）→ 用 `tools/taiti/高频采集`（技能 hf-collect-analysis）；
  - 协议细节/状态机字段咨询 → 用 knowledge-lookup 查协议蒸馏库；
  - 拉合闸/停复电/入网/升级/档案异常 → 各自专项工具；
  - 需要在线操作（串口、烧录、发帧）→ 本工具不提供，属 ai-control-plane 工作台能力。

## 输入要求（边界三）

| 输入 | 格式 | 说明 |
| --- | --- | --- |
| 侦听台 7E 报文 | `[     1][09:15:57.280]7E FF 02 ... 7E` | 一帧一行；hex 可带空格/大小写；**唯一时间基准** |
| CCO 串口日志 | `[20260920-09:16:15:603]49440413 | info | ...` | 括号时间为打印时刻（晚于空中 1~3s，洪峰更长）；行可能被截断（~30B） |
| 页面 xlsx | `Sheet1` 第 3 列冻结周期、第 4 列表地址 | 日期 `2026/9/20` 不补零，工具自动归一化 |

## 用法

```bash
cd tools/taiti/分钟采集
python3 run.py sniff <7E报文>                     # jitter + 重传率
python3 run.py cco <CCO日志>                      # 三通道 + 告警 + 补抄延迟
python3 run.py page <页面xlsx>                    # 页面实收
python3 run.py pair <CCO日志> <7E报文>            # 两端时钟偏移 + 补抄延迟交叉
python3 run.py miss <CCO日志> <7E报文> <页面xlsx> [--freeze "2026-09-20 10:15"]  # 漏报对账
python3 run.py utcalib <CCO日志> <7E报文> [--freeze "2026-09-20 10:15"]          # UT 校准参数
```

> 依赖：Python 3.10+；`miss`/`page` 需 openpyxl（系统 python3 已带；
> ZZT_SELF 的 `.venv` 未装 openpyxl，跑页面相关命令时请用 `python3` 而非 `.venv/bin/python`）。

## 自测

```bash
python3 -m pytest tools/taiti/分钟采集/test_mclt_analyze.py -q
```

`samples/` 为真实数据精简样例（侦听台 10:29:58~10:30:03 洪峰、CCO 09:29:00~09:30:12
补抄/上报窗口、页面 10:15 实收 149/151 缺 2 块），可当示例跑：

```bash
python3 run.py miss samples/cco日志_分钟采集_精简.log samples/侦听台报文_分钟采集_精简.txt \
  samples/页面_分钟采集_精简.xlsx --freeze "2026-09-20 10:15"
```

## 分析口径与踩坑

详见同目录《分析规则.md》——时间基准（侦听台最准）、帧特征（11E4/11E3/06F230
冻结偏移）、11E3 序号回显配对、时钟偏移≠RTC 偏差、CCO 日志截断/双打印、
"drop stale 不带 MAC"等全部踩坑记录。
