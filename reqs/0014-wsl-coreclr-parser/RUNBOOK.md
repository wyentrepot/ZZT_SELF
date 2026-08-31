# REQS-0014 运行与构建说明

本说明是 REQS-0014 的交付运行手册。历史 WSL 手册位于归档目录，保持只读；
本需求不改变串口映射、烧录或 Windows EXE 打包流程。

## 目标选择

| 环境 | DLL | CLR | 用途 |
|---|---|---|---|
| Windows | libs/shared/dll/bin/Debug/GwHPLCAnalysis.dll | 既有 .NET Framework / Python.NET 默认宿主 | 开发、桌面打包、现有解析链 |
| WSL/Linux | libs/shared/dll/bin/Debug/net8.0/GwHPLCAnalysis.dll | .NET 8 CoreCLR | 原生 listener 解析 |

两个目标的程序集名、命名空间和 Python 接口相同：

- 程序集：GwHPLCAnalysis
- 外部类：NW.NwHPLCAnalysis
- Python：DotNetHplcParser.version()、parse_simple()、parse_full()

## WSL 一次性前提

1. 安装 .NET 8 SDK，并确保 dotnet --info 可运行。
2. 安装 Python 3 与 venv 支持。Ubuntu 若提示 ensurepip is not available，执行：

~~~bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
~~~

3. 在 WSL 的项目副本中安装 listener 依赖：

~~~bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r apps/listener/requirements.txt
~~~

若 .NET 是用户目录安装而非系统安装，必须在启动 Python 前设置：

~~~bash
export DOTNET_ROOT=/root/.dotnet
export PATH="$DOTNET_ROOT:$PATH"
~~~

不要把 /root/.dotnet 写入项目配置；它仅是本机的运行时安装位置。

## WSL 构建与 CoreCLR 自检

~~~bash
dotnet build libs/shared/dll/GwHPLCAnalysis.Net8.csproj -c Debug --nologo
export PYTHONPATH="$PWD/apps:$PWD/libs"
python -c 'from shared.dotnet_parser import DotNetHplcParser, default_dll_path; p=DotNetHplcParser(default_dll_path()); print(default_dll_path()); print(p.version())'
~~~

预期 DLL 路径以 bin/Debug/net8.0/GwHPLCAnalysis.dll 结束。Python 代码会在
导入 clr 之前明确选择 CoreCLR；运行时缺失时 listener 保持 503 降级，不会以
非 Windows 平台为由直接关闭解析器。

启动 listener 后，可验证实际 API：

~~~bash
export PYTHONPATH="$PWD:$PWD/apps:$PWD/libs"
python -c 'from fastapi.testclient import TestClient; from listener import app; c=TestClient(app.app); print(c.get("/api/version").json())'
~~~

返回中的 dll_available 应为 true。使用任意合法 HPLC 报文 POST 到
/api/parse 应返回 HTTP 200。

## Windows 构建与打包边界

Windows 仍以旧项目构建 net48；这也是打包时使用的唯一 DLL：

~~~powershell
& 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe' 'libs\shared\dll\DLL_NwHPLCAnalysis.csproj' /p:Configuration=Debug /p:BaseIntermediateOutputPath=obj\net48\ /v:minimal /nologo
~~~

随后 Windows 打包入口保持为 tools/packaging/build_exe.bat。不要把
bin/Debug/net8.0 复制到 Windows EXE；其仅为 WSL/Linux 的 CoreCLR 构建产物。

## 跨环境契约验证

在 Windows 工作树执行，下列命令会以本机 net48 解析，并自动调用 WSL 的 net8.0
解析，比较 6 类固定报文的 simple/full 解码 JSON：

~~~powershell
$env:HPLC_TEST_DATA_ROOT='D:\2-侦听台改造\测试文件'
$env:HPLC_WSL_DOTNET_ROOT='/root/.dotnet' # 按本机安装位置调整
python tools/scripts/compare_hplc_parser_runtimes.py --compare
~~~

结果必须为 result=equal、case_count=6。唯一允许忽略的值是 version.date
（构建生成时间）；所有业务 JSON 字段和 byte 数组均严格比对。

## 常见故障

| 现象 | 排查与处理 |
|---|---|
| no dotnet runtime | 安装 .NET 8，并使 dotnet 位于 PATH；用户目录安装需设置 DOTNET_ROOT。 |
| No module named pythonnet | 在 WSL 项目 venv 中安装 apps/listener/requirements.txt。 |
| 解析器为 None / API 503 | 检查相应平台 DLL 是否已构建，再执行 CoreCLR 自检命令；服务会保留日志与串口能力。 |
| ensurepip is not available | 安装与系统 Python 匹配的 python3.x-venv 包。 |
| golden diff 有 MPDU 差异 | 确认 net8 DLL 已由当前源码重新构建；JsonCompat 必须把 byte[] 输出为数值数组，不能退回 Base64。 |
