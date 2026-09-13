# REQS-0031 · TODO

## 阶段 P0 · 方案与 Demo 入库
- [x] 设计文档与 demo 提交至工作分支（v3 simcon 方案、v4 全套方案、两个可交互 demo、备选变体）— commit 156a489

## 阶段 P1 · 壳骨架软件化（index.html / styles.css / app.js）
- [x] 菜单栏行：品牌压缩 + 会话/数据/视图/帮助下拉（视图菜单：主题切换、折叠侧栏、折叠 Dock）
- [x] 侧栏视觉升级：SVG 图标 + 收缩态悬浮提示；折叠机制沿用 wb-sidebar-collapsed + localStorage
- [x] 全局 Dock：帧日志/日志流/事件页签，可折叠 Ctrl+J、拖高记忆，postMessage `wb-dock-push` 接收
- [x] 底部状态栏：服务健康（module_log/listener，迁移 wb-status 数据源）、主题名、Dock 态
- [x] 机制红线自检：iframe 保活 / hash 路由 / 主题广播 / 窄屏抽屉不变；test_shell_navigation 7 项绿

## 阶段 P2 · 模拟集中器接入
- [x] simcon.html：去页内面包屑顶栏，四列+三横条重组为四页签（构帧下发/并发抄表/数据观测/会话设置），元素 id 全保留
- [x] simcon.js：新增 pushDockFrame 钩子（增量帧 postMessage 推送全局 Dock，首批历史帧不推），其余零改动
- [x] 浏览器实测（静态服务无后端口径）：四页签切换、会话设置字段齐全、主题双向跟随、Dock 推送协议端到端、侧栏收缩
- [ ] **带后端浏览器实测**（打开串口→下发→Dock 收帧/内置应答渲染/结果面板四页签真数据）——需启动后端，待用户环境执行

## 阶段 P3 · 回归与收尾
- [x] 相关 pytest：shell 导航 7 + serial-profile 前端 8 + app/ai-simcon 43 + serial-profile api ≈ 全绿（52 passed）
- [ ] 第二步（侦听台 29 项 + 模块日志 21 项平移、轻模块深度换肤）另行排期
- [x] TODO/DONE/REQS-INDEX 状态更新，等待用户指令合并
