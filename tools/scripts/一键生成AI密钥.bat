@echo off
rem One-click: generate a random AI admin key and install it (user env var).
title AI ÃÜÔ¿Ò»¼üÉú³É
chcp 936 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gen_ai_key.ps1"
echo.
pause
