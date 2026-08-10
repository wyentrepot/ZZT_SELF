@echo off
setlocal
cd /d "%~dp0"
title GW HPLC Listener / Module

echo.
echo  GW HPLC Listener / Module Log
echo  ================================
echo.

if not exist "dll\bin\Debug\GwHPLCAnalysis.dll" goto :missing_dll

:choose_mode
echo  Select startup mode:
echo    1 = Listener (serial capture, port 8765)
echo    2 = Module log / flash (port 8766)
echo    3 = Start both (8765 + 8766)
echo.
set "HPLC_CHOICE="
set /p "HPLC_CHOICE=Enter 1 / 2 / 3 and press Enter: "
if /i "%HPLC_CHOICE%"=="1" goto :bootstrap
if /i "%HPLC_CHOICE%"=="2" goto :bootstrap
if /i "%HPLC_CHOICE%"=="3" goto :bootstrap
echo  Invalid choice, default to Listener(1).
set "HPLC_CHOICE=1"
goto :bootstrap

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
echo [START] Listener ^-^> http://127.0.0.1:8765/
echo Close this window to stop the service.
echo.
"%APP_PYTHON%" -m hplc_web.listener_run
if errorlevel 1 goto :failed
exit /b 0

:start_module
echo.
echo [START] Module log/flash ^-^> http://127.0.0.1:8766/module-serial
echo Close this window to stop the service.
echo.
"%APP_PYTHON%" -m hplc_web.module_serial_run
if errorlevel 1 goto :failed
exit /b 0

:start_all
echo.
echo [START] Listener(8765) + Module(8766) ...
echo To stop, end the python processes in Task Manager.
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
