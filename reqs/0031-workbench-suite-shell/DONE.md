# REQS-0031 · DONE（最新在上）

## 2026-09-13 ｜ P0-P2 完成（commit 156a489 + 4acb9cb）

- **P0**：v3/v4 设计文档与 demo 入库（ui-demo/ 四件套 + docs/ui 备选变体），REQS-INDEX 登记。
- **P1 壳骨架**：菜单栏（会话/数据/视图/帮助）、侧栏图标化升级（196↔50px、悬浮提示）、全局 Dock（三页签 + wb-dock-push postMessage 协议 + Ctrl+J 折叠 + 拖高记忆）、底部状态栏（服务健康/主题/Dock 态）。机制红线全部保留：iframe 保活、hash 路由、主题广播、窄屏抽屉；test_shell_navigation 7 项契约全绿。
- **P2 simcon**：四列+三横条 → 四页签（构帧下发/并发抄表/数据观测/会话设置）；连接控件迁至「会话设置」，stChip 迁至页签条；元素 id 全保留使 simcon.js 仅新增 pushDockFrame 推送钩子（首批历史帧不重复推送）。
- **验证**：52 项相关 pytest 全绿；静态服务浏览器实测（无后端口径）：9 模块导航、菜单、双主题跟随（含 iframe 广播）、四页签切换、Dock 推送端到端（iframe→parent，2 行入列、TX/RX 计数正确）、侧栏收缩 50px。
- **遗留**：带后端的完整链路实测（开串口→下发→Dock 收帧）待用户环境；第二步（侦听台/模块日志平移、轻模块换肤）另行排期。
