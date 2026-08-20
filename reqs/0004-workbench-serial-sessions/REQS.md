# AI 工作台串口状态保持、统一映射与模块日志动态页签设计

- 文档状态：待确认（已补充 AI 控制与证据接口设计）
- 日期：2026-08-20
- 影响范围：统一工作台、侦听台、模块日志、打包配置

## 1. 背景与问题

当前统一工作台只有一个 iframe。点击“验证工作台 / 模块日志 / 侦听台”时，前端通过修改同一 iframe 的 `src` 切页，原子页面会被销毁并重新加载。因此产生以下问题：

1. 侦听台已启动实时串口后，切到模块日志再切回，页面恢复为默认“日志文件分析”，没有根据后端串口状态恢复“实时串口”视图。
2. 后端串口实际上可能仍被占用，页面却显示未启动；用户再次点击启动时会收到“已运行”或“端口被占用”，表现为串口打不开。
3. 设备名与 Windows COM 号的映射目前只存在于 `apps/listener/serial_com_map.json`，模块日志没有复用，也没有模块角色、显示名称等信息。
4. 模块日志把 CCO 与 STA 固定为两个并排面板，页面空间拥挤；后端也固定为两个通道，无法支持按需增加独立串口页面。
5. 日志只按 CCO/STA 分类，缺少稳定的串口身份信息；同角色多串口或设备编号变化时不易辨认来源。

## 2. 目标

### 2.1 功能目标

1. 工作台顶层页面切换不销毁已经打开的子页面，串口连接和页面状态保持。
2. 侦听台页面无论首次打开、刷新还是从其他页面返回，都以后端状态为准：串口正在运行时自动显示实时串口模式，并恢复轮询。
3. 建立一个外部 JSON 串口映射文件，同时供侦听台和模块日志加载；以后串口变化只修改 JSON，不改代码。
4. 映射至少能描述 Linux/WSL 设备名、Windows COM 号、显示名称、默认用途和模块类型。
5. 模块日志实时日志区改为页签模型：默认只有一个独占页面；用户可新增、切换和关闭页面；每个页面独立选择并连接一个串口。
6. 模块日志后端由固定 CCO/STA 双通道升级为动态串口会话，不设置“两路”上限；实际可连接数由操作系统和硬件决定。
7. 每个会话独立保存串口参数、连接状态、日志缓冲、发送、烧录状态及增量游标。
8. 模块日志文件名和界面同时展示映射后的稳定身份，能够区分具体串口。

### 2.2 非目标

1. 本次不改变侦听台 7E 帧解析算法和日志索引结构。
2. 本次不改变 XMODEM 烧录协议实现。
3. 本次不实现跨进程抢占或自动关闭另一个工具已占用的串口。
4. 本次不允许同一物理串口被两个会话同时打开；冲突时给出明确提示。

## 3. 验收场景

1. 在侦听台选择实时串口并启动，切到模块日志，再切回侦听台：仍显示实时串口模式、运行状态和累计帧数，串口不中断。
2. 直接刷新侦听台页面：若后端串口仍运行，页面自动进入实时串口模式，不需要再次点击启动。
3. 修改统一 JSON 中某设备的 COM 号或显示名称，重启软件后，侦听台和模块日志下拉框均显示新映射。
4. 打开模块日志时只显示一个实时日志页签，并占满实时日志工作区，不再同时展示 CCO 和 STA。
5. 点击“新增页面”可创建第二、第三个独立页签；不同页签可以分别连接不同串口并独立收发、烧录、清屏。
6. 切换页签不会停止后台串口，也不会清空其他页签日志。
7. 关闭运行中的页签时必须二次确认；确认后先停止串口再删除会话。关闭最后一个页签后自动创建一个空白默认页。
8. 同一物理串口已被某个会话占用时，另一个会话启动失败，并指出占用它的页签/会话。
9. 日志文件中可从文件名和首条 EVENT 看出模块类型、映射名称、COM 号和实际设备名。
10. 原“对照解析”和“模拟集中器”页签继续可用；对照解析可选择某一个动态实时会话作为来源。

## 4. 总体设计

### 4.1 工作台子页面保活

顶层工作台从“一个 iframe 反复换地址”改为“每个一级页面一个固定 iframe”：

- 初始化时为验证工作台、模块日志、侦听台各创建一个 iframe。
- 切换一级页签时只改变对应容器的显示/隐藏状态，不修改 iframe `src`。
- 每个 iframe 只加载一次，页面内定时器、表单状态和连接状态不因一级导航切换而丢失。
- 主题切换向所有已经加载的 iframe 广播，而不是只通知当前 iframe。
- 为避免启动时同时产生不必要请求，当前页立即加载，其余页首次点击时再懒加载；加载后永久保留。

该改动只影响浏览器页面生命周期，不主动启动或停止任何串口。

### 4.2 侦听台状态恢复

侦听台初始化采用“后端状态优先”：

1. 页面加载后请求 `/api/listener/serial/status`。
2. 若状态为 `running` 或 `starting`：选中“实时串口”，执行实时模式布局，恢复状态轮询和帧列表刷新。
3. 若状态不是运行态：使用页面默认或用户上次选择的数据源。
4. 前端不得在 `unload`/一级页签切换时调用停止接口。
5. 启动按钮状态、端口、波特率均以后端返回值回填，避免页面显示与真实句柄不一致。

### 4.3 统一串口映射 JSON

新增外部配置 `config/serial_ports.json`。开发模式从项目根目录读取；打包模式从可执行文件同级的 `config/serial_ports.json` 读取，使现场维护无需重新打包。

建议结构：

```json
{
  "version": 1,
  "ports": [
    {
      "id": "usb0",
      "linux_device": "/dev/ttyUSB0",
      "windows_com": "COM4",
      "label": "",
      "usage": "",
      "module": "",
      "enabled": true
    }
  ]
}
```

字段定义：

- `id`：稳定且唯一的映射标识；日志命名优先使用该值。
- `linux_device`：WSL/Linux 下真实打开的设备名。
- `windows_com`：Windows 下真实打开的 COM 号，也是 WSL 展示标注。
- `label`：面向用户的名称，例如“CCO 主模块”“STA-1”。
- `usage`：建议用途，可选 `listener`、`module_log` 或空字符串；仅用于排序和提示，不强制限制。
- `module`：模块日志默认类型，可选 `cco`、`sta` 或空字符串。
- `enabled`：是否在界面中展示此映射。

加载规则：

