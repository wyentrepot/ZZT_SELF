@echo off
setlocal
cd /d "%~dp0.."
title Build HPLC Parser exe

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run 启动解析工具.bat once first to create it.
    pause
    exit /b 1
)
if not exist "dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)

echo [1/3] Installing PyInstaller...
".venv\Scripts\python.exe" -m pip install pyinstaller
if errorlevel 1 goto :failed

echo [2/3] Building package (PyInstaller onedir)...
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm packaging\hplc_parser.spec
if errorlevel 1 goto :failed

echo [3/3] Done.
echo Output: %CD%\dist\侦听台\
echo Smoke test: .venv\Scripts\python.exe scripts\smoke_test_packaged.py
exit /b 0

:failed
echo.
echo [ERROR] Build failed. See messages above.
pause
exit /b 1
