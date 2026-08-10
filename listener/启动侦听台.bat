@echo off
setlocal
cd /d "%~dp0.."
title 侦听台（串口采集 + 日志解析）

echo.
echo  侦听台（Listener）
echo  =================
echo.

if not exist "shared\dll\bin\Debug\GwHPLCAnalysis.dll" goto :missing_dll

:check_python
where python >nul 2>&1
if errorlevel 1 goto :missing_python

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [首次运行] 正在创建 Python 环境...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
if exist ".venv\.deps_listener" goto :launch
"%APP_PYTHON%" -c "import fastapi, httpx, uvicorn, serial, pythonnet" >nul 2>&1
if not errorlevel 1 (
  echo. > ".venv\.deps_listener"
  goto :launch
)
echo [首次运行] 正在安装依赖，请稍候...
"%APP_PYTHON%" -m pip install -r "listener\requirements.txt"
if errorlevel 1 goto :failed
echo. > ".venv\.deps_listener"

:launch
echo.
echo [启动] 侦听台 ^-^> http://127.0.0.1:8765/
echo 关闭此窗口即停止服务。
echo.
"%APP_PYTHON%" -m listener.run
if errorlevel 1 goto :failed
exit /b 0

:missing_dll
echo [错误] 未找到解析库：shared\dll\bin\Debug\GwHPLCAnalysis.dll
echo 请先编译 C# 工程（shared\dll\DLL_NwHPLCAnalysis.csproj）。
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
