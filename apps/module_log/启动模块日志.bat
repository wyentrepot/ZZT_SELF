@echo off
setlocal
cd /d "%~dp0.."
title 模块日志 / 烧录

echo.
echo  模块日志 / 烧录(Module Log ^& Flash)
echo  ====================================
echo.

REM =====================================================================
REM 启动模块日志桌面软件（内嵌窗口 exe）。
REM
REM 注意：本工作区源码受 E-SafeNet 透明加密（.py 为密文），
REM 源码直跑（python -m module_log.desktop）在当前环境必然失败，
REM 因此统一改为启动已构建的 dist\模块日志\模块日志.exe。
REM 如需从源码开发/测试，请到 WSL 工作区（见 docs\开发指南.md）。
REM
REM 在 cmd（含双击与非交互调用）中均可正确匹配。
REM =====================================================================

:check_exe
if exist "%~dp0..\..\dist\模块日志\模块日志.exe" goto :launch
echo [错误] 未找到 dist\模块日志\模块日志.exe。
echo 请先执行 tools\packaging\build_exe.bat 构建(选 3)。
echo 或使用 WSL 工作区源码开发(docs\开发指南.md)。
pause
exit /b 1

:launch
echo.
echo [启动] 模块日志本地软件(内嵌窗口 exe)...
echo 关闭窗口即停止服务。
echo.
start "" "%~dp0..\..\dist\模块日志\模块日志.exe"
exit /b 0

:failed
echo.
echo [错误] 启动失败，请保持窗口查看上方信息。
pause
exit /b 1
