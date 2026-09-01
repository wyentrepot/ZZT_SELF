@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0..\.."

title HPLC 解析服务（Windows） - port 8700

echo.
echo  HPLC 解析服务（Parse Service）
echo  =============================
echo.

REM 必须在明文区（.build_plain）运行以规避 E-SafeNet 加密；请勿在主工作区直跑源码。
if not exist "libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll" goto :missing_dll

:check_python
where python >nul 2>&1
if errorlevel 1 goto :missing_python

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [首次运行] 正在创建 Python 环境...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
if exist ".venv\.deps_parser_service" goto :launch
"%APP_PYTHON%" -c "import fastapi, httpx, uvicorn, pythonnet" >nul 2>&1
if not errorlevel 1 (
  echo. > ".venv\.deps_parser_service"
  goto :launch
)
echo [首次运行] 正在安装解析服务依赖...
"%APP_PYTHON%" -m pip install -r "apps\parser_service\requirements.txt"
if errorlevel 1 goto :failed
echo. > ".venv\.deps_parser_service"

:launch
echo.
echo [启动] 解析服务 ^-^> http://127.0.0.1:8700/health
echo 关闭此窗口即停止服务。本服务不碰串口；串口归 WSL 侦听台。
echo.
set "PYTHONPATH=%CD%\apps;%CD%\libs;%PYTHONPATH%"
"%APP_PYTHON%" -m parser_service.run
if errorlevel 1 goto :failed
exit /b 0

:missing_dll
echo [错误] 未找到解析库：libs\shared\dll\bin\Debug\GwHPLCAnalysis.dll
echo 请先在 .build_plain 明文区构建 C# 工程：libs\shared\dll\DLL_NwHPLCAnalysis.csproj
pause
exit /b 1

:missing_python
echo [错误] 未在 PATH 中找到 Python。
echo 请安装 Python 3 并勾选 "Add Python to PATH"。
pause
exit /b 1

:failed
echo.
echo [错误] 启动失败，请保持窗口查看上方信息。
pause
exit /b 1