1. 按当前操作系统选择实际打开名：Windows 使用 `windows_com`，Linux/WSL 使用 `linux_device`。
2. 枚举到但未配置的真实串口仍展示，避免配置遗漏导致设备不可用。
3. 配置存在但系统当前未枚举到的设备标记为“离线”，不能直接启动。
4. JSON 缺失或格式错误时不阻断软件启动：接口返回配置错误信息，并降级为系统枚举结果。
5. `id` 重复、设备别名冲突、非法 `module` 等问题在加载时报告，不静默选取。
6. 现有 `apps/listener/serial_com_map.json` 的设备/COM 数据迁移到新文件；旧文件停止作为运行时数据源。

共享加载器放在 `libs/shared`，侦听台和模块日志均调用同一份解析与匹配逻辑，避免两套映射漂移。

### 4.4 模块日志动态会话模型

将固定 `CHANNELS = ("cco", "sta")` 改为动态 `sessions`：

- `session_id`：服务端生成的稳定标识。
- `title`：页签名称，默认“实时日志 1、2、3…”，可由映射名称生成。
- `module`：`cco` 或 `sta`，用于日志分类和 loghooks 规则；映射配置提供默认值，用户可在页内调整。
- `port_identity`：映射 `id`、显示名称、实际设备名和 COM 别名。
- `serial_config`：波特率、数据位、校验位、停止位。
- `state`：`idle / starting / running / error`。
- 每会话独立拥有串口句柄、RX 线程、日志文件、内存环形缓冲、序号、烧录状态和发送能力。

服务启动时不预先打开串口。前端打开模块日志后，如果服务端没有会话，则创建一个空闲默认会话。页面刷新时从服务端重新取得会话列表，因此不会凭空丢失已经运行的连接。

### 4.5 动态会话 API

新增以下资源式接口：

- `GET /api/module-serial/sessions`：列出所有会话及状态。
- `POST /api/module-serial/sessions`：创建空闲会话。
- `PATCH /api/module-serial/sessions/{session_id}`：修改标题或模块类型；运行中禁止修改影响日志分类的字段。
- `DELETE /api/module-serial/sessions/{session_id}`：删除空闲会话；运行中返回冲突，由前端确认后先停止。
- `POST /api/module-serial/sessions/{session_id}/start`
- `POST /api/module-serial/sessions/{session_id}/stop`
- `POST /api/module-serial/sessions/{session_id}/write`
- `POST /api/module-serial/sessions/{session_id}/write-text`
- `POST /api/module-serial/sessions/{session_id}/baudrate`
- `POST /api/module-serial/sessions/{session_id}/flash`
- `GET /api/module-serial/sessions/{session_id}/logs?after=`
- `GET /api/module-serial/ports`：保留原 `ports` 字段，并增加带映射信息的 `port_details`，兼容已有调用。

旧的固定通道接口暂时保留一版兼容层，内部解析到同名旧会话；新前端只使用 sessions API。完成迁移后再单独评估移除，不在本次直接破坏外部脚本。

### 4.6 串口独占与冲突

模块日志服务维护进程内端口占用表，启动前将 Windows COM 别名和 Linux 设备别名归一到同一映射 `id`：

- 同一映射 `id` 已被其他动态会话运行时，拒绝重复启动。
- 无映射的设备按实际设备名判断重复。
- 操作系统返回占用错误时，转换为可读信息，包含实际设备名和可能的占用来源。
- 不自动停止侦听台或模拟集中器；跨子应用冲突由操作系统保护，并在界面明确提示“该串口可能正被侦听台/其他工具占用”。

### 4.7 实时日志页签交互

模块日志二级导航仍保留“实时日志 / 对照解析 / 模拟集中器”。“实时日志”内部改为：

- 顶部：会话页签列表、`+ 新增页面`、当前页关闭按钮。
- 主体：只渲染当前会话的完整控制区和日志区，独占可用宽度与高度。
- 页面切换：只切换当前 `session_id`，不会停止连接或清空 DOM/游标。
- 每个会话独立保存端口选择、串口参数、固件路径、自动滚动、刷新游标和日志 DOM。
- 页签状态点显示空闲、连接中、运行、错误；标题优先使用映射 `label`，否则使用模块类型与实际端口。
- 新建页签只创建会话，不自动打开串口。
- 关闭运行页签必须确认；关闭空闲页签直接删除。最后一个页签关闭后立即创建新的空白页。
- 发送框归属当前页签，不再通过 CCO/STA 下拉选择目标，降低误发风险。

### 4.8 对照解析适配

实时来源从固定 CCO/STA 切换为“动态会话”：

- 对照解析页面增加实时会话选择框。
- 选中会话后，根据该会话的 `module` 选择 CCO/STA 规则，并读取对应会话内存日志。
- 文件日志分析仍可手工选择 CCO/STA，不受动态会话影响。
- 日志源列表递归兼容新文件命名，并继续识别原有日志。

### 4.9 日志辨识

保留现有 `data/logs/模块/cco` 与 `data/logs/模块/sta` 分类目录，降低对现有扫描逻辑的影响；文件名增加串口身份：

```text
20260820-140053_[cco]_[usb0-COM4-devttyUSB0].log
```

首条 EVENT 同时记录：会话 ID、映射 ID、映射名称、模块类型、实际打开设备、COM/Linux 别名和串口参数。即使文件被单独复制，也能确定来源。

映射名称为空时仍使用实际设备名，不产生无法区分的日志。

## 5. 数据流

1. 应用启动时，共享映射加载器读取并校验 JSON。
2. 端口接口将系统枚举结果与映射合并后返回。
3. 用户在当前实时日志页选择串口；映射中的 `module` 和 `label`作为默认值回填。
4. 用户启动后，服务端为该 session 打开串口并注册占用关系。
5. RX 数据进入该 session 的独立缓冲，同时写入带串口身份的日志文件。
6. 前端按当前或后台会话的独立游标增量拉取；切换页签不影响后端采集。
7. 停止或删除 session 时关闭句柄并释放占用关系。

## 6. 错误处理

- 映射文件错误：端口接口返回 `mapping_error`，页面给出维护提示，同时仍展示系统串口。
- 串口离线：启动前拒绝并提示刷新端口。
- 串口占用：返回 409，包含占用会话或“可能被其他工具占用”。
- 会话不存在：返回 404，前端刷新会话列表并恢复到首个可用页。
- 运行中删除/修改模块类型：返回 409。
- 页面或网络短暂断开：串口采集继续；恢复后通过 session 状态与增量日志重新同步。
- JSON 使用 UTF-8；跨 Windows/WSL 写入后执行 UTF-8 有效性校验。

