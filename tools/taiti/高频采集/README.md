# 高频采集失败分析（tools/taiti/高频采集）

台体（taiti）扩展系列之一：从三份 HPLC 日志（台体 / CCO / 侦听台）交叉定位
"某几块表抄读超时不回、补抄超 3 次"的具体失败表与根因。

## 目录结构（按日志来源分类）

```
tools/taiti/高频采集/
├── run.py                     # 统一入口（taish / cco / sniff / cross 四命令）
├── 台体/                      # 台体日志分析
│   ├── analyze_taish.py       #   采集帧/ReadMeter/补抄次数/建档案帧/最终判定
│   └── samples/台体日志_精简.log
├── CCO/                       # CCO 日志二次证据
│   ├── analyze_cco.py         #   ApsReadRecord / aps tx / 命中统计
│   └── samples/cco日志_精简.log
├── 侦听台/                    # 侦听台 HPLC 报文二次证据
│   ├── analyze_sniff.py       #   请求/应答帧命中
│   └── samples/侦听台报文_精简.txt
├── test_hf_collect_analyze.py # 单测（16 用例，用精简样例）
└── 分析规则.md                 # 分析口径/判定规则
```

## 用法

```bash
cd tools/taiti/高频采集
# 1. 台体分析（先定位失败表）
python run.py taish "path/to/台体日志.log"
# 2. 二次证据（交叉验证失败表）
python run.py cross "path/to/cco.log" "path/to/sniff.txt" \
  010000012201 020000012201 --start 16:42:00 --end 16:44:50
#   或分别查单侧
python run.py cco "path/to/cco.log" 010000012201 --start 16:42:00 --end 16:44:50
python run.py sniff "path/to/sniff.txt" 010000012201 --start 16:42:00 --end 16:44:50
```

## 依赖

- 仓库 Python（复用 `libs/parser_lib` 与 `libs/sim_concentrator.frame_codec`）
- 台体日志 GBK 编码、CCO 日志 UTF-8（带 BOM）、侦听台报文 UTF-8

## 时间基准（重要）

三份日志时钟不同步，**时间线一律以侦听台（纯硬件抓包）为基准**：
- 侦听台 HPLC 报文：最准（唯一时间基准）
- CCO 调试日志：基本准，`ApsReadRecord` 为上层处理时刻（比实际到达滞后 30~50s）
- 台体日志：**不可靠**（可能快/慢几分钟），只作为"动作序列"识别

## 自带精简样例

`{台体,CCO,侦听台}/samples/` 三份样例由原始 8MB/4MB/12MB 日志筛选而来
（保留与失败表、时间窗相关的行，编码保持），单测用它验证，可作回归基线。

## 单测

```bash
python -m pytest tools/taiti/高频采集/test_hf_collect_analyze.py
```
