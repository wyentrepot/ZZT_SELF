# REQS-0016 TODO

> 变更记录只追加不覆盖。

## P0 需求建立
- [x] REQS.md 建立（根因：UI 未按 dir 区分上下行）

## P1 上行 Fn 只读化（simcon.js）
- [x] selectFn 判断 dir=上行 → 隐藏下发按钮 + 只读提示（替换表单/无数据单元卡片）
- [x] 下行 Fn 恢复下发按钮显示
- [x] 06H 上报历史入口接入（读 /store/events，选中即 loadEvents）

## P2 验证
- [x] node 语法检查
- [x] 提交推送远程

## 日志
- 2026-09-01 需求建立。
- 2026-09-01 P1/P2 完成：selectFn 上行只读分支（06H/00H 隐藏下发 + 从库读取）；06H 选中即 loadEvents 显示上报历史。
