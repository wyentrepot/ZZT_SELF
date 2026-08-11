@echo off
setlocal
cd /d "%~dp0.."
title 打包工具（侦听台 / 模块日志）

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 启动工具.bat once first to create it.
    pause
    exit /b 1
)

echo.
echo  HPLC 打包工具
echo  ============
echo    1 = 侦听台网页版（控制台，dist\侦听台）
echo    2 = 侦听台桌面版（pywebview 内嵌窗口，dist\侦听台桌面）
echo    3 = 模块日志桌面版（pywebview 内嵌窗口，dist\模块日志）
echo.
set "PKG_CHOICE="
set /p "PKG_CHOICE=请输入 1 / 2 / 3 后回车: "
if /i "%PKG_CHOICE%"=="1" goto :listener_web
if /i "%PKG_CHOICE%"=="2" goto :listener_desktop
if /i "%PKG_CHOICE%"=="3" goto :module_desktop
echo 输入无效，默认打包侦听台网页版(1)。
set "PKG_CHOICE=1"
goto :listener_web

:install_deps
echo [1/3] 安装依赖（pyinstaller + pywebview）...
".venv\Scripts\python.exe" -m pip install pyinstaller pywebview
if errorlevel 1 goto :failed
exit /b 0

:listener_web
if not exist "shared\dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] shared\dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)
call :install_deps
echo [2/3] 打包侦听台网页版（PyInstaller onedir）...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\hplc_parser.spec
if errorlevel 1 goto :failed
echo [3/3] 完成。
echo 输出: %CD%\dist\侦听台\
echo 冒烟: .venv\Scripts\python.exe scripts\smoke_test_packaged.py
exit /b 0

:listener_desktop
if not exist "shared\dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] shared\dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)
call :install_deps
echo [2/3] 打包侦听台桌面版（pywebview windowed）...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\hplc_parser_desktop.spec
if errorlevel 1 goto :failed
echo [3/3] 完成。
echo 输出: %CD%\dist\侦听台桌面\
exit /b 0

:module_desktop
call :install_deps
echo [2/3] 打包模块日志桌面版（pywebview windowed）...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\module_log.spec
if errorlevel 1 goto :failed
echo [3/3] 完成。
echo 输出: %CD%\dist\模块日志\
exit /b 0

:failed
echo.
echo [ERROR] 打包失败。请查看上方信息。
pause
exit /b 1
