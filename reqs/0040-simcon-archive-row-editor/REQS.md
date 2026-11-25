# REQS-0040 — 模拟集中器路由档案行编辑表（11H-F1/F2 增删 + 10H-F2 表格读取）

> 状态：✅ 已完成（代码 + 前端实测；后端需重启 8790 生效，见 §6）
> 创建：2026-11-19
> 来源：用户现场反馈（模拟集中器页 `#simcon` → `11H F1 添加从节点`）
> 关联：REQS-0028（recipe 库 add_archive/clear_archive）、REQS-0015（参数区表单化）、
> REQS-0030（档案读取/临时会话）、REQS-0013（响应表格）

## 1. 问题（用户原话口径）

页面在 `11H F1 添加从节点` 处的表达**根本用不起来**：

1. **加不了协议类型**：要求"这个添加档案还要加协议类型吧"；
2. **表达方式不对**：要求"应该要一行一行的新增档案，可以直接写地址，选协议类型，这样子再下发"；
3. **读取也不对**：要求"读取时也要是一个表格显示"。

根因（代码层核实，不是审美问题）：

- 11H-F1 数据单元 = `数量n(1B) + (地址 BCD 6B + 协议类型 1B) × n`，天然是**多行结构**；
- 协议字典 `afn_fn.json` 里 11H-F1 的 `req.fields` 只有「从节点的数量 n」带 `key="nodes"`，
  **地址与协议类型两个字段 `key: null`** —— 通用字段表单按 key 渲染，直接把它们丢掉，
  于是页面只剩一个"地址列表"输入框，地址与协议**根本无法表达**；
- 更糟的是当时把 `nodes` 当纯地址数组传后端，适配器用 `nd.get(...)` 取值，
  实测 `POST /api/simcon/build` 返回 `构帧失败：'str' object has no attribute 'get'`，
  即**这条路径本来就是坏的**（不是用户操作问题）。

## 2. 方案

把 11H-F1/F2 从"通用字段表单"里摘出来，做专用**行编辑表**：

| 能力 | 实现 |
| --- | --- |
| 一行一个档案 | 每行 = 地址（12 位 BCD 输入框）+ 协议类型（下拉：00H 透明 / 01H 645-1997 / 02H 645-2007 / 03H 698.45） |
| 增删行 | 「＋ 新增一行」「清空」「✕ 删本行」；参数由行表生成，不再依赖字段 key |
| 前置校验 | 12 位数字、去重；非法行在**构帧前**报出（不浪费一次串口往返） |
| 协议差异 | F1 带协议列；F2（删除从节点）只留地址列（其数据单元只有数量+地址） |
| 批量下发 | 单帧上限 16 条（与 `recipes.MAX_BATCH` 同口径），超出**自动拆帧逐帧下发**并逐帧报确认/否认 |
| 帧预览 | 预览与实际下发同口径：>16 条时**分行显示每帧**（`帧 1/2（16 条，共 23 条）` + hex），不再"预览 1 帧、实发 2 帧" |
| 读取 | 新增「读取当前档案（10H-F2）」：表格显示 地址 / 协议类型 / 信号品质 / 中继级别 / 相位 / 在网 |
| 读后动作 | 勾选「导入编辑表」（把模块现有档案带进行表，含协议类型）或「勾选删除」（走 11H-F2 一帧多地址） |

红线保持：前端**不手拼字节**，构帧走 `/api/simcon/build`、下发走 `/api/simcon/step`（ADR-5 语义化）。

## 3. 代码改动

**前端（apps/workbench/static/pages/simcon/）**

- `simcon.js`：新增「路由档案」段（`isArchiveFn` / `renderArchivePanel` / `arcRows` /
  `arcValidRows` / `arcInlineParams` / `arcBuildChunkedPreview` / `arcSendChunks` /
  `arcReadArchive` / `renderArcReadTable` / `arcImportSelected` / `arcDeleteSelected`）；
  `selectFn` 分流、`collectFormParams` 与 `buildPreview` 优先认档案表、`#btnSend` 走拆帧路径。
