# REQS-0027 — 1376.2 协议下发统一不带地址域

> 状态：🚧 进行中
> 创建：2026-09-02
> 关联：REQS-0013（1376.2 协议帧页面）、REQS-0015（simcon 参数区表单化）、REQS-0020（实机运行测试）

## 1. 背景与问题

实机测试（REQS-0020）中执行 10H-F2「查询从节点档案」时，构帧走 `anhui` profile
（`cco_addr=020103040506`），`build_address` 据此装配了**带地址域 A** 的下行帧：

```
68 1E 00 43 02 00 00 00 00 04 02 01 03 04 05 06 02 01 03 04 05 06 10 02 00 00 00 10 95 16
```

集中器回执为**否认帧（NAK）**：`AFN=00 Fn=F2`，错误状态字 `0A`（主节点不支持此命令）。

**根因**：CCO/集中器本地 1376.2 交互（`build_local_13762_frame`，`address=None`）不携带地址域，
信息域 `module_id=0`。带地址域（`module_id=1`）的查询帧不被支持。

## 2. 目标

1. **所有 1376.2 协议下行下发统一不带地址域**（`module_id=0`，`address=None`）。
2. 模拟集中器（simcon）构帧/下发默认不装配地址域，无论 profile 是否配置 `cco_addr`。
3. 帧预览（`/api/simcon/build`）与实际下发（`/api/simcon/step`）行为一致，均无地址域。

## 3. 设计

- `scenario_codec.build_address`：默认不装配地址域。仅当显式指定 `src`/`dst`（如广播
  `broadcast=true` 需 A3=全 F）时才装配。
- `build_send` 默认调用 `build_address` 返回空 → `module_id=0` 无地址域帧。
- `frame_codec.build_local_13762_frame` 已是 `address=None`，保持作为无地址域基准。
- 校验：所有下行帧解析后 `信息域R.module_id=0`、`地址域A=(无)`。

## 4. 验收

- [ ] `build_send(anhui, 10H-F2)` 输出帧无地址域，与 `build_local_13762_frame` 一致。
- [ ] `/api/simcon/build` 预览 10H-F2 为无地址域帧。
- [ ] 实机下发无地址域 10H-F2，集中器不再返回 `0A` 否认（或返回正常数据/确认）。
- [ ] 现有帧解析（decode）不受影响。

## 5. 变更记录
