@echo off
set "HPLC_LAUNCH_MODE=test"
call "%~dp0hplc_launcher.bat"
exit /b %errorlevel%
