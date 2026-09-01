#Requires -Version 5.1
param(
    [switch]$Test,   # 测试模式：仅显示状态后退出（供自动化验证）
    [ValidateSet('map','restore','status','start-gateway','stop-gateway')][string]$Action = ''
)
<#
    uart-map.ps1 - Windows <-> WSL 环境部署工具
    功能：
      1) 映射串口到 WSL : 将 Windows 串口(USB 转串口)通过 usbipd 挂载到 WSL，并加载内核驱动
      2) 恢复串口到 Windows (detach)
      3) 查看状态（串口 + 解析网关）
      4) 启动解析网关（Windows 侧解析服务，端口 8700，供 WSL 深度解析，REQS-0019）
      5) 停止解析网关

    用法：
      - 直接运行（双击 wsl环境部署.bat）→ 交互菜单
      - powershell -File uart-map.ps1 -Action start-gateway   （非交互，供自动化）
      - powershell -File uart-map.ps1 -Test                   （仅显示状态）
    依赖：
      - usbipd-win (usbipd.exe 位于 C:\Program Files\usbipd-win\usbipd.exe)
      - WSL2 发行版 (Ubuntu-22.04)
      - 解析网关：D:\019-wy-tool\ZZT_SELF\.build_plain（明文区）+ 主工作区 .venv
#>

# usbipd 会把 info/warning 写到 stderr，在 'Stop' 下会抛 NativeCommandError，
# 因此用 'Continue' 并手动检查 $LASTEXITCODE
$ErrorActionPreference = 'Continue'
$script:UsbipdPath = 'C:\Program Files\usbipd-win\usbipd.exe'
$script:WslDist = 'Ubuntu-22.04'

# ---- 解析网关配置（REQS-0019）----
$script:ProjectRoot = 'D:\019-wy-tool\ZZT_SELF'
$script:BuildPlain  = Join-Path $script:ProjectRoot '.build_plain'
$script:PythonExe   = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
$script:ParsePort   = 8700
$script:GatewayOut  = Join-Path $script:BuildPlain 'parse_svc.out.log'
$script:GatewayErr  = Join-Path $script:BuildPlain 'parse_svc.err.log'

function Get-ConsoleCodePage {
    # 将控制台切到 UTF-8，保证中文正常显示
    try { chcp 65001 | Out-Null } catch {}
    # 规避 cmd.exe 在 UNC 当前目录（\\wsl.localhost...）下无法启动的警告
    if ($PWD.ProviderPath -like '\\*') { Set-Location $env:USERPROFILE }
}

function Show-Banner {
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Windows <-> WSL 环境部署工具（串口 + 解析网关）" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""
}

function Test-Preconditions {
    if (-not (Test-Path $script:UsbipdPath)) {
        Write-Host "[错误] 未找到 usbipd.exe，请先安装 usbipd-win (winget install usbipd)" -ForegroundColor Red
        return $false
    }
    $wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
    if (-not $wsl) {
        Write-Host "[错误] 未找到 WSL，请先安装 WSL2" -ForegroundColor Red
        return $false
    }
    # 检查发行版 (wsl -l -q 输出 UTF-16LE，需要去掉 NUL 字节)
    $distsRaw = (& wsl.exe -l -q 2>$null | Out-String) -replace "`0", ""
    if ($distsRaw -notmatch [regex]::Escape($script:WslDist)) {
        Write-Host "[警告] 未检测到发行版 '$script:WslDist'，请修改脚本顶部的 WslDist" -ForegroundColor Yellow
    }
    return $true
}

