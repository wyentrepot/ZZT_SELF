# UI E2E（Playwright 真实浏览器操作）

对"自动测试"页面做真点击验证：外壳导航 → 页面渲染 → 搜索/标签过滤 → 行内单跑（实板）→
抽屉结果/文档 → 链路一键跑 → 勾选批量跑 → tag 组跑。截图落 /tmp/req-ui-evidence/。

前置：工作台 8790 在线（WORKBENCH_LOCAL_FULL_ACCESS=1），simcon 已开串口
（POST /api/simcon/open {"port":"/run/hplc/serial/simcon"}），系统 python3 已装 playwright。

```bash
python3 tools/ui_e2e/ui_test.py        # 9 步主链路
python3 tools/ui_e2e/ui_group_test.py  # tag 组跑（--group 对齐）
```
