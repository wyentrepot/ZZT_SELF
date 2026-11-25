@echo off
chcp 65001 >nul 2>&1
rem ===============================================================
rem NOTE: keep this file ASCII-only and CRLF-terminated.
rem       Multi-byte (non-ASCII) text inside a .bat makes cmd.exe
rem       mis-parse lines and execute fragments as commands.
rem ===============================================================
rem Register FC-Wsl-Control scheduled task (one-time, admin):
rem   - wsl-control.py HTTP control service on 0.0.0.0:8766
rem   - logon autostart, highest privileges, auto-restart on failure
rem   - firewall rule for WSL subnet (TCP 8766)
rem ===============================================================
setlocal EnableExtensions
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administrator rights required, requesting UAC elevation ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
title WSL Control API Register
if not exist "%~dp0wsl-control.py" (
    echo [ERROR] wsl-control.py not found next to this bat file.
    pause
    exit /b 1
)
if not exist "%~dp0wsl-control-register.ps1" (
    echo [ERROR] wsl-control-register.ps1 not found next to this bat file.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0wsl-control-register.ps1"
pause
