# TODO.md — 任务清单（需求：0002 project-split-and-log-classify）

> 大需求按阶段推进。完成一项勾 - [x]；全部完成移到 DONE.md。

## 阶段 0：初始化需求
- [x] 建立 reqs/0002 结构（REQS.md / TODO.md / DONE.md）+ REQS-INDEX 登记
- [x] 现状调查：日志存储、app.py 路由、前端页面、启动脚本、README 结构

## 阶段 1：日志分类存储（A）
- [x] LOG 目录：侦听台/ 与 模块/{cco,sta}/
- [x] SerialCaptureService 落盘到 LOG/侦听台/
- [x] ModuleSerialService 落盘到 LOG/模块/{cco|sta}/，命名 时间_[cco].log
- [x] 模块日志页前端下拉框选 cco/sta（默认 cco），传给后端
- [x] 相关测试更新（22 测试全绿）

## 阶段 2：项目拆分（B）
- [x] 建立 listener_app/ 与 module_serial_app 两独立应用
- [x] 拆分 FastAPI 路由：listener_app.py（侦听台）+ module_serial_app.py（模块日志）
- [x] 各自独立前端页面与静态资源
- [x] 端口 8765/8766，验证互不干扰（listener /module-serial=404，module=200）

## 阶段 3：启动脚本与 README（C）
- [x] 启动脚本：用户选择 1=侦听台，2=模块日志，3=全开
- [x] 更新 README（目录结构 + 拆分说明）

## 阶段 4：验证与收尾
- [x] 测试全绿无回归（152 测试仅 2 个既有失败）
- [x] 部署 D:\zzt
- [ ] req-mgmt 收尾（DONE.md 归档、REQS-INDEX 更新）