- `simcon.html`：`.arc-tbl*` 样式（行表滚动区、行内输入高度、删除按钮悬停色）；
  脚本缓存戳 `simcon-v8` → `simcon-v9`。

**后端（两处最小修复，前端能力的地基）**

- `libs/parser_lib/adapters/adapter_10376/__init__.py`（`(0x11,1)` 分支）：
  `nodes` 元素兼容 **dict 或纯地址字符串**（字符串等价 `{"addr": 该串}`）；
  非 dict/str 抛清晰 `ValueError`。修掉上面那个 `'str' object has no attribute 'get'`。
- `libs/sim_concentrator/deep_app.py`：`_node_from_record` 增加
  `protocol=(v>>12)&0x7`、`phase=(v>>8)&0xF`（与 10H-F2 解码器同口径）；
  `ArchiveSession.export_excel` 同步补「协议类型」「相位」两列。

## 4. 验收

- [x] 页面 11H-F1 出「一行一个」行编辑表，每行有地址 + 协议下拉（4 种协议齐全）
- [x] 11H-F2 出同款行表但**无协议列**（数据单元决定），并带「勾选删除」
- [x] 23 行 → 提示「将拆 2 帧下发」，预览显示 `帧 1/2（16 条）`+`帧 2/2（7 条）`，首帧 n=0x10、次帧 n=0x07
- [x] 非法地址（`0800`）在构帧前报「第 1 行地址须为 12 位数字」
- [x] 其余 11H Fn（F3/F4/F5/F6/F100/F101/F102）仍是原表单/无数据单元，无回归
- [x] 后端：`nodes` 传纯地址串可构帧，与 dict 写法字节一致（新增 2 条用例）
- [x] 后端：10H-F2 记录带 protocol/phase（新增用例 + Excel 列更新）

测试与实测证据：

| 项 | 结果 |
| --- | --- |
| `test_simcon_archive_frontend.py`（新增，9 条静态断言） | 9 passed |
| 相关前端套件（archive + mclt + shell-navigation + app + serial-profile） | 70 passed |
| `libs/sim_concentrator` 全量 | 272 passed / 4 skipped |
| `libs/parser_lib` 全量 | 605 passed；1 failed（`test_10f104_up_unsupported`，**存量失败**，已用 HEAD 版文件隔离复现：基线返回 `b'\x00\x00\x00'` 同样不过） |
| Playwright 真浏览器（无串口发送，只走 build 预览） | 面板结构 12/12、拆帧预览 5/5、11H 全 Fn 回归 5/5 |

## 5. 已知边界

- **协议类型显示为码值→人话映射在前端**：后端只给 `0/1/2/3`（与 10376 解码器同口径），
  前端 `ARC_PROTO_NAME` 负责中文名，避免两处维护同一张表。
- **读取不做分页遍历**：`archive/query` 沿用后端 `count=200` 单次口径（REQS-0030 既有行为），
  超过 200 条会截断——属既有实现边界，本次未扩大。
- **拆帧下发的中间帧失败不自动回滚**：第 2 帧被否认时第 1 帧已写入模块，页面以 banner
  列出「第 N 帧（M 条）否认 xx」由人判断重试；不做隐式回滚（删除操作有破坏性，不应自动化）。
- 通用字段表单对「数组型参数」的老限制仍在（其它 AFN 的 `nodes`/`meters` 仍是单行文本），
  本次只按用户指向改造 11H-F1/F2。

## 6. 生效说明（重要）

后端那两处是 Python 进程内代码，**当前 8790 进程未重启前仍是旧行为**（前端静态文件已即时生效）。
实测后端修复已在独立进程（真实 FastAPI app + TestClient）验证通过；重启 8790 后页面即可完整使用。
重启会断开当前 simcon 串口会话（`ecu-biz`/COM28），故未擅自重启，交由人安排时机。