## 7. 兼容与迁移

1. 统一工作台、侦听台独立应用和模块日志独立应用都继续可启动。
2. 模块日志旧固定接口保留兼容期，新界面完全切到动态会话 API。
3. 原日志目录和旧日志文件可继续被对照解析扫描。
4. 打包脚本把默认 `config/serial_ports.json` 放到程序同级 `config` 目录；已存在的现场配置不得被升级包覆盖。
5. 首次迁移使用现有四条设备/COM 对应关系，不擅自填写未知的 CCO/STA 角色；角色由维护人员在 JSON 中确认。

## 8. 测试与验证方案

遵循 RED → GREEN → REFACTOR：

1. 工作台前端回归测试：一级页签切换后 iframe 节点和 `src` 不变。
2. 侦听台前端回归测试：后端状态为 running 时自动选择实时串口并启动轮询。
3. 映射加载器单元测试：正常、缺失、非法 JSON、重复 ID、Windows/WSL 别名匹配、未映射设备降级。
4. 动态会话服务测试：创建、列表、独立启动、三会话并存、独立日志、独占冲突、停止与删除。
5. 动态会话 API 测试：状态码、请求校验、兼容接口。
6. 模块日志前端测试：默认一页、新增/切换/关闭、每页独立端口与日志游标、运行页关闭确认。
7. 对照解析测试：动态会话作为实时来源，规则按 session.module 选择。
8. 运行模块日志、侦听台、统一工作台相关测试集，要求零失败。
9. 检查静态资源完整性、`git diff --check` 和打包配置。
10. 人工冒烟：真实连接一个侦听串口和至少两个模块串口，执行跨一级页面切换、动态页切换、收发和日志来源核对。

## 9. 实施顺序

1. 先添加本需求设计文档并评审范围。
2. 添加统一映射 JSON、共享加载器及测试。
3. 修复工作台 iframe 保活及侦听台状态恢复，并补回归测试。
4. 将模块日志后端重构为动态会话，补服务/API 测试并保留兼容层。
5. 将实时日志前端改为动态独占页签，适配发送、烧录、日志轮询。
6. 适配对照解析、日志来源和文件命名。
7. 更新打包配置、使用说明并执行完整验证。

## 10. 设计决策摘要

- 选择“后端动态会话 + 前端动态页签”，不采用只在前端复制 CCO/STA 面板的方案，因为后者仍受两路后端上限限制。
- 选择“多 iframe 保活”，不采用切页时保存/恢复整个子应用状态，因为串口页面包含定时器、日志 DOM 和异步任务，保活更可靠且改动边界更清晰。
- 选择一个外部统一 JSON 与共享加载器，不在侦听台和模块日志各维护一份映射。
- 日志保留 CCO/STA 目录并在文件名/首条事件增加串口身份，在可辨识性与旧扫描兼容之间取平衡。

## 11. 新增需求：AI 控制与证据检索接口

### 11.1 需求核实结论

该需求可行，并且与现有工作台的 Run、Artifact、Evidence、loghooks 和侦听台帧索引方向一致。现有能力可以复用，但不能只把当前 UI API 原样暴露给 AI，仍需增加统一控制层。

现有基础：

1. 侦听台已有串口状态、启动/停止、按时间筛帧、帧详情和 SQLite 索引。
2. 侦听台解析摘要已能用 `FrmType = 中央信标` 识别中央信标，完整帧详情可按 `frame_id` 再解析。
3. 模块日志已有串口状态、增量日志、日志文件落盘、烧录和 loghooks 规则扫描。
4. 工作台已有异步 Run、Artifact 清单、Evidence 下钻和串口资源租约模型。

当前缺口：

1. 没有面向 AI 的统一、稳定、版本化 API；AI 需要理解三个子应用的内部接口。
2. 没有“提交匹配规则后由服务端等待”的观察任务；AI 只能持续拉取全部日志。
3. 没有授权范围、有效期、设备范围和操作审计，无法安全开放烧录、启停串口等能力。
4. 没有统一返回日志物理位置、逻辑 Artifact、命中行范围、侦听台帧索引和 UI 深链。
5. 当前后端串口会话、UI 和 AI 控制面之间缺少统一的会话复用、命令串行化和观察者登记规则，可能发生重复启动或停止时机不一致。
6. 侦听台实时启动时会重建索引；如果没有记录观察任务的起始帧边界，旧数据和本次验证数据可能混淆。

因此新增一个 `/api/ai/v1` 控制面，并采用“异步操作/观察任务 + 有界等待”模型。AI 只提交目标和规则，服务端在本地消费串口与索引；AI 等待任务状态，命中后只读取结果片段或 Artifact，不实时吞取所有日志。

### 11.2 典型使用场景

场景：修改“每分钟第一个中央信标携带的标志”，增加指定打印并验证。

1. 人在工作台 UI 为 AI 创建限时授权，允许指定模块串口的烧录、模块日志启动、侦听台启动和证据读取。
2. AI 查询统一状态，确认目标设备、串口映射、现有会话及是否被人占用。
3. AI 调用烧录操作，等待烧录完成结果。
4. AI 创建或复用一个模块日志会话并启动串口，取得本次日志文件位置。
5. AI 创建模块日志观察任务，条件为指定打印或 loghooks 规则，范围从“任务创建时”开始，最长等待指定时间。
6. AI 创建侦听台观察任务，条件为 `FrmType = 中央信标`、选择“每分钟第一帧”，并可继续断言解析 JSON 中某字段等于期望标志。
7. 服务端在本地持续采集和匹配；AI 只调用等待接口。
8. 条件命中后，模块日志任务返回日志文件、命中行、上下文片段；侦听台任务返回结果集、帧 ID、解析 JSON 地址和 UI 深链。
9. AI 读取证据、形成结论；是否停止串口由任务创建时的生命周期策略决定。

## 12. 方案比较与选择

### 12.1 方案 A：AI 直接组合现有子应用 API

优点是实现最快。缺点是 AI 必须高频轮询日志和帧列表，接口耦合 UI 内部结构，缺少统一授权、证据固化、资源所有权和稳定引用，不满足“只按规则等待结果”的核心诉求。

### 12.2 方案 B：单个同步长阻塞接口

一个请求内部完成启动、等待和返回。调用简单，但烧录或观察可能持续数分钟，容易受到 HTTP 代理超时、客户端断线和服务重启影响，也不便于 UI 查看、取消或接管。

