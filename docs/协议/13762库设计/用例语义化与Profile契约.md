# 13762 用例语义化 + Profile 契约（v1 定稿，用户已确认 2026-08-28）

> 配套 ADR：模拟集中器用例语义化（见 DECISIONS.md）。
> 目标：用例只保留"必要信息"（afn/fn + 最小业务参数），全局信息（cco 地址、
> sta 档案、通信方式）放 profile 共享，执行时经 scenario_codec 交给 13762 库
> 生成完整帧下发。`raw` 整帧 hex 直发**彻底移除**，未覆盖 Fn 明确报错。

## 一、Profile（全局信息基底）

路径：`apps/workbench/scenarios/profiles/<id>.json`。字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| `id` | str | profile 标识，task 用 `profile` 字段引用 |
| `name` | str | 中文说明 |
| `cco_addr` | str | CCO 地址（BCD 数字串，12 位） |
| `comm_mode` | int | 通信方式：3=HPLC（默认） |
| `seq_auto` | bool | 是否由执行器自动分配递增报文序列号（默认 true） |
| `task_range` | {min,max} | 任务号合法范围（安徽 1~15） |
| `sta_archives` | [obj] | 全局 STA 档案库：`{id, addr, protocol, phase}` |

task 引用 profile 后可用 `profile_overrides` 覆盖基底字段（如个别用例换 cco_addr）。

## 二、用例 JSON 结构

```jsonc
{
  "id": "anhui_minute_collect",
  "profile": "anhui",              // 引用 profiles/anhui.json
  "profile_overrides": { ... },    // 可选，覆盖基底
  "module": "cco",
  "port": "COM24", "baudrate": 9600,
  "steps": [
    {
      "name": "查询路由状态 (10H-F4)",
      "send": { "afn": "10", "fn": "F4" },                 // 无数据单元，参数可省
      "expect": { "afn": "10", "fn": "F4", "dir": "up" }
    },
    {
      "name": "配置采集任务 (11H-F231)",
      "send": { "afn": "11", "fn": "F231",
        "params": { "task_no": 7, "action": "enable", "protocol": 645,
                    "cycle_min": 5,
                    "items": [ { "meter_type": 0, "item": "04000201", "reply_len": 4 } ] } },
      "expect": { "afn": "11", "fn": "F231", "dir": "up" }
    },
    {
      "name": "关联档案 (11H-F232)",
      "send": { "afn": "11", "fn": "F232",
        "params": { "task_no": 7, "meters": ["010133000000", "010133000001"] } }
    }
  ]
}
```

### send 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `afn` | str/int | 应用功能码，如 `"10"` / `0x10` / `16` |
| `fn` | str/int | 信息类标识，如 `"F4"` / `"F230"` / `4` / `230` |
| `direction` | str | 缺省 `"down"`；`"up"` 用于构造上行帧 |
| `params` | dict | 该 AFN/Fn 的数据单元语义字段（见三） |
| `comm_mode` | int | 可选，覆盖 profile 默认 |

### expect 字段（沿用现状字段化匹配）

`{afn, fn, dir, nested, fields, nested_fields, any}`，`expect` 不做改动。

## 三、AFN/Fn 数据单元参数（构建侧模板，聚焦现有用例）

| AFN | Fn | 方向 | params 字段 | appdata 字节 |
|-----|----|------|-------------|--------------|
| 00H | F1 | 下/上 | `status`(可选 0/1) | 空或 1B 确认/否认 |
| 03H | F10 | 下 | （无） | 空（查询） |
| 10H | F4 | 下 | （无） | 空（查询） |
| 10H | F2 | 下 | `start`(2B BIN), `count`(1B BIN) | start+count |
| 10H | F230 | 下 | （无） | 空（查询） |
| 10H | F231 | 下 | `task_no`(1B), `protocol`(1B) | 任务号+协议类型 |
| 11H | F231 | 下 | `task_no`(1B), `action`(enable/delete), `protocol`(1B), `cycle_min`(1B), `items`(扁平列表) | 见"分组编码" |
| 11H | F232 | 下 | `task_no`(1B), `meters`(BCD 6B×N, 2B 数量) | 任务号+数量+地址 |
| 06H | F230 | 上（上报） | （responder 回确认；上行参数不在此表） | — |
| 06H | F3 | 上 | （responder 回确认；上行参数不在此表） | — |

### 11H-F231 分组编码（用户定稿：用例层扁平 items，codec 内部分组）

```python
def encode_11F231(params):
    # 1. 按 meter_type 分组排序（0单相 → 1三相 → 2其他）
    # 2. 对每组：固定值(1B) → 数据项数量(1B) → 预计回复总长度(2B, BIN)
    #    → 逐项写 数据项标识(4B) + 回复长度(1B)
    # 3. 拼接所有组
    # 校验：组内数量 n 与 items 实际数一致；组总长 = Σ(4+reply_len)
```

`items` 元素：`{meter_type: 0|1|2, item: "04000201"(4B hex), reply_len: int(1B)}`。

### 其他表（meter_type=2）

协议中"其他表"组结构同单/三相：固定值 0x02 → 数量 → 预计总长 → 数据项+回复长度。分组编码统一处理。

## 四、地址域语义（module_id=1，带地址域）

单 68 帧地址域 A = `A1(6B) + [A2(6B)×中继] + A3(6B)`，由 `info.module_id=1` 触发。

scenario_codec 按方向装配（用户定稿）：

| 场景 | A1(src) | A3(dst) |
|------|---------|---------|
| 下行（集中器→CCO） | `cco_addr` | `sta_addr`（查档案/配置目标） |
| 上行（CCO→集中器） | `sta_addr` | `cco_addr` |
| 广播 | `cco_addr` | `999999999999H` |

- 目标 sta 地址来源优先级：`send.params` 显式地址 > `profile.sta_archives` 查表 > 广播。
- 旧 `rtsa` 同址填 src/dst 的兼容路径在迁移时删除。
- seq 由执行器自动分配递增，注入 `info.seq`（`seq_auto=true` 时）。

## 五、错误处理

- 未覆盖的 AFN/Fn 模板：构帧时报错 `UnsupportedFn: 未覆盖 Fn 0x..-F..（模板未建立）`，不静默产出错帧。
- 参数缺失/越界（如任务号超出 task_range、items 组数量不一致）：构帧时报错并指出字段。

## 六、兼容与迁移

- 旧 task 的 `send.raw`（整帧 hex）与 `format:"local" + buff` 手写 hex 迁移为语义 `params`。
- `format` 字段不再区分 local/standard（单 68 统一，ADR-3）；`build_send_frame` 仅走 scenario_codec。
