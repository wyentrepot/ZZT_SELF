# DONE.md — 完成日志（需求：0002 project-split-and-log-classify）

> 只追加，最新在上。记录做了什么、为什么、改了哪些文件（req-mgmt 决策 #1）。

## 2026-08-10 — 变更 4：启动脚本 echo 的 > 触发 cmd 重定向修复
- **做了什么**: 真机启动脚本（选择 1/2/3）报"文件名、目录名或卷标语法不正确"。根因：launcher 里 `echo [START] Listener -> http://...` 的 `>` 被 cmd 当重定向符（重定向到无效文件 http:）→ 报错并中断。修复：`->` 转义为 `^-^>`，清理调试代码。验证选择 1/2/3 均正常（8765+8766 同时 Listen）。
- **为什么**: cmd echo 中 `>` 是重定向符，URL 的 `->` 需转义。
- **涉及文件**: hplc_launcher.bat（`->`→`^-^>`）、reqs/0002/REQS.md（变更 4，基线 v4）、部署 D:\zzt
- **验证**: 选择 1→8765 Listen、2→8766 Listen、3→双端口 Listen 均正常；无"文件名"错误

## 2026-08-10 — 变更 3：侦听台应用修复 + launcher CRLF
- **做了什么**: 真机启动验证发现手工重写的 `listener_app.py` 有缺陷（`log_service.query_frames` 方法名不存在、漏 task-config/delete-config 等大量侦听台路由），导致侦听台接口报错、用户反馈"无法启动"。修复：listener_app 复用已验证的 `app.create_app`（完整侦听台功能），模块路由 503；module_serial_app 独立（核心 API 200，侦听台路由 404 正确解耦）。另修 hplc_launcher.bat：LF→CRLF（cmd 解析错乱）、中文提示改英文（避免 GBK 乱码）、流程顺序（选择后先设 APP_PYTHON）。
- **为什么**: 拆分时手工重写 listener_app 引入方法名/漏路由 bug；launcher LF 换行导致 cmd 报错。
- **涉及文件**: hplc_web/listener_app.py（复用 create_app）、hplc_launcher.bat（CRLF+英文+流程）、reqs/0002/REQS.md（变更 3，基线 v3）、部署 D:\zzt
- **验证**: TestClient 两应用正常：listener(/api/version|logs/status|serial/ports 200，模块 start 503)；module(/api/module-serial/* 200，serial/logs 404 解耦)；uvicorn 前台启动 8765/8766 正常；launcher CRLF 后 cmd 执行正常

## 2026-08-10 — 变更 2：日志分类 + 项目拆分实现
- **做了什么**: ①日志分类：SerialCaptureService 落盘 `LOG/侦听台/`，ModuleSerialService 按 log_type 落盘 `LOG/模块/{cco,sta}/`（命名 `时间_[cco].log`）；模块日志页前端加「日志归属」下拉框（cco/sta，默认 cco），start 接口加 log_type。②项目拆分：新建 `listener_app.py`（侦听台 8765，入口 listener_run.py）与 `module_serial_app.py`（模块日志 8766，入口 module_serial_run.py）两个独立 FastAPI 应用，共享服务/静态资源/DLL。③启动脚本改用户选择 1=侦听台 2=模块日志 3=全部；更新 README。
- **为什么**: 用户要求项目框架清晰、解耦拆分两独立项目、日志按功能与 cco/sta 分类、前端下拉选归属、启动脚本选择 1/2/3。
- **涉及文件**: hplc_web/listener_app.py、module_serial_app.py、listener_run.py、module_serial_run.py（新增）；module_serial_service.py、serial_service.py（日志分类）；app.py（log_type）；static/module-serial.html/js（下拉框）；hplc_launcher.bat（选择模式）；tests/test_launcher.py、test_module_serial_service.py；README.md
- **验证**: test_launcher(3)+test_module_serial_service(22) 全绿；全量 152 测试仅 2 个既有失败；两应用可同时加载路由隔离（listener /module-serial=404，module=200）；部署 D:\zzt 一致
