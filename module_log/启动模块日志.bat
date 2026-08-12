@echo off
setlocal
cd /d "%~dp0.."
title 模块日志 / 烧录

echo.
echo  模块日志 / 烧录（Module Log ^& Flash）
echo  ====================================
echo.

:check_python
where python >nul 2>&1
if errorlevel 1 goto :missing_python

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [首次运行] 正在创建 Python 环境...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
if exist ".venv\.deps_module" goto :launch
"%APP_PYTHON%" -c "import fastapi, httpx, uvicorn, serial" >nul 2>&1
if not errorlevel 1 (
  echo. > ".venv\.deps_module"
  goto :launch
)
echo [首次运行] 正在安装依赖，请稍候...
"%APP_PYTHON%" -m pip install -r "module_log\requirements.txt"
if errorlevel 1 goto :failed
echo. > ".venv\.deps_module"

:launch
echo.
echo [启动] 模块日志/烧录 本地软件（内嵌窗口，源码直跑）
echo 关闭窗口即停止服务。开发模式无需打包 exe。
echo.
"%APP_PYTHON%" -m module_log.desktop
if errorlevel 1 goto :failed
exit /b 0

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
