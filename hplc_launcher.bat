@echo off
setlocal
cd /d "%~dp0"
title GW HPLC Log Parser

if not defined HPLC_LAUNCH_MODE set "HPLC_LAUNCH_MODE=production"
set "APP_URL=http://127.0.0.1:8765/"
if /i "%HPLC_LAUNCH_MODE%"=="test" set "APP_URL=http://127.0.0.1:8765/?mode=test"

echo.
echo  GW HPLC Log Parser
echo  ==================
echo  Mode: %HPLC_LAUNCH_MODE%
echo.

if not exist "dll\bin\Debug\GwHPLCAnalysis.dll" goto :missing_dll

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8765/api/version' -TimeoutSec 2; if ($r.StatusCode -eq 200) { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto :check_service_capability
goto :bootstrap

:check_service_capability
powershell -NoProfile -Command "try { $version = Invoke-RestMethod -UseBasicParsing 'http://127.0.0.1:8765/api/version' -TimeoutSec 2; $api = Invoke-RestMethod -UseBasicParsing 'http://127.0.0.1:8765/openapi.json' -TimeoutSec 2; if ($version.picker_api_revision -eq 2 -and $version.minute_analysis_api_revision -eq 3 -and $null -ne $api.paths.'/api/fs/pick' -and $null -ne $api.paths.'/api/logs/minute-analysis') { exit 0 } else { exit 1 } } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto :already_running
goto :restart_outdated_service

:restart_outdated_service
echo [SERVICE_OUTDATED_RESTARTING] Restarting the local parser service...
for /f "delims=" %%P in ('powershell -NoProfile -Command "$connection = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($connection) { $process = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $connection.OwningProcess); if ($process.Name -eq 'python.exe' -and $process.CommandLine -match 'hplc_web\.run') { [Console]::Write($process.ProcessId) } }"') do set "HPLC_SERVER_PID=%%P"
if not defined HPLC_SERVER_PID goto :port_in_use
taskkill /PID %HPLC_SERVER_PID% /T /F >nul 2>&1
timeout /T 1 /NOBREAK >nul
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
"%APP_PYTHON%" -c "import clr, fastapi, httpx, uvicorn" >nul 2>&1
if not errorlevel 1 goto :dependencies_ready

echo [FIRST RUN] Installing required packages. Please wait...
"%APP_PYTHON%" -m pip install -r "hplc_web\requirements.txt"
if errorlevel 1 goto :failed

:dependencies_ready
echo [READY] Opening %APP_URL%
echo Close this window to stop the local service.
echo.
"%APP_PYTHON%" -m hplc_web.run
if errorlevel 1 goto :failed
exit /b 0

:already_running
echo [SERVICE_ALREADY_RUNNING] Opening %APP_URL%
start "" "%APP_URL%"
exit /b 0

:port_in_use
echo [ERROR] Port 8765 is in use by a process that is not this parser service.
echo Stop that process, then run this launcher again.
pause
exit /b 1

:missing_dll
echo [ERROR] dll\bin\Debug\GwHPLCAnalysis.dll was not found.
echo Keep the launcher files in the repository root directory.
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
