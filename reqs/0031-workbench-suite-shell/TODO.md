# REQS-0031 · TODO

## 阶段 P0 · 方案与 Demo 入库
- [ ] 设计文档与 demo 提交至工作分支（v3 simcon 方案、v4 全套方案、两个可交互 demo、备选变体）

## 阶段 P1 · 壳骨架软件化（index.html / styles.css / app.js）
- [ ] 菜单栏行：品牌压缩 + 会话/数据/视图/帮助下拉（视图菜单：主题切换、折叠侧栏、折叠 Dock）
- [ ] 侧栏视觉升级：SVG 图标 + 布局对齐 v4 demo；折叠机制沿用 wb-sidebar-collapsed + localStorage
- [ ] 全局 Dock：底部面板（帧日志/日志流/事件页签、可折叠 Ctrl+J、拖高），监听子页 postMessage `wb-dock-push`
- [ ] 底部状态栏：platform 健康度（module_log/listener ✓✗，迁移现 wb-status 数据源）、主题名、Dock 态
- [ ] 机制红线自检：iframe 保活 / hash 路由 / 主题广播 / 窄屏抽屉行为不变；test_shell_navigation.py 7 项保持绿

## 阶段 P2 · 模拟集中器接入
- [ ] simcon.html：去页内面包屑顶栏，四列+三横条重组为四页签（构帧下发/并发抄表/数据观测/会话设置），全部元素 id 保留
- [ ] simcon.js：TX/RX 时 postMessage 推送全局 Dock（`wb-dock-push`）；其余逻辑零改动
- [ ] 浏览器实测：页签切换、下发、收发记录、结果面板、主题跟随

## 阶段 P3 · 回归与收尾
- [ ] workbench 相关 pytest（shell 导航 + serial-profile 前端 + app）全绿（存量失败单独登记）
- [ ] TODO/DONE/REQS-INDEX 状态更新，等待用户指令合并
