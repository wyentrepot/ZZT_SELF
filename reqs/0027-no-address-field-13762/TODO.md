# REQS-0027 — TODO

## 阶段划分

### P0：行为确认与帧验证
- [x] 确认带地址域 10H-F2 下行帧被集中器否认（错误状态 0A）
- [x] 构造无地址域 10H-F2 帧（`build_local_13762_frame`，`module_id=0`）
- [x] 实机下发无地址域 10H-F2，验证集中器应答：正常返回（AFN=10 Fn=F2 响应，从节点总数量 120），不再 0A 否认

### P1：代码改造（下发统一不带地址域）
- [x] `scenario_codec.build_address` 默认返回空（不装配地址域）
- [x] `build_send` 走无地址域分支（广播等显式场景保留地址域）
- [x] `/api/simcon/build` 预览与下发一致（均无地址域）

### P2：测试与验收
- [x] 单测覆盖：build_send(anhui, 10H-F2) 无地址域
- [x] 帧解析（decode）回归不受影响（test_scenario_codec 16/16 通过）
- [x] 更新 REQS-INDEX.md（P1 完成状态）
- [x] **实机清空验证（2026-09-02）**：查询 120 档案 → 8 批删除 → 复查 total=0 ✅
  - 发现并修复：`build_address` 误把 `params.meters`（11H-F2 删除对象清单）当路由目标装配地址域；已移除，删除帧恢复无地址域
- [ ] 遗留：test_api_cli 2 项 + test_responder_matcher 1 项为基线既有失败（与本次改动无关，已确认 stash 前即失败）