### 12.3 方案 C：异步操作/观察任务 + 有界等待（推荐）

创建任务立即返回 `operation_id`，服务端后台执行；AI 可查询状态，或用单次最长 30 秒的 wait 接口进行长轮询。任务命中、超时、取消或失败后返回固化结果。

选择方案 C，原因是：

- AI 无需实时读取所有日志。
- 客户端断线不影响本地串口采集和规则匹配。
- UI 与 AI 可以看到同一任务状态并协作。
- 易于审计、取消、恢复和生成 Artifact。
- 与当前工作台异步 Run 模型一致，但不强制把每个短观察任务伪装成完整验证 Run。

## 13. AI 控制面总体架构

新增 `Agent Control Service`，挂载在统一工作台 `/api/ai/v1`。它不重新实现串口、烧录、解析和 loghooks，而是编排现有服务：

```text
AI 客户端 / 工作台 UI
        |
        v
/api/ai/v1  授权、操作、观察任务、结果引用
        |
        +-- 统一资源注册表与租约
        +-- ModuleSerialService 动态会话 / XMODEM / loghooks
        +-- Listener SerialCaptureService / LogFileService / ParserService
        +-- OperationStore / ArtifactStore / AuditLog
```

设计边界：

1. 控制面只接受结构化、受限操作，不提供任意命令执行或任意文件读取接口。
2. 编译和源码修改仍由 AI 工作环境完成；控制面负责已授权硬件的烧录、采集和证据检索。
3. 烧录输入优先使用已登记的固件 Artifact ID；允许本地路径时必须位于授权目录内。
4. 观察任务只读取目标会话或目标索引，不重复打开同一串口。
5. 完整验证仍可关联现有 `run_id`；临时观察使用独立 `operation_id`，避免污染场景 Run 语义。

## 14. 授权模型

### 14.1 人工授权

AI 不能自行扩大权限。授权必须由人在工作台 UI 创建，生成短期 Bearer Token。授权记录至少包含：

- `grant_id`：授权标识。
- `scopes`：允许的动作。
- `resources`：允许访问或控制的后端串口会话、串口映射 ID 或设备组。
- `expires_at`：到期时间。
- `max_operation_seconds`：单次操作最长时长。
- `firmware_roots`：允许烧录文件所在目录或允许的 Artifact 类型。
- `created_by`、`reason`：授权人和用途。

建议 scopes：

- `status:read`：读取工作台、串口和任务状态。
- `evidence:read`：读取命中片段、解析 JSON 和 Artifact。
- `module_session:ensure`：确保指定后端模块日志会话已运行；已运行时直接复用，不重复打开串口。
- `module_session:stop`：请求停止指定后端会话；服务端根据活动观察任务和 UI 使用状态决定立即停止或返回冲突。
- `module_flash:execute`：在后端串口会话上执行烧录。
- `module_send:execute`：通过后端会话发送文本或十六进制数据。
- `listener:ensure`：确保侦听台后端采集已运行；已运行时直接复用。
- `listener:stop`：请求停止侦听台后端采集。
- `observation:create`：给现有后端会话或索引创建服务端观察任务。

### 14.2 Token 与审计

1. Token 只在创建时展示一次，服务端仅保存摘要。
2. 每次控制调用记录 `grant_id`、操作者、目标资源、参数摘要、结果、时间和关联 `operation_id`。
3. Token、固件内容和大段原始日志不得写入普通日志。
4. 授权可随时撤销；撤销后禁止新动作。已运行任务默认进入 `authorization_revoked` 并按创建时的安全停止策略处理。
5. UI 明确显示“AI 已授权”“AI 正在观察/烧录”的状态与剩余有效期。

## 15. 统一状态接口

### 15.1 请求

```http
GET /api/ai/v1/status
Authorization: Bearer <token>
```

### 15.2 返回内容

状态响应为轻量快照，不携带大段日志：

```json
{
  "server_time": "2026-08-20T15:30:00+08:00",
  "workbench": {"state": "ready", "version": "..."},
  "listener": {
    "state": "running",
    "backend_session_id": "listener-main",
    "port": {"id": "sniffer", "device": "/dev/ttyUSB0", "com": "COM4"},
    "frame_count": 12560,
    "index_id": "idx-20260820-153000-a1b2",
    "index_path": ".../runtime/indexes/idx-20260820-153000-a1b2.sqlite3",
    "log_path": ".../data/logs/侦听台/...txt",
    "consumers": {"ui_views": 1, "ai_observations": 1}
  },
  "module_sessions": [
    {
      "session_id": "ms-...",
      "title": "CCO 主模块",
      "state": "running",
      "module": "cco",
      "port": {"id": "cco-main", "device": "/dev/ttyUSB1", "com": "COM24"},
      "log_path": "...log",
      "flash": {"state": "idle"},
      "consumers": {"ui_views": 1, "ai_observations": 1}
    }
  ],
  "operations": [
    {"operation_id": "op-...", "kind": "observation", "actor": "ai:grant-...", "state": "waiting"}
  ],
  "serial_handles": [
    {"resource_id": "cco-main", "backend_session_id": "ms-...", "state": "open"}
  ]
}
```

路径字段仅在调用方具有 `evidence:read` 时返回；否则只返回逻辑引用。

## 16. 操作接口

控制面提供统一异步操作：

- `POST /api/ai/v1/module-sessions/ensure`：按串口映射 ID 创建或复用后端模块日志会话；幂等，不为 UI 和 AI 分别打开串口。
- `POST /api/ai/v1/module-sessions/{session_id}/stop`：请求后端停止会话；有活动观察任务时默认返回冲突，显式强制停止需要相应授权。
- `POST /api/ai/v1/module-sessions/{session_id}/send`
- `POST /api/ai/v1/flash-operations`：在指定后端会话上创建烧录操作。
- `POST /api/ai/v1/listener/ensure`：启动或复用侦听台后端采集。
- `POST /api/ai/v1/listener/stop`：请求停止侦听台后端采集；有活动观察任务时按生命周期规则处理。
- `GET /api/ai/v1/operations/{operation_id}`：查询操作状态。
- `GET /api/ai/v1/operations/{operation_id}/wait?timeout_seconds=30`：有界等待。
- `POST /api/ai/v1/operations/{operation_id}/cancel`：取消任务。

烧录创建示例：

```json
{
  "session_id": "ms-1234",
  "firmware": {"artifact_id": "fw-20260820-cco"},
  "slot": 0,
  "no_reboot_after": false,
  "client_request_id": "beacon-flag-test-flash-1"
}
```

