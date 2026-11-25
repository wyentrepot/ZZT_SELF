@echo off
chcp 65001 >nul 2>&1
rem ===============================================================
rem NOTE: keep this file ASCII-only and CRLF-terminated.
rem       Multi-byte (non-ASCII) text inside a .bat makes cmd.exe
rem       mis-parse lines and execute fragments as commands.
rem       uart-map.ps1 switches the console to UTF-8 by itself.
rem ===============================================================
rem WSL environment deploy:
rem   [6] Python serial bridge toggle (recommended)
rem   (usbipd map/restore removed 2026-11-17; serial ports go through the Python bridge)
rem   [4]/[5] Windows parse gateway
rem ===============================================================
setlocal EnableExtensions
rem elevation kept: schtasks (FC-Diag-Service) and firewall setup may need admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Administrator rights required, requesting UAC elevation ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
title WSL Host Deploy (Bridge/gateway/diag)
if not exist "%~dp0..\win_host\uart-map.ps1" (
    echo [ERROR] uart-map.ps1 not found in tools\win_host\.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0..\win_host\uart-map.ps1"
pause