function Get-SerialDevices {
    <#
        解析 usbipd list 输出，返回串口设备列表
        每项: @{ BusId='5-1'; VidPid='10c4:ea60'; Name='...COM4...'; State='Shared'|'Attached' }
        只保留包含 COM 的 USB 转串口设备（排除蓝牙串口等非 USB 设备，蓝牙不在 usbipd 列表）
    #>
    $raw = & $script:UsbipdPath list 2>&1 | Out-String
    $lines = $raw -split "`r?`n"
    $devices = @()
    $inConnected = $false
    foreach ($line in $lines) {
        if ($line -match '^Connected:') { $inConnected = $true; continue }
        if ($line -match '^Persisted:') { $inConnected = $false; continue }
        if (-not $inConnected) { continue }
        # 跳过表头
        if ($line -match '^BUSID') { continue }
        if ($line -match '^----') { continue }
        $t = $line.Trim()
        if (-not $t) { continue }
        # 解析: BUSID  VID:PID  DEVICE  STATE
        if ($t -match '^(\S+)\s+([0-9a-fA-F]{4}:[0-9a-fA-F]{4})\s+(.*?)\s+(\S+)$') {
            $devices += [PSCustomObject]@{
                BusId  = $Matches[1]
                VidPid = $Matches[2]
                Name   = $Matches[3].Trim()
                State  = $Matches[4].Trim()
            }
        }
    }
    # 只保留含 COM 的串口设备
    $serial = @($devices | Where-Object { $_.Name -match 'COM\d+' })
    return $serial
}

function Show-Status {
    $devices = Get-SerialDevices
    if ($devices.Count -eq 0) {
        Write-Host "未检测到 USB 串口设备 (COM)。" -ForegroundColor Yellow
        return
    }
    Write-Host "当前 USB 串口设备：" -ForegroundColor Green
    Write-Host ("{0,-8} {1,-10} {2,-50} {3}" -f 'BUSID','VID:PID','设备','状态')
    Write-Host ("{0,-8} {1,-10} {2,-50} {3}" -f '------','--------','----','----')
    foreach ($d in $devices) {
        $color = if ($d.State -eq 'Attached') { 'Green' } elseif ($d.State -eq 'Shared') { 'Yellow' } else { 'Gray' }
        Write-Host ("{0,-8} {1,-10} {2,-50} {3}" -f $d.BusId, $d.VidPid, $d.Name, $d.State) -ForegroundColor $color
    }
}

function Invoke-WslCommand {
    param([string]$Command)
    & wsl.exe -d $script:WslDist -e bash -lc $Command 2>&1
    return $LASTEXITCODE
}

