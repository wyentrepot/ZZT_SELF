# loghooks 源码打印扫描 —— 给工程侧 AI 的提示词

> 用途：让工程侧 AI 扫描 **CCO 固件源码**，把模块日志会打印的语句采集为结构化清单，作为
> 运行状态钩子规则（loghooks）的**数据来源**，供本侧规划与判定规则。
> 工程 AI **只做诚实采集，不改任何代码**；规则转换与判定由本侧完成。

---

## 0. 输出落点（必须遵守）

工程 AI 的扫描结果**必须写到指定目录**：

```
D:\zzt\loghooks\rules_source\
```

- 文件名建议：`cco_print_scan.json`（JSON 数组）或 `cco_print_scan.md`（Markdown 表格）。
- **一次扫描产出一个文件**，不要散落多处。
- 若目录不存在，先创建：`D:\zzt\loghooks\rules_source\`。

> ⚠️ 若你运行在被 E-SafeNet 透明加密的环境，请确认写出的文件是**明文**（无加密头/LOCK/NUL），
> 否则需按项目既定通道（git 明文）落盘。

---

## 1. 任务

扫描 CCO 固件源码中的**日志/打印入口**（printf、日志封装宏/函数等），凡是会产出
`序列号 | info | xxx.c (行号) | 消息` 这类模块日志文本行的语句都要采集。
**不要修改任何代码。**

---

## 2. 每一条打印语句需要记录的字段

| 字段 | 含义 | 必填 |
|------|------|------|
| `file` | 源文件，如 `aps_ioctrl_nwk.c` | ✅ |
| `line` | 打印语句所在行号 | ✅ |
| `func` | 所在函数名（如有） | |
| `raw_format` | 打印原文格式，如 `"onnet cnt = %d"` | ✅ |
| `msg` | 消息文本（去掉序号/文件前缀后的实际内容），如 `onnet cnt = %d` | ✅ |
| `params` | 携带的变量/参数（参数名 + 语义），如 `cnt → 入网节点数` | ✅ |
| `parsable` | 该行是否含可被正则捕获的可解析标识/数值：MAC / NID / TEI / 计数 / 状态码等 | ✅ |
| `direction` | RX / TX / EVENT（或未知） | ✅ |
| `trigger` | 触发场景描述（入网完成/周期心跳/异常等） | ✅ |
| `periodic` | 是否周期性/高频轮询（如 `trycnt` 那种高频重复行） | ✅ |
| `category` | 功能归类（见下） | ✅ |
| `scope` | `common`（所有分支通用）/ `province`（某省特有） | ✅ |
| `province` | 若 scope=province，注明省份（如 `anhui`） | 条件 |
| `anchor` | 该行可作跨来源关联锚点的业务标识（NID/MAC/TEI/冻结时刻等） | |
| `context_lines` | 若该打印只是某流程的一步，列出同流程的前后打印（用于规划 sequence 状态流） | |

---

## 3. 功能归类 category（沿用现有体系，可扩展）

| 值 | 含义 |
|----|------|
| `join` | 入网（新节点/STA 入网/onnet 计数/关联） |
| `collect` | 采集上报（抄表/分钟采集上报/数据读取） |
| `send` | 往网络层发送/上行/转发 |
| `beacon` | 信标/网络发现/NID |
| `state` | 状态机/心跳/周期性状态 |
| `error` | 错误/异常/失败 |
| `flash` | 烧录 |
| `other` | 其他（注明具体功能） |

---

## 4. 特殊关注

1. 标出**高频轮询噪音行**（`periodic=true`，如 `bpsCheck_state0 trycnt N`），
   这些通常不进规则、或只作过滤。
2. 对**省份特有功能**（如安徽分钟采集 E2/E3/E4 上报处理打印）单独标注 `province`，
   不要混进 `common`。
3. 若某打印属于一个**多步骤流程**（如 STA 入网：发现→关联→跟踪→收信标），
   在 `context_lines` 里把同流程相邻打印列全，供规划 `sequence` 状态流。
4. **不要臆造**：只记录源码里真实存在的打印；不确定的标注 `uncertain` 并说明。
---

## 5. 输出格式

**推荐 JSON 数组**（`loghooks/rules_source/cco_print_scan.json`）：

```json
[
  {
    "file": "aps_ioctrl_nwk.c",
    "line": 950,
    "func": "ioctrl_nwk",
    "raw_format": "onnet cnt = %d",
    "msg": "onnet cnt = %d",
    "params": [{"name": "cnt", "semantic": "入网节点数"}],
    "parsable": true,
    "direction": "RX",
    "trigger": "周期上报当前入网节点数",
    "periodic": true,
    "category": "join",
    "scope": "common",
    "province": null,
    "anchor": null,
    "context_lines": []
  }
]
```

**或 Markdown 表格**（`loghooks/rules_source/cco_print_scan.md`），表头同上。

---

## 6. 最终交付三部分

1. **打印清单**（上面字段的完整表，写入 `loghooks/rules_source/`）
2. **按功能归类的汇总**（各 category 有哪些、是否通用/省份）
3. **疑问/不确定项**（需要本侧确认的）

---

## 7. 本侧如何消费这份清单（供你理解，不需执行）

- 去噪：剔除 `periodic=true` 高频轮询行（或转 `exclude` 过滤）。
- 归类：`category/scope/province` 直接落到规则文件对应字段。
- 可解析性：`parsable=true` 的 `raw_format` → 转正则 `match` 规则，`params` → `capture`；
  `parsable=false` 的仅作事件标记或跳过。
- 状态流：`context_lines` 多步骤流程 → 组装 `sequence` 规则。
- 锚点：`anchor` → 跨来源关联 `correlate` 的锚点。
- 写入：common 进 `loghooks/rules/common.json`，省份进 `loghooks/rules/provinces/<省>.json`。
- 闭环：拿真实日志跑 `loghooks scan` 核对命中率，不匹配回退给工程 AI 补 `raw_format`/`uncertain`。

---

*本文档为给工程侧 AI 的提示词模板；输出落点为 `loghooks/rules_source/`。*
