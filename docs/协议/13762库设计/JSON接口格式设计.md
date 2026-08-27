# 13762 协议库 JSON 接口格式设计（v1 定稿，用户已确认 2026-08-27）

> 基准：Q/GDW 10376.2—2019 单 68 标准帧（ADR-44 已定）
> 帧结构：`68H | L(2B) | C(1B) | 用户数据(L1) | CS(1B) | 16H`
> 用户数据：`R(信息域) | A(地址域) | AFN | 应用数据`

## 一、构帧接口：JSON → bytes

### 输入 JSON（build 请求）

```json
{
  "action": "build",           // 固定
  "direction": "down",         // "down"=集中器→模块(DIR=0) | "up"=模块→集中器(DIR=1)
  "control": {                 // 控制域 C（可选，缺省按 direction/通信方式推导）
    "prm": 1,                  // 启动标志位，缺省 1（下行启动站）/0（上行从动站）
    "comm_mode": 3             // 通信方式：1集中式 2分布式 3HPLC 10微功率 20以太网
  },
  "info": {                    // 信息域 R（可选，按上下行决定字节数）
    "relay_level": 0,          // 中继级别 0~15
    "conflict_detect": 0,      // 冲突检测 0/1（下行）
    "module_id": 0,            // 通信模块标识 0主节点/1从节点
    "sub_node": 0,             // 附属节点标识 0/1（下行）
    "route_flag": 0,           // 路由标识 0带路由/1旁路
    "channel": 0,              // 信道标识 0~15
    "ecc": 0,                  // 纠错编码标识 0未编码/1RS（下行）
    "expect_reply_len": 0,     // 预计应答字节数（下行）
    "rate_unit": 0,            // 速率单位 0bit/s/1kbit/s（下行）
    "rate": 0,                 // 通信速率（下行）
    "meter_channel": 0,        // 电能表通道特征（上行）
    "phase": 0,                // 实测相线标识（上行）
    "sig_quality": 0,          // 信号品质（上行）
    "event_flag": 0,           // 事件标志（上行）
    "seq": 0                   // 报文序列号 0~255
  },
  "address": {                 // 地址域 A（可选；module_id=0 时无地址域）
    "src": "010203040506",     // 源地址 A1 6B BCD
    "relay": [],               // 中继地址 A2 列表（每项 6B BCD）
    "dst": "999999999999"      // 目的地址 A3 6B BCD（广播用全9）
  },
  "afn": "03",                 // 应用功能码 2位hex
  "fn": "F1",                  // 信息类标识（可选，映射 DT1/DT2）
  "data": {                    // 应用数据（按 afn/fn 语义，可选）
    "nested_645": {...},       // 或内嵌 645 帧 JSON
    "nested_698": {...},       // 或内嵌 698 帧 JSON
    "raw": "AABBCC"            // 或原始 hex
  }
}
```

### 返回 JSON（build 响应）

```json
{
  "action": "build",
  "ok": true,
  "frame_hex": "68 0C 00 03 06 01 02 03 04 05 06 00 03 01 00 ... 16",
  "frame_bytes": [104, 12, 0, ...],
  "length": 28,
  "cs": 145,
  "warnings": []
}
```

## 二、解析接口：bytes/hex → JSON

### 输入（parse 请求）

```json
{
  "action": "parse",
  "frame": "68 0C 00 03 06 01 02 03 04 05 06 00 03 01 00 02 0A 16"
}
```
（frame 支持空格/连续 hex，或 base64/bytes 数组）

### 返回 JSON（parse 响应）

```json
{
  "action": "parse",
  "ok": true,
  "structure": "1376.2",
  "raw_hex": "68...16",
  "control": {
    "dir": 0, "prm": 1, "comm_mode": 3,
    "direction": "down", "comm_mode_name": "HPLC载波通信"
  },
  "info": { "relay_level":0, "module_id":0, ... },
  "address": { "src":"010203040506", "dst":"999999999999", "has_address":true },
  "afn": "03",
  "afn_name": "查询数据",
  "fn": "F1",
  "fn_name": "厂商代码和版本信息",
  "data": {
    "nested": [
      { "structure": "645", "fields": {...}, "items": [...], "nested": [] }
    ],
    "raw_hex": "..."
  },
  "fields": { "AFN": {"name":"AFN","value":"0x03 (查询数据)","hex":"03","raw":3,"unit":null}, ... },
  "cs": {"value":145, "ok":true},
  "warnings": []
}
```

## 三、设计原则

1. **纯函数、无 IO**：build: json→bytes；parse: bytes→json；不含串口/网络/文件。
2. **单 68 为标准**：C/R/A/AFN 全字段化；AFN/Fn 应用层语义映射保留现有字典。
3. **645/698 内嵌**：data.nested 递归复用 DLT645Adapter / DLT69845Adapter。
4. **上下行自适应**：direction 决定 R 的字节布局（下行 6B / 上行 6B）。
5. **字段可省**：control/info/address 缺省时按方向与协议默认值推导。
6. **字段带 unit**：fields/items 每项含 `unit`（单位），供前端/上层直接使用。
7. **兼容过渡**：旧双 68 实现存为 `__init__.py.dual68.bak` / `frame_codec.py.dual68.bak`
   备份供比对，不作为长期 API。
