@echo off
setlocal
cd /d "%~dp0"
title 侦听台 / 模块日志 启动器

echo.
echo  侦听台改造 启动器
echo  ===================
echo    1 = 侦听台（串口采集 + 日志解析，端口 8765）
echo    2 = 模块日志 / 烧录（端口 8766）
echo    3 = 全部启动（8765 + 8766）
echo    4 = 模块日志本地软件（内嵌窗口 exe）
echo    5 = 侦听台本地软件（内嵌窗口 exe）
echo.
set "HPLC_CHOICE="
set /p "HPLC_CHOICE=请输入 1 / 2 / 3 / 4 / 5 后回车: "
if /i "%HPLC_CHOICE%"=="1" goto :listener
if /i "%HPLC_CHOICE%"=="2" goto :module
if /i "%HPLC_CHOICE%"=="3" goto :both
if /i "%HPLC_CHOICE%"=="4" goto :module_desktop
if /i "%HPLC_CHOICE%"=="5" goto :listener_desktop
echo 输入无效，默认启动侦听台(1)。
set "HPLC_CHOICE=1"
goto :listener

:listener
call "%~dp0listener\启动侦听台.bat"
exit /b %errorlevel%

:module
call "%~dp0module_log\启动模块日志.bat"
exit /b %errorlevel%

:module_desktop
if not exist "%~dp0dist\模块日志\模块日志.exe" (
    echo [ERROR] 未找到 dist/模块日志/模块日志.exe。请先执行 packaging/build_exe.bat 构建。
    pause
    exit /b 1
)
echo.
echo [START] 启动模块日志本地软件（内嵌窗口）...
start "" "%~dp0dist\模块日志\模块日志.exe"
exit /b 0

:listener_desktop
if not exist "%~dp0dist\侦听台桌面\侦听台桌面.exe" (
    echo [ERROR] 未找到 dist/侦听台桌面/侦听台桌面.exe。请先执行 packaging/build_exe.bat 构建。
    pause
    exit /b 1
)
echo.
echo [START] 启动侦听台本地软件（内嵌窗口）...
start "" "%~dp0dist\侦听台桌面\侦听台桌面.exe"
exit /b 0

:both
echo.
echo [START] 启动全部：侦听台(8765) + 模块日志(8766) ...
echo 结束请在任务管理器中终止对应 python 进程。
echo.
start "侦听台-8765" cmd /c "%~dp0listener\启动侦听台.bat"
start "模块日志-8766" cmd /c "%~dp0module_log\启动模块日志.bat"
exit /b 0

