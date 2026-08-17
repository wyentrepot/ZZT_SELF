#Requires -Version 5.1
param(
    [switch]$Test   # 测试模式：仅显示状态后退出（供自动化验证）
)
<#
    uart-map.ps1 - Windows <-> WSL 串口映射工具
    功能：
      1) 映射到 WSL : 将 Windows 串口(USB 转串口)通过 usbipd 挂载到 WSL，并加载内核驱动
      2) 恢复       : 将所有串口从 WSL 释放回 Windows
      3) 查看状态   : 显示当前串口映射状态
    依赖：
      - usbipd-win (usbipd.exe 位于 C:\Program Files\usbipd-win\usbipd.exe)
      - WSL2 发行版 (Ubuntu-22.04)
#>

# usbipd 会把 info/warning 写到 stderr，在 'Stop' 下会抛 NativeCommandError，
# 因此用 'Continue' 并手动检查 $LASTEXITCODE
$ErrorActionPreference = 'Continue'
$script:UsbipdPath = 'C:\Program Files\usbipd-win\usbipd.exe'
$script:WslDist = 'Ubuntu-22.04'

function Get-ConsoleCodePage {
    # 将控制台切到 UTF-8，保证中文正常显示
    try { chcp 65001 | Out-Null } catch {}
    # 规避 cmd.exe 在 UNC 当前目录（\\wsl.localhost...）下无法启动的警告
    if ($PWD.ProviderPath -like '\\*') { Set-Location $env:USERPROFILE }
}

function Show-Banner {
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Windows <-> WSL 串口映射工具" -ForegroundColor Cyan
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

# ---------- 主流程 ----------
Get-ConsoleCodePage
Show-Banner

if (-not (Test-Preconditions)) {
    Read-Host "按回车退出"
    exit 1
}

if ($Test) {
    Show-Status
    exit 0
}

$running = $true
while ($running) {
    Write-Host ""
    Write-Host "请选择操作：" -ForegroundColor White
    Write-Host "  [1] 映射串口到 WSL  (attach + 加载驱动)"
    Write-Host "  [2] 恢复串口到 Windows (detach)"
    Write-Host "  [3] 查看当前状态"
    Write-Host "  [Q] 退出"
    Write-Host ""
    $choice = Read-Host "请输入选项"

    switch ($choice.Trim().ToUpper()) {
        '1' { Map-To-Wsl; break }
        '2' { Restore-FromWsl; break }
        '3' { Show-Status; break }
        'Q' { $running = $false; break }
        default { Write-Host "无效选项，请重试。" -ForegroundColor Yellow }
    }
}
Write-Host "再见！" -ForegroundColor Cyan