所有写操作支持 `client_request_id` 幂等键。AI 因超时重试时，服务端返回原操作，不重复烧录、重复启动或重复发送。

## 17. 观察任务接口

### 17.1 创建与等待

- `POST /api/ai/v1/observations`：创建观察任务，返回 HTTP 202、`operation_id` 和初始来源位置。
- `GET /api/ai/v1/operations/{operation_id}`：立即查询。
- `GET /api/ai/v1/operations/{operation_id}/wait?timeout_seconds=30&after_version=N`：有界长轮询；状态无变化时返回 `waiting`，AI 可再次等待。
- `POST /api/ai/v1/operations/{operation_id}/cancel`：取消观察，但默认不停止共享采集会话。

任务状态：

```text
created -> waiting -> matched
                  -> timed_out
                  -> cancelled
                  -> error
```

`matched / timed_out / cancelled / error` 为终态。服务重启后，非终态任务标记 `interrupted`，保留已生成日志和索引位置，不伪造成功。

### 17.2 公共窗口定义

```json
{
  "window": {
    "mode": "live",
    "start": "now",
    "end": null,
    "timeout_seconds": 180
  },
  "context": {"before": 20, "after": 30},
  "completion": {"match_count": 1, "settle_milliseconds": 500},
  "lifecycle": {
    "ensure_source_running": true,
    "on_finish": "leave_running"
  }
}
```

窗口模式：

- `live`：从任务创建边界开始等待新数据。
- `time_range`：查询指定开始/结束时间的已有日志或索引。
- `cursor_range`：按模块日志 seq 或侦听台 frame_id 精确限定，供自动化复跑。

创建任务时必须固化 `start_seq` 或 `start_frame_id`，保证不会误命中历史数据。时间统一使用带时区 ISO 8601；设备日志的时分秒同时保留原值和归一化时间。

## 18. 模块日志观察规则

### 18.1 支持的匹配器

1. `literal`：指定打印的字面匹配，默认推荐。
2. `regex`：受限正则匹配；限制长度、复杂度和执行时间，防止表达式拖垮采集线程。
3. `loghook_rule`：按已有 loghooks `rule_id` 或事件类型匹配，适合稳定语义。
4. `all / any / sequence`：组合多个匹配器；`sequence` 支持步骤顺序和最大间隔。
5. `not_seen`：在完整时间窗口内未出现某打印，只能在窗口结束后得出结论。

示例：等待指定打印：

```json
{
  "source": "module_log",
  "target": {"session_id": "ms-1234"},
  "window": {"mode": "live", "start": "now", "timeout_seconds": 120},
  "match": {
    "kind": "literal",
    "value": "central beacon first-of-minute flag=1",
    "case_sensitive": true
  },
  "context": {"before": 20, "after": 30},
  "lifecycle": {"ensure_source_running": true, "on_finish": "leave_running"}
}
```

### 18.2 命中结果

```json
{
  "operation_id": "op-log-...",
  "state": "matched",
  "source": "module_log",
  "session_id": "ms-1234",
  "matched_at": "2026-08-20T15:31:12.456+08:00",
  "log": {
    "artifact_id": "op-log-...-raw",
    "path": ".../20260820-153000_[cco]_[cco-main-COM24].log",
    "download_url": "/api/ai/v1/artifacts/op-log-...-raw",
    "line_start": 1250,
    "line_end": 1300,
    "match_lines": [1270]
  },
  "snippet": [
    {"seq": 1269, "time": "...", "direction": "RX", "text": "..."},
    {"seq": 1270, "time": "...", "direction": "RX", "text": "central beacon ..."}
  ]
}
```

创建观察任务时即可返回当前 `log.path` 和 `log.artifact_id`，使 AI 能知道日志存储位置；终态再补充命中范围。上下文片段有数量和字节上限，完整日志通过 Artifact 按需读取。

## 19. 侦听台观察与索引结果

### 19.1 匹配层级

侦听台匹配器分两级：

1. 摘要级：直接查询 SQLite `frames.summary_json`，用于帧类型、NID、时间、TEI 等高频条件。
2. 详情级：先用摘要缩小候选帧，再调用 ParserService 获取完整解析 JSON，并按字段路径判断。

中央信标使用稳定摘要条件：兼容 `FrmType` 和中文键 `帧类型`，值为 `中央信标`。不把 UI 显示文字散落在 AI 调用中，由服务端维护标准语义别名 `frame_kind = central_beacon`。

### 19.2 帧选择器

支持：

- `first`：窗口内第一帧。
- `last`：窗口内最后一帧。
- `all`：窗口内全部匹配帧，受数量上限约束。
- `first_per_minute`：按归一化时间分组，返回每分钟第一帧。
- `nth`：返回第 N 个匹配帧。

“每分钟第一个中央信标”使用 `frame_kind = central_beacon` + `selector = first_per_minute`，避免 AI 自己下载所有帧再排序。

### 19.3 创建示例

```json
{
  "source": "listener",
  "target": {"capture": "current"},
  "window": {
    "mode": "live",
    "start": "now",
    "timeout_seconds": 180
  },
  "match": {
    "kind": "parsed_frame",
    "frame_kind": "central_beacon",
    "selector": "first_per_minute",
    "where": [
      {"path": "detail.信标管理信息.目标标志", "op": "eq", "value": 1}
    ]
  },
  "completion": {"match_count": 1},
  "lifecycle": {"ensure_source_running": true, "on_finish": "leave_running"}
}
```

字段路径只是契约示例，实施时以 ParserService 实际 JSON 树为准；API 提供 `GET /api/ai/v1/listener/schema` 返回可查询的摘要字段、标准 `frame_kind` 和详情字段示例，避免 AI 猜字段名。

### 19.4 数据库主键与稳定直达地址

当前实现已经使用 SQLite：`frames.id` 是 `INTEGER PRIMARY KEY AUTOINCREMENT`，现有 `/api/logs/frames/{frame_id}` 也是按该主键查询。因此 AI 命中结果必须返回真实数据库主键 `frame_id`，而不是只返回另外生成的结果编号。

但当前 `log_index.sqlite3` 在启动实时串口或重新建立文件索引时会执行 `DROP TABLE` 后重建，新的数据库内容可能再次出现相同 `frame_id`。所以单独的 `frame_id` 只在“当前这次索引”内唯一，不能作为长期稳定引用。

设计采用复合数据库键：

```text
frame_key = (index_id, frame_id)
```

