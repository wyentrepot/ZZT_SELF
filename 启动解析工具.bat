@echo off
set "HPLC_LAUNCH_MODE=production"
call "%~dp0hplc_launcher.bat"
exit /b %errorlevel%
