# REQS.md — 需求基线（需求 ID：0002，标题：项目拆分 + 日志分类存储）

> 本文件遵循「需求只追加、不覆盖」ADR 模式（req-mgmt 决策 #1）。
> **顶部「当前生效基线」只存最新版本**；所有历史变更追加在下方「变更记录」，禁止静默覆盖。

---

## 当前生效基线（版本：v4，更新：2026-08-10）

### 目标
把当前 ZZT_SELF 仓库里「侦听台」与「模块日志/烧录」两个功能**解耦拆分成两个独立可运行的应用**，互不干扰；同时把日志文件**按功能、按模块类型分类存储**，前端可选日志归属（cco/sta）；更新 README 与启动脚本（用户选择启动模式）。

### 需求点

#### A. 日志分类存储（功能开发）
- **目录结构**：`LOG/` 下分两个子目录
  - `LOG/侦听台/`：侦听台串口采集日志
  - `LOG/模块/{cco,sta}/`：模块日志，按 cco / sta 分
- **模块日志命名**：时间 + `_[cco].log` / `_[sta].log`，如 `20260810-204700_[cco].log`
- **前端下拉框**：模块日志页加下拉框选择日志存储归属 cco / sta，**默认 cco**

#### B. 项目拆分（解耦）
- **同仓库两个独立应用**：
  - `listener_app/`：侦听台应用（端口 **8765**）
  - `module_serial_app/`：模块日志/烧录应用（端口 **8766**）
- 各自独立 FastAPI 应用、独立启动脚本、独立页面；**共享** venv / dll / parser_lib（不重复复制基础设施）
- 两个应用互不干扰，可同时运行

#### C. 启动脚本与 README
- 启动脚本改为**用户选择**：`1=侦听台`，`2=模块日志`，`3=全开`
- 更新 README 反映新结构

### 验收标准
- [ ] LOG 目录按 侦听台/模块{cco,sta} 分类，模块日志命名含 [cco]/[sta]
- [ ] 模块日志页前端下拉框可选 cco/sta（默认 cco）
- [ ] listener_app 与 module_serial_app 两独立应用，端口 8765/8766，互不干扰
- [ ] 启动脚本用户选择 1/2/3
- [ ] README 更新
- [ ] 既有测试不回归（侦听台功能完整）

### 所属分支
master

---

## 变更记录（只追加，禁止覆盖）

### 变更 1 ｜ 2026-08-10 ｜ 需求确认
- **改成什么**: 建立需求 0002 基线。用户确认：同仓库两独立应用（listener_app 8765 + module_serial_app 8766，共享基础设施）；LOG 分 侦听台/模块{cco,sta}，模块日志命名 时间_[cco].log；前端下拉框选 cco/sta 默认 cco；启动脚本选择 1/2/3；更新 README。
- **为什么**: 当前项目文件冗杂、模块日志与侦听台功能耦合；需解耦拆分并分类日志。
- **影响**: 全部（项目结构、日志服务、前端、启动脚本、README）
- **被取代**: 无（初始版本）

### 变更 4 ｜ 2026-08-10 ｜ 启动脚本修复：echo 里的 > 触发 cmd 重定向
- **改成什么**: 真机启动脚本（选择 1/2/3）报"文件名、目录名或卷标语法不正确"。定位根因：launcher 里 `echo [START] Listener -> http://127.0.0.1:8765/` 等行的 `>` 被 cmd 当作**重定向符**（`-> http:` 里的 `>` 重定向到无效文件 http:），导致报"文件名..."错误并中断启动。修复：把 `->` 转义为 `^-^>`；同时清理调试代码。验证选择 1/2/3 均正常（8765/8766 可同时 Listen）。
- **为什么**: cmd 的 echo 中 `>` 是重定向符，URL 里的 `->` 需转义。
- **影响**: hplc_launcher.bat（`->` 转义 `^-^>`、去调试）、部署 D:\zzt
- **被取代**: 变更 3 中 launcher 的 `->` 未转义版本

### 变更 3 ｜ 2026-08-10 ｜ 侦听台应用修复（复用 create_app）+ launcher CRLF
- **改成什么**: 真机启动验证发现：手工重写的 `listener_app.py` 有缺陷（`log_service.query_frames` 方法名不存在、漏了 task-config/delete-config 等大量侦听台路由），导致侦听台页面/接口报错（AttributeError/404/500），用户反馈"无法启动"。修复：`listener_app.py` **复用已验证的 `app.create_app` 工厂**（完整侦听台功能 + 解析），模块路由因 module_serial_service=None 返回 503；`module_serial_app.py` 保持独立（核心 /api/module-serial/* 全 200，侦听台路由 404 正确解耦）。另修复 `hplc_launcher.bat` 用 LF 换行导致 cmd 解析错乱（改 CRLF）+ 中文乱码（提示改英文）+ 流程顺序 bug（选择后未先设 APP_PYTHON）。
- **为什么**: 拆分时手工重写 listener_app 引入方法名/漏路由 bug，真机启动失败；launcher LF 换行导致 cmd 报错。
- **影响**: hplc_web/listener_app.py（复用 create_app）、hplc_launcher.bat（CRLF+英文+流程）、部署 D:\zzt
- **被取代**: 变更 2 中手工重写的 listener_app.py（方法名错误/漏路由）

### 变更 2 ｜ 2026-08-10 ｜ 日志分类 + 项目拆分实现
- **改成什么**: 实现日志分类存储与项目拆分。①日志分类：`LOG/侦听台/`（SerialCaptureService 落盘）+ `LOG/模块/{cco,sta}/`（ModuleSerialService 按 log_type 落盘，命名 时间_[cco].log）；模块日志页前端加「日志归属」下拉框选 cco/sta（默认 cco），start 接口加 log_type 参数。②项目拆分：新建两个独立 FastAPI 应用——`hplc_web/listener_app.py`（侦听台，端口 8765，入口 listener_run.py）+ `hplc_web/module_serial_app.py`（模块日志/烧录，端口 8766，入口 module_serial_run.py），共享服务模块/静态资源/DLL；验证两应用可同时加载、路由完全隔离（listener 的 /module-serial 返回 404，module 返回 200）。③启动脚本改为用户选择：1=侦听台，2=模块日志，3=全部；更新 README。
- **为什么**: 用户要求项目框架清晰、模块日志与侦听台解耦拆分为两个独立项目、日志按功能与 cco/sta 分类、前端下拉选归属、启动脚本选择 1/2/3。
- **影响**: hplc_web/listener_app.py、module_serial_app.py、listener_run.py、module_serial_run.py（新增）；module_serial_service.py、serial_service.py（日志分类目录）；app.py（log_type 参数）；static/module-serial.html/js（下拉框）；hplc_launcher.bat（选择模式）；tests（test_launcher 重写、module_serial_service 测试更新）；README
- **被取代**: 原单应用 run.py（8765 双标签）→ 拆分为两个独立 run 入口；原根 LOG/ 平铺 → 分类目录
