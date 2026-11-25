# TODO.md — 需求 0039 交付/阶段状态

> 基线：`REQS.md` v1（2026-11-07）；本需求是工作台能力需求，服务于 REQS-0007 C 阶段。
> 一个阶段验收通过才勾 `- [x]`；全部完成追加到 `DONE.md`。

## P0：立项与基线
- [x] REQS.md v1 落盘；REQS-INDEX 登记
- [x] 现状证据：simcon 游标 bug、pending_frames、日志宏门禁、临时长跑
- [ ] 与 REQS-0007 C 阶段的 48h 运行窗口对齐（避免重启打断）

## P1：simcon 高频抄表修复与背压
- [ ] `batch._read_one()` 改单调帧序号游标（`seq > last_seq`），加回归用例：>1000 帧后仍能匹配
- [ ] `_rx_history` 截断策略与游标兼容；`_rx_queue` 上限/drain；`pending_frames` 阈值告警
- [ ] 节流/退避/自动 `simcon close/open`；目标 ≥2 轮/分钟、成功率 ≥95%（连续 1h）
- [ ] 台架验证：旧行为可复现“到 1000 帧后全超时”，修复后 1h 不崩

## P2：长跑任务 / 心跳 / 可见性
- [ ] 长跑任务模型与 `start/stop/status/list`；PID/进程组/状态机/幂等
- [ ] `GET /api/ai/v2/longruns/{id}/heartbeat` + CLI，≤2KB/≤2s
- [ ] 60s 心跳 + 1h 汇总 + 告警；心跳不依赖聊天/UI
- [ ] 「会话」页长跑卡片（状态/elapsed/guard/batch_ok/最后心跳/证据）
- [ ] 工作台重启后会话恢复/续跑

## P3：日志门禁与触发捕获（固件 + 工作台）
- [ ] 固件 `diag_caps`：日志域/ring buffer/guard 版本/固件 md5，启动 banner + 只读查询
- [ ] 诊断构建打开 `LOGGER_MAC_ENABLE`（media_access_task/csma_access/mac/mac_data_buff）与 `DBG_LEVEL_APP|DBG_LEVEL_MM`
- [ ] ring buffer：合法写历史、任务上下文、邻居哨兵（nwk_intf_fifo/subMap/tei_set_bitmap）
- [ ] guard/canary 命中 dump ring buffer + 最近日志 + manifest
- [ ] 工作台 preflight 日志门禁：缺域/ring buffer 未启用 → 拒绝启动
- [ ] 台架验证：注入 poison → guard 命中 → 证据能回答“谁/何时写坏 write”

## P4：48h 验收与交付
- [ ] 48h 无人值守跑通；每小时汇总/心跳齐全
- [ ] 负结果判据：`batch_ok/send_ok/pending_frames/日志增长`，区分“无复现”与“台架无效”
- [ ] 既有 v2 接口回归（`verify_api_inventory.py`）
- [ ] 交付 `summary.md/json` + 证据目录 + 复盘
