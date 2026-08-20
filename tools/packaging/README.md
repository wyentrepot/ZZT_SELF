# Windows / PyInstaller 打包资源约定

本目录负责四个 Windows 可执行包：侦听台网页版、侦听台桌面版、模块日志桌面版，以及统一 AI 工作台。`build_exe.bat` 的菜单 `4` 用于构建工作台。

## 可维护的串口映射

每个 `.spec` 都会把 `config/serial_ports.json` 作为发布模板收集进包中。冻结程序首次启动时，`runtime_hooks/ensure_serial_ports_config.py` 会把模板复制到：

```text
<exe 所在目录>/config/serial_ports.json
```

程序读取的是这个外置文件。因此现场人员可以修改 COM 口、波特率和业务身份，而无需重新打包。后续启动绝不覆盖已有文件，人工维护的映射可跨升级保留；若要使用新包内的默认值，先删除或改名外置文件再启动。JSON 的 `version` 必须保持为 `1`。

`build_exe.bat` 在构建成功后会为尚无该文件的 onedir 程序目录立即放置模板，故无需等第一次启动再编辑；若目录已有人工维护的 JSON，构建也不会覆盖它。若使用 `PyInstaller ... spec` 直接构建，运行时钩子仍会在首次启动时完成同样的初始化。

安装目录不可写时，应用不会因为复制失败而无法启动：串口映射目录会返回清晰的配置错误，普通串口枚举仍可继续。

## 资源覆盖范围

| 可执行包 | 收集的静态页面 | 串口映射 | 显式动态模块 |
| --- | --- | --- | --- |
| 侦听台 / 侦听台桌面版 | `apps/listener/static` | 外置 JSON 模板 | `listener.serial_service`、`listener.index_registry`、`shared.serial_mapping`、pyserial 枚举模块 |
| 模块日志 | `apps/module_log/static` | 外置 JSON 模板 | `shared.serial_mapping` |
| 统一工作台 | 工作台外壳和已复制的侦听台/模块日志页面 | 外置 JSON 模板 | AI 控制 API、授权/存储/操作模块、侦听索引目录、串口映射 |

静态目录按整个目录收集。因此动态模块会话页面、侦听台恢复页面及其 HTML/JS/CSS 文件都会跟随对应程序发布，无需逐文件维护清单。

## 构建与验证

构建脚本会同时安装侦听台和模块日志的依赖（包括 `pythonnet`），以满足工作台的统一挂载需求。构建前可先运行：

```bash
python -m pytest tools/packaging/test_packaging_resources.py -q
```

在 Windows 上通过 `tools\packaging\build_exe.bat` 打包后，检查每个产物目录都存在同级的 `config\serial_ports.json`。启动两次后确认该文件内容不变，即可证明外置配置不会被覆盖。