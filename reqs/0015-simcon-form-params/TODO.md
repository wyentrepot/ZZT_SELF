# REQS-0015 TODO

> 变更记录只追加不覆盖。

## P0 库字段键名（已前置完成）
- [x] afn_fn.json 字段补 `key`（业务键名）→ scripts/migrate_afn_fn_v2.py FIELD_KEYS 34 项；校验全绿

## P1 前端表单化（simcon.js / simcon.html）
- [x] 重写 `selectFn`：无字段 Fn 隐藏业务参数栏；有字段 Fn 渲染表单（数字+单位/下拉/地址/时间/hex/列表）
- [x] 表单值按 `key` 组装 `params`（`currentSend` + `collectFormParams`）
- [x] 帧预览卡片保留并接入表单
- [x] 加表单样式（对齐 tokens-v2 主题，深/浅色）

## P2 验证
- [x] node 语法检查 simcon.js
- [x] 回归：REQS-0013 相关测试不破坏（29 passed）
- [x] 字段分类验证：01H/12H 无数据单元隐藏；03H-F6 duration/min、05H-F4 timeout/s、10H-F2 start/count 表单化

## 日志
- 2026-09-01 需求建立；字段 key 映射已注入（34 项 FIELD_KEYS）。
- 2026-09-01 P1/P2 完成：selectFn 表单化重写 + collectFormParams 组装 + 表单 CSS；回归 29 passed。
