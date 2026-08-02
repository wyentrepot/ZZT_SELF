# 前端新增「应用层展开」标签页实施计划

> 更新：2026-08-02

## 问题描述

后端链路（DLL → Python `DualMode43Adapter` 富化）已为抄表帧（0x0003）与分钟采集帧
（0x00E2/0x00E3/0x00E4）生成结构化 `simple.application`（fields/items/nested，含
内嵌 698.45/645 帧），并经真实大日志端到端验收（200/200 帧递归出内嵌 698.45）。
但前端详情面板只以"完整 JSON"文本兜底展示，没有针对 `application` 的字段表格与
嵌套帧树渲染。用户要求：**在原有解析详情页面新增标签页，切换显示、重新渲染**，
且**只渲染并发抄表帧（0x0003）与分钟采集相关帧（0x00E2/0x00E3/0x00E4）**。

## 现状（已核实）

| 项 | 位置 |
|---|---|
| 详情面板结构 | `hplc_web/static/index.html` 123-152 行（`#detail-content`） |
| 详情渲染 | `hplc_web/static/app.js` `renderDetail()` 237-270 行 |
| 视图标签页机制（可复用模式） | `app.js` `switchView()` 373-386 行 + index.html 25-30 行 |
| 样式 | `hplc_web/static/styles.css`（分钟采集 tab 样式在 547 行起） |
| UI 测试 | `hplc_web/tests/test_ui_layout.py` |

## 验收标准

1. `index.html` 的 `#detail-content` 内新增两个子标签：`基础解析`（默认）与
   `应用层展开`，用 `data-detail-tab` 属性区分。
2. `app.js` 实现子标签切换：切换时显隐对应面板并重新渲染（`renderApplicationDetail`）。
3. `renderApplicationDetail` 仅对 `APP_ID ∈ {"0003","00E2","00E3","00E4"}` 且存在
   `simple.application` 的帧渲染：
   - `application.fields` 渲染为字段表格（名称/值/十六进制/说明）；
   - `application.items` 渲染为数据项列表；
   - `application.nested` 渲染为嵌套帧树（structure/address/字段，递归折叠）。
4. 其他帧（或 `application_error`）在「应用层展开」面板显示提示文案，不渲染空表格。
5. 现有基础解析/JSON/原始帧展示不受影响。
6. `hplc_web/tests/test_ui_layout.py` 新增断言（标签页存在、属性正确）；
   `hplc_web` 全量测试通过。

## 执行任务

- [ ] T1 修改 `index.html`：`#detail-content` 内新增子标签栏与 `#detail-app` 面板容器。
- [ ] T2 修改 `app.js`：
      - `renderDetail` 中渲染两个子面板；
      - 新增 `renderApplicationDetail(simple)`（字段表格 + items + nested 树）；
      - 新增子标签点击切换逻辑。
- [ ] T3 修改 `styles.css`：新增 detail-tabs、application 表格、嵌套树样式。
- [ ] T4 扩展 `test_ui_layout.py`：断言子标签与容器存在、属性正确。
- [ ] T5 运行 `hplc_web` 全量测试，确认无回归。
- [ ] T6 更新进度表与计划文档。
