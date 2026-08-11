@echo off
setlocal
cd /d "%~dp0.."
title Build exe (listener / module_log)

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 启动工具.bat once first to create it.
    pause
    exit /b 1
)

rem ---- 默认构建侦听台；传 "module" 参数构建模块日志 ----
set "TARGET=%1"
if /i "%TARGET%"=="module" goto :module
goto :listener

:listener
if not exist "shared\dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] shared\dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)
echo [1/3] Installing PyInstaller...
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto :failed
echo [2/3] Building listener (PyInstaller onedir)...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\hplc_parser.spec
if errorlevel 1 goto :failed
echo [3/3] Done.
echo Output: %CD%\dist\侦听台\
echo Smoke test: .venv\Scripts\python.exe scripts\smoke_test_packaged.py
exit /b 0

:module
echo [1/3] Installing PyInstaller + pywebview...
".venv\Scripts\python.exe" -m pip install pyinstaller pywebview
if errorlevel 1 goto :failed
echo [2/3] Building module_log (PyInstaller onedir, pywebview windowed)...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\module_log.spec
if errorlevel 1 goto :failed
echo [3/3] Done.
echo Output: %CD%\dist\模块日志\
exit /b 0

:failed
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1
