---
name: mclt-collect-analysis
description: 'Use when 需求明确涉及 HPLC 分钟采集（MCLT，15 分钟冻结周期）分析：页面漏报/缺报表三端对账定位、11E4 主动上报时序（jitter/重传）、11E3 补抄请求→响应延迟、分钟采集两端时钟偏移、mclt_ut 校准参数提取（UT_STA_RSP_DELAY_MS/cco_offset_s/UT_SSN_BASE），且已有（或可定位到）侦听台 7E 报文与 CCO 串口日志路径。纯离线文件分析，只读日志。DO NOT load for 高频采集失败分析（用 hf-collect-analysis）、协议帧/字段/状态机咨询（用 knowledge-lookup）、拉合闸/停复电/入网/升级、或要求在线/实时分析。'
metadata:
  author: reasonix
  version: "1.1.0"
  applies-to: /01-workfile-ai/01-zzt/ZZT_SELF
---

> v1.1.0（2026-11-03）：REQS-0037 — 工具节新增 minute_assert 断言库入口（`assert_698_11e3_chain`，离线核 698 11E3 补抄帧）。

# 分钟采集分析技能（mclt-collect-analysis）

项目内、**显式调用**的 HPLC 分钟采集（MCLT）离线分析技能。底层分析器在
`tools/taiti/分钟采集/`（纯 Python，`run.py` 提供
`sniff / cco / page / pair / miss / utcalib` 六个命令）。
本文件是**执行步骤**：只读本地日志文件，不碰串口/硬件/烧录。

## 加载边界（先读，不满足即不加载）

**本技能仅在需求明确是"分钟采集（MCLT）/ 冻结周期 / 11E4 主动上报 / 11E3 补抄 / 分钟采集漏报 / mclt_ut 校准"时加载。** 判定要点：

- ✅ **加载**：提到 分钟采集/分钟级采集/MCLT/冻结周期（10:00/10:15/10:30 冻结）、
  11E4（0x00E4）主动上报时序、11E3（0x00E3）补抄响应延迟、分钟采集漏报/缺报三端对账、
  两端时钟偏移、mclt_ut / UT_STA_RSP_DELAY_MS / cco_offset_s 校准参数提取，
  且能给出（或定位到）侦听台 7E 报文与 CCO 日志路径。
- ❌ **不加载**（明确排除，加载后请停用）：
  - 高频采集失败（抄读超时/补抄超 3 次/失败表定位）→ `hf-collect-analysis`；
  - 协议帧格式/字段/状态机细节咨询 → `knowledge-lookup`；
  - 拉合闸/停复电/入网/升级/档案异常 → 各自专项工具；
  - 无日志文件、要求在线/实时分析 → 本技能是纯离线文件分析。
- `allow_implicit_invocation: false`：仅显式调用，不自动加载。

## 边界（本技能能做什么 / 不能做什么）

**能分析**（全部离线、只读文件）：
1. 11E4 主动上报 jitter 分布（相对冻结周期结束边界，min/P50/均值/P95/max、±3s 占比）；
2. 每表每冻结上报次数（重传率）；
3. 11E3 补抄请求→响应延迟（按回显序号精确配对）及空中(7E)交叉验证；
4. 两端时钟偏移（同一 11E4 在 CCO 日志 vs 侦听台捕获的时刻差）；
5. 页面缺失表三端对账（空中11E4 × CCO收/丢 × 补抄 × 06F230 → 漏报根因类别）；
6. mclt_ut 单元测试校准参数提取（jitter / 响应延迟 / 时钟偏移 / ssn 段）。

**不能/不做**：
- 不提供 `ensure/start/stop/send/flash/烧录/串口打开` 等在线能力；
- 不新建、不启动任何采集来源；输入必须是既有日志文件；
- 06F230 是 CCO→集中器应用层帧，**不在侦听台 7E 空中捕获里**，7E 里搜不到是正常的；
- 不修改源日志；默认输出到终端（可重定向到报告文件）。

## 时间基准（必守）

- **以侦听台捕获时刻为唯一时间基准**（纯硬件抓包）。
- CCO 日志括号时间为**打印时刻**（比空中晚 ~1-3s，洪峰 2~6s）——这是打印延迟，
  不是 RTC 偏差；CCO RTC 已由集中器每 5 分钟校时 ≈ 真实时间。
- 分析"边界竞态漏报"时：页面缺失表往往全部有空中 11E4 且 jitter 很小（≤ +0.3s 附近），
  是被 CCO 冻结匹配在周期边界判 stale 丢弃（现场固件口径：上报冻结 == 上一周期起点）。

## 六个命令

