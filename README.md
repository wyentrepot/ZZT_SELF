# 侦听台改造

> HPLC 抄表通信报文侦听、解析与自动验证工作台

项目由侦听分析、模块日志与烧录、日志事件、模拟集中器和自动验证编排组成；当前统一入口为 `apps/workbench`，各核心能力仍可独立测试与运行。

## 权威文档

| 文档 | 内容 |
|---|---|
| [总体方案](docs/01-总体方案.md) | 为什么做、最终效果、系统边界、总体架构 |
| [总需求](docs/02-总需求.md) | FR/NFR、范围与验收标准 |
| [骨架设计](docs/03-骨架设计.md) | 模块职责、输入输出、协议、目录与数据模型 |
| [任务安排](docs/04-任务安排.md) | 任务 1～5、状态、优先级、阻塞与验收出口 |

协议与 DLL 接口原文位于 [`docs/_references/protocols/`](docs/_references/protocols/)，旧方案和历史记录位于 [`docs/_archive/`](docs/_archive/)。

## 快速运行

- 统一工作台：`python -m workbench.run`（默认端口 8790）
- 侦听台：`python -m listener.run`（默认端口 8765）
- 模块日志：`python -m module_log.run`（默认端口 8766）
- Windows 一键入口：`启动工具.bat`

## 验证

```powershell
.venv\Scripts\python.exe -m pytest apps/listener apps/module_log apps/workbench libs/shared libs/parser_lib
```

运行要求与模块边界以四份权威文档为准，历史文档不再作为当前事实来源。
