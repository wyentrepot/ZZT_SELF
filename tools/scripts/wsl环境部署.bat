@echo off
chcp 65001 >nul
rem WSL 环境部署：串口映射 + 解析网关控制
title WSL 环境部署
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uart-map.ps1"