function Map-To-Wsl {
    Write-Host "[1/3] 开始映射串口到 WSL ..." -ForegroundColor Green

    # 1. attach 所有串口设备
    $devices = Get-SerialDevices
    $targets = @($devices | Where-Object { $_.State -eq 'Shared' })
    if ($targets.Count -eq 0) {
        Write-Host "没有可映射的串口（可能已全部 Attached 或未共享）。" -ForegroundColor Yellow
    } else {
        foreach ($d in $targets) {
            Write-Host "  挂载 $($d.BusId)  $($d.Name) ..." -ForegroundColor Gray
            $busidVal = $d.BusId
            $attachOut = & cmd /c "`"$script:UsbipdPath`" attach --wsl=$script:WslDist --busid=$busidVal 2>&1"
            $attachOut | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  [警告] $($d.BusId) 挂载失败" -ForegroundColor Yellow
            }
        }
        Start-Sleep -Seconds 2
    }

    # 2. WSL 内加载 USB 串口驱动
    Write-Host "[2/3] WSL 内加载串口驱动 (ch341/cp210x/cdc_acm) ..." -ForegroundColor Green
    $modResult = Invoke-WslCommand "modprobe ch341 2>/dev/null; modprobe cp210x 2>/dev/null; modprobe cdc_acm 2>/dev/null; modprobe ftdi_sio 2>/dev/null; modprobe pl2303 2>/dev/null; sleep 2; ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null"
    Write-Host "  驱动加载完成，WSL 设备：" -ForegroundColor Gray
    foreach ($line in $modResult) {
        if ($line -match 'tty') {
            Write-Host "    $line" -ForegroundColor Green
        }
    }

    # 3. 检查 WSL 内是否出现设备
    Write-Host "[3/3] 验证映射结果 ..." -ForegroundColor Green
    $check = Invoke-WslCommand "ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null; echo ---; lsusb 2>/dev/null"
    $wslDev = @($check | Where-Object { $_ -match '/dev/tty' })
    if ($wslDev.Count -gt 0) {
        Write-Host "  WSL 已检测到串口设备:" -ForegroundColor Green
        foreach ($d in $wslDev) { Write-Host "    $d" -ForegroundColor Green }
        Write-Host "  [成功] 串口已映射到 WSL（如 /dev/ttyUSB0、/dev/ttyUSB1 ...）" -ForegroundColor Green
    } else {
        Write-Host "  [警告] WSL 未检测到 tty 设备，请检查 usbipd 防火墙与 WSL 驱动" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  提示：在 WSL 中使用串口的软件请访问 /dev/ttyUSB*，" -ForegroundColor DarkGray
    Write-Host "        Windows 侧对应 COM 将不可用（被独占）。" -ForegroundColor DarkGray
}

function Restore-FromWsl {
    Write-Host "开始释放串口回 Windows ..." -ForegroundColor Green
    $devices = Get-SerialDevices
    $targets = @($devices | Where-Object { $_.State -eq 'Attached' })
    if ($targets.Count -eq 0) {
        Write-Host "当前没有 Attached 状态的串口。" -ForegroundColor Yellow
    } else {
        foreach ($d in $targets) {
            Write-Host "  释放 $($d.BusId)  $($d.Name) ..." -ForegroundColor Gray
            $busidVal = $d.BusId
            $detachOut = & cmd /c "`"$script:UsbipdPath`" detach --busid=$busidVal 2>&1"
            $detachOut | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
        Start-Sleep -Seconds 2
    }
    Write-Host "已完成释放，串口应回到 Windows 可用状态。" -ForegroundColor Green
}

# ===================== 解析网关（REQS-0019）=====================

function Get-ParseProcess {
    # 返回占用解析网关端口(8700)的进程；未运行返回 $null
    $conn = Get-NetTCPConnection -LocalPort $script:ParsePort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { return Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue }
    return $null
}

function Show-GatewayStatus {
    $p = Get-ParseProcess
    if (-not $p) {
        Write-Host "解析网关（端口 $($script:ParsePort)）：未运行" -ForegroundColor DarkGray
        return
    }
    Write-Host "解析网关（端口 $($script:ParsePort)）：运行中（PID $($p.Id)）" -ForegroundColor Green
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$($script:ParsePort)/health" -TimeoutSec 3
        Write-Host ("  /health: status={0} dll_available={1}" -f $r.status, $r.dll_available) -ForegroundColor Green
    } catch {
        Write-Host "  /health 探测失败：$_" -ForegroundColor Yellow
    }
}