| 命令 | 说明 |
|---|---|
| `sniff <7E报文>` | 11E4 jitter 分布（每冻结周期）+ 每表上报次数（重传率） |
| `cco <CCO日志>` | 11E4/11E3请求/11E3响应/06F230 计数 + stale/rejected/resultfail 告警直方图 + 11E3 请求→响应延迟 |
| `page <页面xlsx>` | 页面实收（冻结周期 → 表集合） |
| `pair <CCO日志> <7E报文>` | 两端时钟偏移 + 补抄延迟交叉验证 |
| `miss <CCO日志> <7E报文> <页面xlsx> [--freeze HH:MM]` | 页面缺失表三端对账（漏报根因定位） |
| `utcalib <CCO日志> <7E报文> [--freeze HH:MM:SS]` | 一键输出 mclt_ut 校准参数 |

## 断言库入口（minute_assert，离线核帧）

- `from minute_assert import Expect698, assert_698_11e3_chain`：对 F231 明细逐帧断 698 内层——
  C=0xC3、APDU 85 02 01 `<di_cnt>` / 缺项 OAD 按长度补 FF、TimeTag=冻结时刻、APDU 长度含 9B 尾。
- `oad_lens` **按实际 F231 配置传入**，勿凭记忆写死。
- 真机样本见 `/home/H_STA/01/sta/reqs/0005-*/evidence/`。

## 执行步骤

1. **定位输入**：确认侦听台 7E 报文（`[ N][HH:MM:SS.mmm]7E ...`）与 CCO 串口日志
   （`[YYYYMMDD-HH:MM:SS:mmm]...`）路径；可选页面 xlsx。
2. **漏报排查优先跑 miss**（用户问"为什么缺/漏"时）：
   ```bash
   cd tools/taiti/分钟采集
   python3 run.py miss "<CCO日志>" "<7E报文>" "<页面xlsx>" --freeze "2026-09-20 10:15"
   ```
   对每个缺失表看证据链，按《分析规则.md》§4 判定根因类别：
   - `页面缺失 ∧ 空中11E4 ∧ CCO已收 ∧ stale丢` → **边界竞态漏报**（主因）；
   - `页面缺失 ∧ 空中11E4 ∧ 补抄请求有 ∧ 06F230无` → 补抄响应被拒/未上送；
   - `页面缺失 ∧ 空中无11E4` → STA 未上报（少见的背景缺口）。
3. **行为核查/校准参数**用 sniff / cco / pair / utcalib：
   ```bash
   python3 run.py sniff "<7E报文>"
   python3 run.py cco "<CCO日志>"
   python3 run.py utcalib "<CCO日志>" "<7E报文>" --freeze "2026-09-20 10:15"
   ```
4. **输出**：把关键数字与证据写进报告（含样本量）；无法可靠统计的项明确标注
   "数据不足"。协议字段细节如需出处，转 `knowledge-lookup` 查库。

## 判定口径（摘要，详见《分析规则.md》§4）

- jitter：STA 在冻结周期结束边界 ±几秒上报上一周期数据；10:15 周期实测
  P50≈+1.4s、85% 落在 ±3s、16% 提前到达——STA 行为正常。
- 边界竞态：现场固件无容差的冻结匹配把"边界前处理的早达上报"判 stale 丢弃，
  是漏报主因；修正后容差逻辑（±10s）对实测 jitter 全部接受。
- 补抄：响应延迟 P50≈1.1s、均值≈2.0s（CCO 视角）；500ms×3 连发 1.0s 耗尽后
  晚到响应被拒 → 补抄救不回（次因）。
- 时钟偏移：CCO 日志 − 侦听台捕获 ≈ +2.6s（打印延迟）；模型 cco_offset_s 取
  RTC 偏差 ≈ -2（CCO 慢），二者含义不同不可混用。

## 自带精简样例（自测/演示）

```bash
cd tools/taiti/分钟采集
python3 -m pytest test_mclt_analyze.py -q          # 10 项自测
python3 run.py miss samples/cco日志_分钟采集_精简.log \
  samples/侦听台报文_分钟采集_精简.txt \
  samples/页面_分钟采集_精简.xlsx --freeze "2026-09-20 10:15"
```

## 错误处理

- 路径不存在 / 格式不匹配 → 脚本安全报错（解析 0 条），非零退出，先核对文件路径。
- `page`/`miss` 需要 openpyxl：ZZT_SELF `.venv` 未装时用系统 `python3`。
- 冻结参数 `--freeze` 格式 `YYYY-MM-DD HH:MM[:SS]`；不传则全周期输出。
- 分析口径冲突时以最新日志实测为准并更新《分析规则.md》。
