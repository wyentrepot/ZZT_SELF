@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0..\.."
title AI 工作台（网页版，端口 8790）

echo.
echo  AI 工作台（Workbench）
echo  ======================
echo.

:check_python
where python >nul 2>&1
if errorlevel 1 goto :missing_python

if exist ".venv\Scripts\python.exe" goto :venv_ready
echo [首次运行] 正在创建 Python 环境...
python -m venv ".venv"
if errorlevel 1 goto :failed

:venv_ready
set "APP_PYTHON=.venv\Scripts\python.exe"
"%APP_PYTHON%" -c "import fastapi, httpx, uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [错误] 缺少依赖 fastapi/httpx/uvicorn，请先执行 tools\packaging\build_exe.bat 或手动安装。
    pause
    exit /b 1
)

REM =====================================================================
REM E-SafeNet 透明加密环境：磁盘 .py 为密文，python 直接读必报
REM SyntaxError（source code string cannot contain null bytes）。
REM 因此统一从 git archive HEAD 导出明文副本到 .build_plain\ 再运行。
REM .build_plain\ 已在 .gitignore，不污染仓库。
REM =====================================================================

echo.
where git >nul 2>&1
if errorlevel 1 (
    echo [错误] 需要 git 导出明文源码，但未在 PATH 中找到 git。
    pause
    exit /b 1
)
if not exist ".build_plain" mkdir ".build_plain"
REM 增量导出：仅当 .build_plain 缺少关键文件，或记录 rev 与当前 HEAD 不一致时才重建。
REM 避免每次启动都重复 git archive + tar（导出仅需一次，之后秒级启动）。
set "NEED_EXPORT="
if not exist ".build_plain\apps\workbench\run.py" set "NEED_EXPORT=1"
if not exist ".build_plain\git-rev.txt" set "NEED_EXPORT=1"
if exist ".build_plain\git-rev.txt" (
    for /f "delims=" %%R in (.build_plain\git-rev.txt) do set "SAVED_REV=%%R"
    for /f "delims=" %%R in ('git rev-parse HEAD') do set "CURR_REV=%%R"
    if not "!SAVED_REV!"=="!CURR_REV!" set "NEED_EXPORT=1"
)
if not defined NEED_EXPORT (
    echo [准备] 明文副本已是最新（HEAD 未变化），跳过导出。
    goto :ready
)
echo [准备] 从 git 导出明文源码副本（E-SafeNet 加密环境，HEAD 有更新）...
if exist ".build_plain\src.tar" del /q ".build_plain\src.tar"
git archive -o ".build_plain\src.tar" HEAD
if errorlevel 1 (
    echo [错误] git archive 导出明文副本失败。
    pause
    exit /b 1
)
REM Windows 自带 tar 对 UTF-8 中文文件名解压失败，且会连带丢失部分 ASCII 文件
REM （实测 shared/infra.py、sim_concentrator 等关键文件丢失，导致启动报 SyntaxError）。
REM 改用 venv python 的 tarfile 在仓库根解压（完整支持 UTF-8 文件名）。
"%~dp0..\..\.venv\Scripts\python.exe" -c "import tarfile; tarfile.open(r'.build_plain\src.tar').extractall(r'.build_plain')"
if errorlevel 1 (
    echo [错误] 解压明文副本失败（python tarfile 异常）。
    pause
    exit /b 1
)
del /q ".build_plain\src.tar" >nul 2>&1
git rev-parse HEAD > ".build_plain\git-rev.txt"
:ready

echo.
echo [启动] AI 工作台 ^-^> http://127.0.0.1:8790/
echo 关闭此窗口即停止服务。
echo 提示：首次启动 uvicorn 预热约 10~25 秒。
echo.
REM 在明文副本中运行：apps/、libs/ 需都在 PYTHONPATH
set "WORKBENCH_DIR=%CD%\.build_plain"
set "PYTHONPATH=%WORKBENCH_DIR%\apps;%WORKBENCH_DIR%\libs;%PYTHONPATH%"
"%APP_PYTHON%" -m workbench.run
if errorlevel 1 goto :failed
exit /b 0

:missing_python
echo [错误] 未在 PATH 中找到 Python。
echo 请安装 Python 3 并勾选 "Add Python to PATH"。
pause
exit /b 1

:failed
echo.
echo [错误] 启动失败，请保持窗口查看上方信息。
pause
exit /b 1