function Start-ParseGateway {
    Write-Host "[解析网关] 启动 Windows 解析服务（端口 $($script:ParsePort)）..." -ForegroundColor Green
    if (Get-ParseProcess) {
        Write-Host "  解析网关已在运行（PID $(Get-ParseProcess | Select-Object -ExpandProperty Id)）。" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path (Join-Path $script:BuildPlain 'apps\parser_service\app.py'))) {
        Write-Host "  [错误] 未找到解析服务代码：$($script:BuildPlain)\apps\parser_service" -ForegroundColor Red
        Write-Host "  请先在 .build_plain 同步 codex/0019-win-parse-service 分支。" -ForegroundColor Yellow
        return
    }
    if (-not (Test-Path $script:PythonExe)) {
        Write-Host "  [错误] 未找到 Python：$script:PythonExe" -ForegroundColor Red
        return
    }
    $env:PYTHONPATH = "$($script:BuildPlain)\apps;$($script:BuildPlain)\libs"
    Remove-Item $script:GatewayOut, $script:GatewayErr -ErrorAction SilentlyContinue
    Start-Process -FilePath $script:PythonExe -ArgumentList '-m','parser_service.run' `
        -WorkingDirectory $script:BuildPlain -WindowStyle Hidden `
        -RedirectStandardOutput $script:GatewayOut -RedirectStandardError $script:GatewayErr
    # 轮询等待就绪（uvicorn + Python.NET + DLL 加载需数秒），最多 20 秒
    $deadline = (Get-Date).AddSeconds(20)
    $ready = $false
    do {
        Start-Sleep -Seconds 1
        if (Get-ParseProcess) {
            try {
                $r = Invoke-RestMethod -Uri "http://127.0.0.1:$($script:ParsePort)/health" -TimeoutSec 2
                if ($r.status -eq 'ok') { $ready = $true; break }
            } catch {}
        }
    } while ((Get-Date) -lt $deadline)
    $p = Get-ParseProcess
    if ($ready -and $p) {
        Write-Host "  解析网关已启动：PID $($p.Id)" -ForegroundColor Green
        Write-Host "  健康检查：http://127.0.0.1:$($script:ParsePort)/health" -ForegroundColor DarkGray
        Write-Host "  WSL 侧经 <WSL网关IP>:$($script:ParsePort) 访问（见 config/remote_parse.json）。" -ForegroundColor DarkGray
    } else {
        Write-Host "  [失败] 网关未起来，请查看日志：$($script:GatewayErr)" -ForegroundColor Red
    }
}

function Stop-ParseGateway {
    Write-Host "[解析网关] 停止 Windows 解析服务..." -ForegroundColor Green
    $p = Get-ParseProcess
    if (-not $p) {
        Write-Host "  解析网关未在运行。" -ForegroundColor Yellow
        return
    }
    Stop-Process -Id $p.Id -Force
    Start-Sleep -Seconds 1
    if (Get-ParseProcess) {
        Write-Host "  [警告] 端口 $($script:ParsePort) 仍被占用，请手动结束进程。" -ForegroundColor Yellow
    } else {
        Write-Host "  解析网关已停止（PID $($p.Id)）。" -ForegroundColor Green
    }
}

# ---------- 主流程 ----------
Get-ConsoleCodePage
Show-Banner

if (-not (Test-Preconditions)) {
    Read-Host "按回车退出"
    exit 1
}

# 非交互动作（供自动化/脚本调用）
if ($Action) {
    switch ($Action) {
        'map'           { Map-To-Wsl }
        'restore'       { Restore-FromWsl }
        'status'        { Show-Status; Show-GatewayStatus }
        'start-gateway' { Start-ParseGateway }
        'stop-gateway'  { Stop-ParseGateway }
    }
    exit 0
}

if ($Test) {
    Show-Status
    Show-GatewayStatus
    exit 0
}

# 交互菜单
$running = $true
while ($running) {
    Write-Host ""
    Write-Host "请选择操作：" -ForegroundColor White
    Write-Host "  [1] 映射串口到 WSL  (attach + 加载驱动)"
    Write-Host "  [2] 恢复串口到 Windows (detach)"
    Write-Host "  [3] 查看当前状态（串口 + 解析网关）"
    Write-Host "  [4] 启动解析网关（Windows 侧，端口 8700）"
    Write-Host "  [5] 停止解析网关"
    Write-Host "  [Q] 退出"
    Write-Host ""
    $choice = Read-Host "请输入选项"

    switch ($choice.Trim().ToUpper()) {
        '1' { Map-To-Wsl; break }
        '2' { Restore-FromWsl; break }
        '3' { Show-Status; Show-GatewayStatus; break }
        '4' { Start-ParseGateway; break }
        '5' { Stop-ParseGateway; break }
        'Q' { $running = $false; break }
        default { Write-Host "无效选项，请重试。" -ForegroundColor Yellow }
    }
}
Write-Host "再见！" -ForegroundColor Cyan