- `index_id`：一次侦听采集或一次日志建索引的唯一 ID。
- `frame_id`：该 SQLite `frames` 表中的真实主键。
- API、Artifact 和 UI 深链都必须同时携带二者。

为保证旧结果仍可访问，侦听台索引改为版本化存储：每次新采集/新建索引创建 `runtime/indexes/{index_id}.sqlite3`，不再覆盖上一份数据库；索引目录记录 `index_id`、数据库路径、源日志路径、创建时间、解析器版本和 SHA-256。`current_index_id` 只是一条当前指针。

命中结果示例：

```json
{
  "operation_id": "op-frame-...",
  "state": "matched",
  "index": {
    "index_id": "idx-20260820-153000-a1b2",
    "path": ".../runtime/indexes/idx-20260820-153000-a1b2.sqlite3",
    "source_log_path": ".../侦听台/...txt",
    "start_frame_id": 20001,
    "end_frame_id": 20450
  },
  "matches": [
    {
      "frame_key": {
        "index_id": "idx-20260820-153000-a1b2",
        "frame_id": 20310
      },
      "sequence": "020310",
      "log_time": "15:32:00.102",
      "frame_kind": "central_beacon",
      "summary": {"FrmType": "中央信标"},
      "detail_url": "/api/ai/v1/listener/indexes/idx-20260820-153000-a1b2/frames/20310",
      "ui_url": "/static/pages/listener/index.html?index_id=idx-20260820-153000-a1b2&frame_id=20310"
    }
  ],
  "artifact_id": "op-frame-...-result-json"
}
```

接口：

- `GET /api/ai/v1/listener/indexes/{index_id}/frames/{frame_id}`：按复合数据库键返回原始 HEX、摘要和完整解析 JSON。
- `GET /api/ai/v1/listener/indexes/{index_id}/frames?start_time=&end_time=&frame_kind=`：在指定数据库索引内检索并返回数据库主键。
- `GET /api/ai/v1/artifacts/{artifact_id}`：下载查询结果 JSON 或源日志。
- `ui_url`：人在工作台打开后切换到指定 `index_id`，再按 `frame_id` 直达该帧详情。

`result_id` 可以作为一次多帧查询的分组编号，但不能替代数据库键。结果 Artifact 仍保存查询条件和关键解析 JSON，作为数据库被清理后的审计备份。

## 20. 后端串口会话与人/AI共享访问

### 20.1 串口独占的准确含义

串口独占发生在后端会话与物理串口之间，不发生在人和 AI 之间：

```text
物理串口 <--唯一 pyserial 句柄-- 后端串口会话
                                    |-- UI 读取状态/日志/索引
                                    |-- AI 读取状态/日志/索引
                                    |-- 服务端观察任务匹配规则
```

UI 和 AI 都是同一个后端服务的客户端，不直接打开串口。它们同时查看状态、日志文件、内存增量日志或 SQLite 索引，不会影响物理串口独占。

当 UI 或 AI 请求“启动”时，控制面执行 `ensure_running`：

- 目标后端会话已经运行：返回现有 `session_id`、日志路径或 `index_id`，不再次打开串口。
- 目标会话不存在：后端创建会话并打开一次物理串口。
- 同一物理串口已被另一个后端会话打开：后端拒绝重复打开。

因此不再给串口会话设置 human/AI owner，也不设计“AI 附着人的串口”或“接管人的串口”。会话属于后端服务；操作记录保留 `actor` 仅用于权限和审计。

### 20.2 控制命令与观察读取分离

- 读取状态、日志、索引、解析结果：天然可由 UI 与 AI 并发执行。
- 创建观察任务：只增加后端读取者，不改变串口句柄。
- 发送、烧录、修改波特率：通过同一后端会话的动作队列串行执行，防止两条命令交错；这属于命令协调，不是串口独占。
- 停止会话：服务端检查活动烧录和观察任务。默认有活动任务时不停止；显式强制停止必须有权限并使相关任务返回 `source_stopped`。
- UI 页面关闭或 AI 连接断开都不自动关闭串口。

### 20.3 统一后端会话注册表

统一工作台内的侦听台、模块日志、模拟集中器、验证 Run、UI 和 AI 控制面共享线程安全的后端串口会话注册表。串口映射 `id` 是物理资源主键，Windows COM 与 WSL 设备名归一到同一资源。

注册表记录的是“哪个后端 session 持有哪个物理串口句柄”，并记录 UI 视图数、AI 观察任务数和正在执行的动作。独立启动的其他进程无法共享此注册表，仍由操作系统串口独占保护。

观察任务生命周期：

- `leave_running`：任务结束后保持后端会话运行，默认值。
- `stop_if_started`：仅当该任务确实启动了会话、当前无其他观察任务且无 UI 要求保持运行时停止。
- `always_stop`：仅在无烧录/发送动作且显式强制授权时允许。

观察超时不会仅因为发起者是 AI 或 UI 而自动关闭后端串口。

## 21. Artifact、读取与数据量控制

1. 侦听台命中结果首先返回真实数据库复合键 `index_id + frame_id`；模块日志和固化证据使用逻辑 `artifact_id`，并在本机授权条件下提供物理 `path`。
2. Artifact 下载接口只允许访问登记过的文件，禁止通过路径参数任意读取文件系统。
3. 模块日志结果默认返回命中前后有限行；侦听台默认返回有限帧摘要。完整内容按 Artifact 或单帧详情按需读取。
4. 大日志支持按行范围、seq 范围或字节范围读取，不要求 AI 一次下载全文件。
5. 观察结果保存 manifest：来源、时间窗、游标、规则、命中位置、文件 SHA-256、解析器/规则版本和授权审计引用。
6. OperationStore 使用现有 `runs.sqlite` 的独立 operations/artifacts 表或同库独立表，不把临时观察强塞进完整 Run；操作可选关联 `run_id`。
7. 结果保留周期可配置；清理前不得删除仍被报告引用的 Artifact。

## 22. 错误与恢复语义

- `401/403`：Token 无效、过期或 scope/resource 不足。
- `404`：会话、操作、结果或 Artifact 不存在。
- `409 resource_conflict`：目标物理串口已被另一个后端会话打开。
- `409 session_busy`：后端会话正在烧录、发送或仍有活动观察任务，当前不能停止或修改关键参数。
- `409 source_not_running`：未授权自动启动且来源未运行。
- `422 invalid_matcher`：规则、字段路径、时间窗或正则非法。
- `422 parser_unavailable`：请求详情级判断但解析 DLL 不可用；摘要级查询仍可降级运行。
- `429 operation_limit`：授权的并发任务或速率上限已达到。
- `503 source_unavailable`：串口服务、索引或模块日志服务不可用。

