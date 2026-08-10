@echo off
setlocal
cd /d "%~dp0"
title GW HPLC 侦听台 / 模块日志

echo.
echo  GW HPLC 侦听台 / 模块日志
echo  ==============================
echo.

if not exist "dll\bin\Debug\GwHPLCAnalysis.dll" goto :missing_dll

:choose_mode
echo  请选择启动模式:
echo    1 = 侦听台            (端口 8765)
echo    2 = 模块日志/烧录      (端口 8766)
echo    3 = 全部启动          (8765 + 8766)
echo.
set "HPLC_CHOICE="
set /p HPLC_CHOICE="请输入 1 / 2 / 3 后回车: "
if /i "%HPLC_CHOICE%"=="1" goto :start_listener
if /i "%HPLC_CHOICE%"=="2" goto :start_module
if /i "%HPLC_CHOICE%"=="3" goto :start_all
echo  无效选择，默认启动侦听台(1)。
goto :start_listener

:bootstrap
where python >nul 2>&1
if errorlevel 1 goto :missing_python

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [FIRST RUN] Creating the Python environment...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
if exist ".venv\.deps_ready" goto :launch
"%APP_PYTHON%" -c "import fastapi, httpx, uvicorn" >nul 2>&1
if not errorlevel 1 (
  echo. > ".venv\.deps_ready"
  goto :launch
)

echo [FIRST RUN] Installing required packages. Please wait...
"%APP_PYTHON%" -m pip install -r "hplc_web\requirements.txt"
if errorlevel 1 goto :failed
echo. > ".venv\.deps_ready"

:launch
if /i "%HPLC_CHOICE%"=="2" goto :start_module
if /i "%HPLC_CHOICE%"=="3" goto :start_all

:start_listener
echo.
echo [启动] 侦听台 -> http://127.0.0.1:8765/
echo 关闭本窗口即停止服务.
echo.
"%APP_PYTHON%" -m hplc_web.listener_run
if errorlevel 1 goto :failed
exit /b 0

:start_module
echo.
echo [启动] 模块日志/烧录 -> http://127.0.0.1:8766/module-serial
echo 关闭本窗口即停止服务.
echo.
"%APP_PYTHON%" -m hplc_web.module_serial_run
if errorlevel 1 goto :failed
exit /b 0

:start_all
echo.
echo [启动] 侦听台(8765) + 模块日志(8766)...
echo 关闭本窗口后，请到任务管理器结束 python 进程停止服务.
echo.
start "listener-8765" "%APP_PYTHON%" -m hplc_web.listener_run
start "module-8766" "%APP_PYTHON%" -m hplc_web.module_serial_run
exit /b 0

:missing_dll
echo [ERROR] dll\bin\Debug\GwHPLCAnalysis.dll was not found.
pause
exit /b 1

:missing_python
echo [ERROR] Python was not found in PATH.
echo Install Python 3 and enable "Add Python to PATH".
pause
exit /b 1

:failed
echo.
echo [ERROR] Startup failed. Keep this window open and check the message above.
pause
exit /b 1
