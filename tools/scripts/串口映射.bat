@echo off
rem UART mapping launcher - starts the PowerShell script
title UART Map Tool
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uart-map.ps1"