恢复规则：

1. 客户端 wait 超时不等于观察任务超时；返回当前状态后可继续 wait。
2. 相同 `client_request_id` 重试返回同一操作。
3. 服务重启后保留终态结果；运行中任务标记 `interrupted`，返回已有日志、索引和错误原因。
4. UI 或 AI 暂时断开不关闭串口。
5. 解析失败的帧仍保留原始 HEX 和 parse_error，结论标记为证据不足，不能当作“未出现”。

## 23. AI 接口新增验收标准

1. 未授权调用只能读取公开健康信息，不能启动、停止、发送或烧录。
2. 人可以在 UI 选择端口、权限和有效期生成授权，并可立即撤销。
3. AI 能用一个统一状态接口获得侦听台、模块会话、日志路径、索引、任务和资源占用状态。
4. AI 能异步发起烧录，使用幂等键重试不会重复烧录，并能得到完整终态和烧录证据。
5. AI 调用 ensure 后能创建或复用后端模块日志会话，并立即取得同一份日志存储位置。
6. AI 提交指定打印规则后，无需下载实时全量日志；wait 接口最终返回命中行、上下文、日志 Artifact 和物理位置。
7. AI 能按明确开始/结束时间检索模块日志，并返回对应日志片段。
8. AI 能确保侦听台后端采集运行，按时间窗检索 `central_beacon`，并取得 `index_id + frame_id` 数据库复合键、摘要、完整解析 JSON 地址和 UI 深链。
9. `first_per_minute` 能稳定返回每分钟第一条中央信标，不把任务创建前的旧帧算入 live 窗口。
10. 人和 AI 同时读取同一后端会话的状态、日志和索引时，不产生第二个串口句柄，也不影响采集。
11. UI 能看到后端会话、日志、活动观察任务和动作状态；AI 与 UI 的发送/烧录命令由后端串行化。
12. 同一物理串口的 Windows COM 与 WSL 设备名被识别为同一资源，两个后端会话重复打开时返回明确冲突。
13. 新一次采集或建索引生成新的 `index_id`；旧的 `index_id + frame_id` 仍能访问对应数据库行，直到按保留策略清理。
14. 操作审计可回答“谁、何时、凭哪个授权、对哪个串口、执行了什么、结果如何”。
15. 针对“烧录后等待指定打印 + 每分钟第一中央信标字段断言”的端到端自动化测试通过。

## 24. 对原实施顺序的调整

原第 2 至第 6 步保持不变，但在动态会话和状态恢复完成后增加 AI 控制面：

1. 需求设计评审。
2. 统一串口映射与线程安全资源注册表。
3. 工作台页面保活与侦听台状态恢复。
4. 模块日志动态会话与独占实时日志页签。
5. 日志身份、对照解析和侦听台深链。
6. OperationStore、Artifact 结果固化和审计日志。
7. AI 授权 UI 与 `/api/ai/v1/status`。
8. 烧录、模块会话和侦听台控制接口。
9. 模块日志与侦听台观察任务、服务端匹配器和有界 wait。
10. 后端会话复用、命令串行化、授权撤销和 UI/AI 并发读取测试。
11. 打包、说明文档、完整回归与真实硬件端到端验证。

本新增设计不改变前述“动态会话 + 统一映射 + 多 iframe 保活”的方向，而是以这些能力作为 AI 安全控制和人机并行使用的基础。

## 25. 规划需求：AI 日志侦听技能雏形

### 25.1 目标与边界

在 `/api/ai/v1` 状态、控制、观察任务和侦听台索引接口完成并稳定后，提供一个面向 AI 的项目内技能雏形，使 AI 能按固定流程调用工作台接口完成：

1. 查询侦听台、模块日志、后端串口会话和观察任务状态。
2. 确保模块日志或侦听台后端会话运行，并复用已打开的后端串口句柄。
3. 提交指定打印、loghooks 规则、时间范围或帧语义的观察任务。
4. 使用有界 wait 等待服务端匹配，不持续读取全部实时日志。
5. 获取模块日志路径、命中行和上下文。
6. 获取侦听台 `index_id + frame_id` 数据库复合键，并按键读取解析 JSON。
7. 返回证据位置、数据库键、Artifact 和 UI 直达链接。

本需求当前只进入计划，不立即创建技能。技能雏形不得写入个人 Codex 技能目录，不得自动安装或加载，也不得在后端接口尚未完成时假装可以执行真实硬件操作。

### 25.2 计划名称与目录

计划技能名称：

```text
observe-workbench-logs
```

计划放在项目内：

```text
skills/observe-workbench-logs/
```

该目录只是可评审、可复制的技能源码。未来需要使用时由人明确安装，或由 AI 按给定路径显式加载。`agents/openai.yaml` 设置：

```yaml
policy:
  allow_implicit_invocation: false
```

因此技能只能通过 `$observe-workbench-logs` 显式调用，不因普通日志问题自动进入 AI 上下文。

### 25.3 技能雏形结构

计划结构：

```text
observe-workbench-logs/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   └── workbench_ai_client.py
└── references/
    └── api-contract.md
```

- `SKILL.md`：只保存核心决策流程、参数检查、授权要求、等待和结果解释。
- `agents/openai.yaml`：技能显示信息，并禁止隐式调用。
- `scripts/workbench_ai_client.py`：使用标准化参数调用 `/api/ai/v1`；支持 dry-run，禁止在参数中明文打印 Token。
- `references/api-contract.md`：保存完整 API 参数、状态枚举、响应示例、错误码和数据库键说明，避免 `SKILL.md` 过长。

不额外创建 README、安装指南或重复说明文件。

### 25.4 显式调用方法

计划使用方式：

```text
使用 $observe-workbench-logs 查询当前侦听台和模块日志状态。
```

```text
使用 $observe-workbench-logs，在 CCO 主模块日志中从现在开始等待打印
"central beacon first-of-minute flag=1"，最长等待 120 秒，返回命中前 20 行、后 30 行和日志位置。
```

```text
使用 $observe-workbench-logs，确保侦听台运行，从现在开始等待中央信标，
返回每分钟第一帧，并检查解析字段是否等于 1；返回 index_id、frame_id 和解析 JSON 地址。
```

```text
使用 $observe-workbench-logs，在 2026-08-20T15:00:00+08:00 到
2026-08-20T15:10:00+08:00 范围查询中央信标，不启动新的串口采集。
```

