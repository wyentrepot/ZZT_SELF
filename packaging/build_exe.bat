@echo off
setlocal
cd /d "%~dp0.."
title 打包工具（侦听台 / 模块日志）

:check_python
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未在 PATH 中找到 Python。请安装 Python 3 并勾选 "Add Python to PATH"。
    pause
    exit /b 1
)

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [首次运行] 正在创建 Python 环境（.venv）...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready

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
if exist ".venv\.deps_build" goto :deps_ok
echo [1/3] 安装依赖（打包 + 模块日志运行依赖）...
".venv\Scripts\python.exe" -m pip install -r "packaging\requirements-build.txt" -r "module_log\requirements.txt"
if errorlevel 1 goto :failed
echo. > ".venv\.deps_build"
:deps_ok
exit /b 0

:listener_web
if not exist "shared\dll\bin\Debug\GwHPLCAnalysis.dll" (
    echo [ERROR] shared\dll\bin\Debug\GwHPLCAnalysis.dll not found. Build the C# project first.
    pause
    exit /b 1
)
call :install_deps
echo [2/3] 打包侦听台网页版（PyInstaller onedir）...
call :build packaging\hplc_parser.spec
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
call :build packaging\hplc_parser_desktop.spec
if errorlevel 1 goto :failed
echo [3/3] 完成。
echo 输出: %CD%\dist\侦听台桌面\
exit /b 0

:module_desktop
call :install_deps
echo [2/3] 打包模块日志桌面版（pywebview windowed）...
call :build packaging\module_log.spec
if errorlevel 1 goto :failed
echo [3/3] 完成。
echo 输出: %CD%\dist\模块日志\
exit /b 0

REM =====================================================================
REM :build <spec 相对路径>
REM 检测工作区 .py 是否处于 E-SafeNet 透明加密状态（文件头 62 14 = "b.#"）。
REM 若是密文，则 git archive HEAD 导出明文副本到 .build_plain\ 后在其中构建
REM （避免 PyInstaller 读到密文报 SyntaxError）；若是明文则直接在工作区构建。
REM 产物统一输出到工作区 dist\，中间产物放 .build_plain\build\。
REM =====================================================================
:build
set "SPEC=%~1"
".venv\Scripts\python.exe" "packaging\check_encrypted.py" "module_log\desktop.py"
if errorlevel 1 goto :build_plain
echo （工作区源码为明文，直接构建）
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm "%SPEC%"
exit /b %errorlevel%

:build_plain
echo （检测到 E-SafeNet 加密源码，从 git 导出明文副本后构建...）
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 需要 git 导出明文源码，但未在 PATH 中找到 git。
    exit /b 1
)
if not exist ".build_plain" mkdir ".build_plain"
if exist ".build_plain\build" rmdir /s /q ".build_plain\build"
if exist ".build_plain\src.tar" del /q ".build_plain\src.tar"
REM git archive 必须在仓库根执行（打包整个仓库），tar 解压需 cd 到目标目录
git archive -o ".build_plain\src.tar" HEAD
if errorlevel 1 (
    echo [ERROR] git archive 导出明文副本失败。
    exit /b 1
)
pushd ".build_plain"
tar -xf "src.tar" >nul 2>&1
REM tar 在中文 Windows 下对 UTF-8 中文文件名会报错（不影响 ASCII 名代码文件），
REM 因此不依据 tar 退出码判断，改为验证关键源码文件是否解出
if not exist "module_log\desktop.py" (
    popd
    echo [ERROR] 解压明文副本失败（未找到 module_log\desktop.py）。
    exit /b 1
)
del /q "src.tar" >nul 2>&1
popd
".venv\Scripts\python.exe" -m PyInstaller --clean --noconfirm --distpath "dist" --workpath ".build_plain\build" ".build_plain\%SPEC%"
set "BUILD_RC=%errorlevel%"
rmdir /s /q ".build_plain\build" >nul 2>&1
exit /b %BUILD_RC%

:failed
echo.
echo [ERROR] 打包失败。请查看上方信息。
pause
exit /b 1