技能执行时必须先把自然语言转换为结构化参数，并在缺少会改变目标设备、时间范围或匹配结论的关键参数时停止调用并请求补充。

### 25.5 公共必传与默认参数

公共参数：

- `base_url`：工作台地址；默认只允许本机工作台地址，示例 `http://127.0.0.1:8790`。
- `token_env`：保存授权 Token 的环境变量名；默认 `WORKBENCH_AI_TOKEN`。不得把 Token 直接写入命令行、提示词、日志或结果。
- `client_request_id`：写操作和创建任务的幂等键；由技能生成并在重试时复用。
- `action`：`status`、`ensure`、`observe`、`wait`、`result`、`frame-detail` 或已授权的 `stop`。
- `dry_run`：只校验并输出脱敏请求，不访问工作台；技能雏形首次验证时默认使用。

公共规则：

1. 未指定写操作时默认只读。
2. 未指定生命周期时使用 `leave_running`。
3. 单次 wait 最长 30 秒；观察总超时由 `timeout_seconds` 控制。
4. AI 连接断开、wait 超时或技能退出均不得自动关闭后端串口。
5. 同一 `client_request_id` 重试不得重复烧录、发送或创建观察任务。
6. 后端返回 `session_busy`、`resource_conflict` 或授权不足时，不自动强制停止或扩大权限。

### 25.6 模块日志观察参数

调用模块日志观察任务前需要明确：

- `target.port_id` 或已有 `target.session_id`：目标串口映射或后端会话。
- `target.module`：`cco` 或 `sta`；映射已配置时可以使用映射默认值。
- `ensure_source_running`：是否允许后端在未运行时启动；默认 `false`，明确授权后才可设为 `true`。
- `window.mode`：`live`、`time_range` 或 `cursor_range`。
- `window.start/end`：`time_range` 必传，使用带时区 ISO 8601。
- `timeout_seconds`：`live` 模式必传，必须小于授权上限。
- `match.kind`：`literal`、`regex`、`loghook_rule`、`sequence` 或 `not_seen`。
- `match.value/rule_id/steps`：由匹配器类型决定。
- `context.before/after`：命中前后返回行数，使用服务端上限。
- `completion.match_count`：需要多少次命中才结束。
- `lifecycle.on_finish`：默认 `leave_running`。

优先使用 `literal` 或稳定 `loghook_rule`；只有确实需要模式匹配时才使用受限 `regex`。`not_seen` 必须等待完整窗口结束后才能得出结论。

### 25.7 侦听台观察参数

调用侦听台观察或索引检索前需要明确：

- `target.capture`：通常为 `current`，或明确的 `index_id`。
- `ensure_source_running`：是否允许启动后端侦听；查询历史 `index_id` 时必须为 `false`。
- `window.mode`：`live`、`time_range` 或 `cursor_range`。
- `window.start/end`：指定时间段查询时必传。
- `timeout_seconds`：实时等待时必传。
- `match.frame_kind`：例如 `central_beacon`，不要直接猜测 UI 中文字段。
- `match.selector`：`first`、`last`、`all`、`first_per_minute` 或 `nth`。
- `match.where`：可选的解析 JSON 字段断言，字段路径必须来自 `/api/ai/v1/listener/schema`。
- `completion.match_count`：满足多少帧后完成。

技能返回结果时必须优先报告：

```text
index_id + frame_id
```

随后再提供 `detail_url`、`ui_url`、摘要、完整解析 JSON 或结果 Artifact。不得只返回不稳定的单独 `frame_id`，也不得用 `result_id` 替代真实数据库键。

### 25.8 计划工作流

技能主体计划遵循以下固定流程：

1. 读取用户目标，判断是状态查询、模块日志观察、侦听台观察还是历史索引查询。
2. 校验授权环境变量存在，但不输出其值。
3. 调用统一状态接口，解析后端会话、串口映射、日志路径和当前 `index_id`。
4. 补齐映射可确定的默认参数；关键参数不明确时请求用户确认。
5. 如果允许启动，调用 `ensure` 幂等接口复用或创建后端会话。
6. 创建观察任务并记录 `operation_id`、起始 seq 或起始 frame_id。
7. 循环调用单次最长 30 秒的 wait；不读取全部实时日志。
8. 到达 `matched`、`timed_out`、`cancelled`、`interrupted` 或 `error` 后停止等待。
9. 模块日志返回日志路径、Artifact、命中行和上下文；侦听台返回 `index_id + frame_id` 并按需读取帧详情。
10. 明确区分“未命中”“解析失败”“来源停止”和“证据不足”，不得把后几种情况报告为“未出现”。
11. 默认保留后端会话运行，不因技能结束停止串口。

### 25.9 计划验收标准

1. 技能源码位于项目目录，未写入或修改个人 Codex 技能目录。
2. 技能不会隐式加载，只能通过 `$observe-workbench-logs` 显式调用。
3. `SKILL.md` 能指导另一个 AI 在不读取源码的情况下正确选择状态、模块日志和侦听台流程。
4. 参数参考明确区分必传、默认、互斥和条件必传参数。
5. 客户端脚本支持 dry-run，并能脱敏输出将要发送的请求。
6. 客户端不接受或输出明文 Token 参数，只从指定环境变量读取。
7. 模块日志示例能生成符合 API 契约的 literal/loghook/time_range 观察请求。
8. 侦听台示例能生成 `central_beacon + first_per_minute` 请求，并正确处理 `index_id + frame_id`。
9. wait 逻辑只读取任务状态和最终结果，不持续下载全部实时日志。
10. 后端接口未实现或版本不匹配时，技能明确报告不可用，不伪造观察结果。
11. 使用 skill-creator 的初始化和验证工具检查目录、frontmatter、名称与 `agents/openai.yaml`。
12. 在不连接真实硬件的情况下先完成参数、dry-run 和错误路径测试；真实硬件前向验证需要用户单独授权。

### 25.10 实施顺序调整

该技能安排在 AI 控制接口稳定之后、最终打包验证之前：

1. 先完成 `/api/ai/v1` 状态、授权、后端会话、观察任务、wait、数据库复合键和 Artifact 接口。
2. 冻结第一版 API 契约和错误码。
3. 创建项目内、未安装的 `observe-workbench-logs` 技能雏形。
4. 编写参数参考和 dry-run 客户端。
5. 执行技能结构验证、客户端单元测试和无硬件调用演练。
6. 经用户确认后再决定是否安装技能以及是否进行真实硬件验证。