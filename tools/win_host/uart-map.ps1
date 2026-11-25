#Requires -Version 5.1
param(
    [switch]$Test,   # 测试模式：仅显示状态后退出（供自动化验证）
    [ValidateSet('status','start-gateway','stop-gateway','bridge-start','bridge-stop','bridge-toggle','bridge-restart','bridge-status','host-info','svc-start','svc-stop','svc-restart','svc-status')][string]$Action = '',
    [switch]$Json    # JSON 模式：stdout 仅输出一份结构化 JSON（供 wsl-control HTTP 接口调用）
)
<#
    uart-map.ps1 - Windows 宿主控制后端（串口桥 / 解析网关 / 诊断服务）
    功能：
      1) 查看宿主状态（Windows COM + Python 串口桥 + 解析网关 + 诊断服务）
      2) Python 串口桥开关（启动/停止/重启 Windows 桥；停止即释放 COM 给 Windows 工具）
      3) 启动/停止解析网关（Windows 侧解析服务，端口 8700，供 WSL 深度解析，REQS-0019）
      4) 诊断服务开关（FC-Diag-Service, HTTP 8765）
      5) 宿主机信息（COM 列表、Python、桥配置路径、WSL 发行版、工作台探活）
    已移除：usbipd 映射/恢复（2026-11-17；串口统一走 Python 桥，不再 attach 到 WSL）

    用法：
      - 直接运行（双击 wsl环境部署.bat）→ 交互菜单
      - powershell -File uart-map.ps1 -Action start-gateway   （非交互，供自动化）
      - powershell -File uart-map.ps1 -Action host-info -Json （结构化输出，供 wsl-control）
      - powershell -File uart-map.ps1 -Test                   （仅显示状态）
    依赖：
      - WSL2 发行版 (Ubuntu-22.04)
      - 解析网关：D:\019-wy-tool\ZZT_SELF\.build_plain（明文区）+ 主工作区 .venv
#>

# JSON 模式：脚本级空实现屏蔽所有人机输出（Write-Host 调用都会落到这里），stdout 仅保留一份 JSON
if ($Json) { function Write-Host { } }

$ErrorActionPreference = 'Continue'
$script:HostBackendVersion = '2.0.0'
$script:WslDist = 'Ubuntu-22.04'

# ---- Python 串口桥（唯一串口方案，2026-10-22 起；usbipd 已停用）----
# WSL 仓库根（按现场修改；Windows 侧通过 \\wsl.localhost\<distro>\... 访问）
$script:WslRepo    = '/01-workfile-ai/01-zzt/ZZT_SELF'
$script:WslRepoUnc = "\\wsl.localhost\$($script:WslDist)" + ($script:WslRepo -replace '/', '\')
$script:BridgeWinScript = "$script:WslRepoUnc\tools\serial_bridge\serial_bridge.py"
$script:BridgeWinConfig = "$script:WslRepoUnc\config\serial_bridge.json"
$script:BridgeRuntimeDir = Join-Path $env:USERPROFILE '.serial-bridge'
$script:BridgePidFile    = Join-Path $script:BridgeRuntimeDir 'bridge.pid'
$script:BridgeLogFile    = Join-Path $script:BridgeRuntimeDir 'bridge.log'
$script:BridgePython     = $null
foreach ($candidate in @('pythonw.exe', 'python.exe', 'py.exe')) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $script:BridgePython = $cmd.Source; break }
}

# ---- 解析网关配置（REQS-0019）----
$script:ProjectRoot = 'D:\019-wy-tool\ZZT_SELF'
$script:BuildPlain  = Join-Path $script:ProjectRoot '.build_plain'
$script:PythonExe   = Join-Path $script:ProjectRoot '.venv\Scripts\python.exe'
$script:ParsePort   = 8700
$script:GatewayOut  = Join-Path $script:BuildPlain 'parse_svc.out.log'
$script:GatewayErr  = Join-Path $script:BuildPlain 'parse_svc.err.log'

# ---- DW31 诊断服务（计划任务 FC-Diag-Service, HTTP 8765）----
$script:DiagTask = 'FC-Diag-Service'
$script:DiagPort = 8765

function Get-ConsoleCodePage {
    # 将控制台切到 UTF-8，保证中文正常显示
    try { chcp 65001 | Out-Null } catch {}
    try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    try { $global:OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
    # 规避 cmd.exe 在 UNC 当前目录（\\wsl.localhost...）下无法启动的警告
    if ($PWD.ProviderPath -like '\\*') { Set-Location $env:USERPROFILE }
}

function Show-Banner {
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host "  Windows 宿主控制后端（串口桥 + 解析网关 + 诊断服务）" -ForegroundColor Cyan
    Write-Host "==============================================" -ForegroundColor Cyan
    Write-Host ""
}

function Test-Preconditions {
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

# ===================== 解析网关（REQS-0019）=====================

function Test-PortListening {
    param([int]$Port)
    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        return [bool]($listeners | Where-Object { $_.Port -eq $Port })
    } catch {
        return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    }
}

function Get-ParseProcess {
    # 返回占用解析网关端口(8700)的进程；未运行返回 $null
    if (-not (Test-PortListening -Port $script:ParsePort)) { return $null }
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
        $script:LastActionOk = $false
        $script:LastActionError = "未找到解析服务代码: $($script:BuildPlain)\apps\parser_service"
        return
    }
    if (-not (Test-Path $script:PythonExe)) {
        Write-Host "  [错误] 未找到 Python：$script:PythonExe" -ForegroundColor Red
        $script:LastActionOk = $false
        $script:LastActionError = "未找到 Python: $script:PythonExe"
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
        $script:LastActionOk = $false
        $script:LastActionError = "解析网关 20 秒内未就绪，请查看日志: $($script:GatewayErr)"
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
        $script:LastActionOk = $false
        $script:LastActionError = "端口 $($script:ParsePort) 停止后仍被占用，请手动结束进程"
    } else {
        Write-Host "  解析网关已停止（PID $($p.Id)）。" -ForegroundColor Green
    }
}

# ===================== Python 串口桥 =====================

function Invoke-WslBridgeCommand {
    param([string]$Arguments)
    $cmd = "cd $($script:WslRepo) && python3 tools/serial_bridge/serial_bridge.py $Arguments"
    return & wsl.exe -d $script:WslDist -u root -e bash -lc $cmd 2>&1
}

function Get-BridgeWslStatus {
    # 快路径：直接读 WSL 侧 supervisor 写的状态文件（UNC，毫秒级）
    try {
        $statusPath = "\\wsl.localhost\$($script:WslDist)\run\hplc\serial\status.json"
        if (Test-Path -LiteralPath $statusPath) {
            $raw = Get-Content -LiteralPath $statusPath -Raw -Encoding UTF8 -ErrorAction Stop
            if ($raw) { return ($raw | ConvertFrom-Json) }
        }
    } catch {}
    # 慢路径：wsl.exe 调 CLI（约 4s），仅当状态文件不可读时使用
    try {
        $out = (Invoke-WslBridgeCommand 'wsl status --config config/serial_bridge.json' | Out-String)
        $start = $out.IndexOf('{')
        if ($start -lt 0) { return $null }
        return ($out.Substring($start) | ConvertFrom-Json)
    } catch {
        return $null
    }
}

function Get-BridgeWindowsProcess {
    if (Test-Path $script:BridgePidFile) {
        $text = (Get-Content $script:BridgePidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($text -match '^\d+$') {
            $p = Get-Process -Id ([int]$text) -ErrorAction SilentlyContinue
            if ($p) {
                $ci = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.Id)" -ErrorAction SilentlyContinue
                if ($ci -and $ci.CommandLine -match 'serial_bridge\.py' -and $ci.CommandLine -match '\bwindows\b') {
                    return $p
                }
            }
        }
        Remove-Item $script:BridgePidFile -ErrorAction SilentlyContinue
    }
    $candidates = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessId -ne $PID -and
        $_.Name -notmatch '^(powershell|pwsh|cmd|conhost)\.exe$' -and
        $_.CommandLine -and
        $_.CommandLine -match 'serial_bridge\.py' -and
        $_.CommandLine -match '\bwindows\b'
    }
    if ($candidates) {
        $first = $candidates | Select-Object -First 1
        return (Get-Process -Id $first.ProcessId -ErrorAction SilentlyContinue)
    }
    return $null
}

function Show-BridgeStatus {
    $win = Get-BridgeWindowsProcess
    if ($win) {
        Write-Host "Python 串口桥 Windows 侧：运行中（PID $($win.Id)）" -ForegroundColor Green
    } else {
        Write-Host "Python 串口桥 Windows 侧：未运行（COM 空闲，可给 Windows 工具用）" -ForegroundColor Yellow
    }

    $st = Get-BridgeWslStatus
    if (-not $st -or -not $st.running -or -not $st.status) {
        Write-Host "Python 串口桥 WSL 侧：未运行" -ForegroundColor Yellow
        return
    }
    Write-Host "Python 串口桥 WSL 侧：运行中（PID $($st.status.pid)）" -ForegroundColor Green
    $ports = @($st.status.ports.PSObject.Properties | ForEach-Object { $_.Value })
    Write-Host ("  {0,-10} {1,-8} {2,-10} {3,-10} {4,-6} {5}" -f 'ID', 'COM', 'data', 'control', 'baud', 'PTY')
    foreach ($p in $ports) {
        $data = if ($p.client_connected) { 'connected' } else { 'DOWN' }
        $ctrl = if ($p.control_connected) { 'connected' } else { 'DOWN' }
        $color = if ($p.client_connected -and $p.control_connected) { 'Green' } elseif ($p.client_connected) { 'Yellow' } else { 'Gray' }
        Write-Host ("  {0,-10} {1,-8} {2,-10} {3,-10} {4,-6} {5}" -f $p.id, $p.windows_com, $data, $ctrl, $p.desired_baud, $p.link) -ForegroundColor $color
    }
    if (-not $win) {
        Write-Host "  提示：Windows 侧未运行，data/control 显示 DOWN；用 [6] 开关重新接管。" -ForegroundColor DarkGray
    }
}

function Start-PythonBridge {
    Write-Host "[Python 桥] 启动（WSL supervisor + Windows 桥）..." -ForegroundColor Green
    if (-not $script:BridgePython) {
        Write-Host "  [错误] 未找到 python.exe / pythonw.exe / py.exe，请先安装 Windows Python。" -ForegroundColor Red
        $script:LastActionOk = $false
        $script:LastActionError = "未找到 Windows Python (python.exe/pythonw.exe/py.exe)"
        return
    }

    Write-Host "  [1/2] 启动 WSL 侧 supervisor ..." -ForegroundColor Gray
    $wslOut = (Invoke-WslBridgeCommand 'wsl start --config config/serial_bridge.json' | Out-String)
    try {
        $startInfo = $wslOut.Substring($wslOut.IndexOf('{')) | ConvertFrom-Json
        if ($startInfo.already_running) {
            Write-Host "    WSL supervisor 已在运行（PID $($startInfo.status.pid)）" -ForegroundColor DarkGray
        } elseif ($startInfo.started) {
            Write-Host "    WSL supervisor 已启动（PID $($startInfo.pid)）" -ForegroundColor DarkGray
        } else {
            Write-Host "    WSL supervisor 启动失败：$($startInfo.error)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "    WSL supervisor 输出无法解析：" -ForegroundColor Yellow
        $wslOut -split "`r?`n" | Select-Object -First 5 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    }

    if (-not (Test-Path $script:BridgeWinScript)) {
        Write-Host "  [错误] 未找到桥脚本：$script:BridgeWinScript" -ForegroundColor Red
        Write-Host "  请确认 WSL 仓库路径 `$script:WslRepo` 与发行版名称是否正确。" -ForegroundColor Yellow
        $script:LastActionOk = $false
        $script:LastActionError = "未找到桥脚本: $script:BridgeWinScript"
        return
    }

    # 串口统一由本桥打开；若 COM 打不开，先看 host-info 的 coms 列表与桥日志，
    # 再用端口占用者视图确认是 fc-diag / 厂商工具在占口。

    $existing = Get-BridgeWindowsProcess
    if ($existing) {
        Write-Host "  Windows 桥已在运行（PID $($existing.Id)），跳过启动。" -ForegroundColor Yellow
    } else {
        New-Item -ItemType Directory -Force -Path $script:BridgeRuntimeDir | Out-Null
        $pyArgs = @()
        if ([IO.Path]::GetFileName($script:BridgePython) -ieq 'py.exe') { $pyArgs += '-3' }
        $pyArgs += @($script:BridgeWinScript, '--log', $script:BridgeLogFile, 'windows', '--config', $script:BridgeWinConfig)
        $proc = Start-Process -FilePath $script:BridgePython -ArgumentList $pyArgs `
            -WorkingDirectory $env:USERPROFILE -WindowStyle Hidden -PassThru
        $proc.Id | Set-Content -Encoding ascii $script:BridgePidFile
        Write-Host "  [2/2] Windows 桥 PID $($proc.Id)，等待 WSL 连接 ..." -ForegroundColor Gray
    }

    $presentComs = @([System.IO.Ports.SerialPort]::GetPortNames())
    $deadline = (Get-Date).AddSeconds(12)
    $ok = $false
    $missingComs = @()
    do {
        Start-Sleep -Milliseconds 500
        $st = Get-BridgeWslStatus
        if ($st -and $st.running -and $st.status -and $st.status.ports) {
            $ports = @($st.status.ports.PSObject.Properties | ForEach-Object { $_.Value })
            $enabled = @($ports | Where-Object { $_.enabled -ne $false })
            $missingComs = @($enabled | Where-Object { $presentComs -notcontains $_.windows_com } | ForEach-Object { $_.windows_com })
            $expected = @($enabled | Where-Object { $presentComs -contains $_.windows_com })
            $bad = @($expected | Where-Object { -not $_.client_connected -or -not $_.control_connected })
            if ($expected.Count -gt 0 -and $bad.Count -eq 0) { $ok = $true; break }
        }
    } while ((Get-Date) -lt $deadline)

    if ($missingComs.Count -gt 0) {
        Write-Host "  [提示] Windows 未枚举到：$($missingComs -join ', ')；对应口保持 DOWN。" -ForegroundColor Yellow
    }

    if ($ok) {
        $okCount = @($expected | Where-Object { $_.client_connected -and $_.control_connected }).Count
        $totalCount = @($enabled).Count
        Write-Host "  [成功] Python 串口桥已连通（$okCount/$totalCount 个已枚举口 data/control 双通道）。" -ForegroundColor Green
    } else {
        Write-Host "  [警告] 12 秒内未全部连通；查看状态或日志：$script:BridgeLogFile" -ForegroundColor Yellow
        $script:LastActionOk = $false
        $script:LastActionError = "串口桥 12 秒内未全部连通（data/control），查看状态或日志: $script:BridgeLogFile"
        if (Test-Path $script:BridgeLogFile) {
            Get-Content $script:BridgeLogFile -Tail 8 -Encoding UTF8 -ErrorAction SilentlyContinue |
                ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
        }
    }
    Show-BridgeStatus
}

function Stop-PythonBridge {
    Write-Host "[Python 桥] 停止 Windows 桥（释放 COM 给 Windows 工具）..." -ForegroundColor Green
    $proc = Get-BridgeWindowsProcess
    if (-not $proc) {
        Write-Host "  Windows 桥未在运行；COM 应该已经是空闲状态。" -ForegroundColor Yellow
    } else {
        $procId = $proc.Id
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        if (Get-Process -Id $procId -ErrorAction SilentlyContinue) {
            Write-Host "  [警告] PID $procId 仍在运行，请手动结束。" -ForegroundColor Yellow
            $script:LastActionOk = $false
            $script:LastActionError = "Windows 桥 PID $procId 停止失败，仍在运行"
        } else {
            Write-Host "  已停止 Windows 桥（PID $procId），COM 已释放。" -ForegroundColor Green
        }
    }
    Remove-Item $script:BridgePidFile -ErrorAction SilentlyContinue
    Show-BridgeStatus
    Write-Host "  说明：WSL supervisor 仍在运行；再按 [6] 即可重新接管。" -ForegroundColor DarkGray
}

function Toggle-PythonBridge {
    $win = Get-BridgeWindowsProcess
    if ($win) {
        Stop-PythonBridge
    } else {
        Start-PythonBridge
    }
}

function Restart-PythonBridge {
    Write-Host "[Python 桥] 重启 Windows 桥（PTY 软链保留，其余口约 5 秒内重连）..." -ForegroundColor Green
    Stop-PythonBridge
    Start-Sleep -Seconds 1
    Start-PythonBridge
}

# ---- DW31 诊断服务开关（计划任务 FC-Diag-Service, HTTP 8765）----

function Get-DiagHealth {
    try {
        return Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/health" -f $script:DiagPort) -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-DiagInstances {
    return @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine -like '*fc_service.py*'
    })
}

function Show-DiagServiceStatus {
    Write-Host "[诊断服务] 计划任务：$($script:DiagTask)" -ForegroundColor Cyan
    $q = schtasks /Query /TN $script:DiagTask /FO LIST 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  任务未安装：$q" -ForegroundColor Yellow
    } else {
        $q | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
    }
    $inst = Get-DiagInstances
    if ($inst.Count -gt 0) {
        Write-Host ("  进程：" + (($inst | ForEach-Object { "$($_.ProcessId)/$($_.Name)" }) -join ", ")) -ForegroundColor DarkGray
    }
    if ($inst.Count -gt 1) {
        Write-Host "  [警告] 检测到多个诊断服务实例，请用 [0] 重启清理" -ForegroundColor Yellow
    }
    $taskRunning = ($q -match 'Running|正在运行')
    if ((Get-DiagHealth) -and -not $taskRunning) {
        Write-Host "  [警告] 8765 有响应但任务未运行（残留实例），请用 [0] 重启清理" -ForegroundColor Yellow
    }
    $h = Get-DiagHealth
    if ($h) {
        Write-Host ("  8765 /health：OK（{0} {1}）" -f $h.service, $h.version) -ForegroundColor Green
        try {
            $st = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/api/state" -f $script:DiagPort) -TimeoutSec 2
            $portTxt = if ($st.port_open) { "已打开" } else { "未打开" }
            Write-Host ("  串口 {0}：{1}；登录={2}；模式={3}；Start.exe={4}" -f $st.port, $portTxt, $st.logged_in, $st.mode, $st.start_running) -ForegroundColor Gray
            if ($st.error) { Write-Host ("  error: " + $st.error) -ForegroundColor Yellow }
        } catch {}
    } else {
        Write-Host "  8765 /health：DOWN（服务未运行或端口未监听）" -ForegroundColor Yellow
    }
}

function Start-DiagService {
    if (Get-DiagHealth) {
        Write-Host "[诊断服务] 8765 已在运行，跳过启动" -ForegroundColor Green
        Show-DiagServiceStatus
        return
    }
    $left = Get-DiagInstances
    if ($left.Count -gt 0) {
        Write-Host "  [提示] 检测到残留实例，先清理 ..." -ForegroundColor Yellow
        foreach ($p in $left) {
            Write-Host ("    清理 PID " + $p.ProcessId + " (" + $p.Name + ")") -ForegroundColor DarkYellow
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "[诊断服务] 启动 $($script:DiagTask) ..." -ForegroundColor Green
    schtasks /Run /TN $script:DiagTask 2>&1 | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (Get-DiagHealth) {
            Write-Host "  [成功] 8765 已就绪" -ForegroundColor Green
            Show-DiagServiceStatus
            return
        }
    }
    Write-Host "  [警告] 30 秒内 8765 未就绪" -ForegroundColor Yellow
    $script:LastActionOk = $false
    $script:LastActionError = "诊断服务 30 秒内 8765 未就绪"
    Show-DiagServiceStatus
}

function Stop-DiagService {
    Write-Host "[诊断服务] 停止 $($script:DiagTask) ..." -ForegroundColor Green
    schtasks /End /TN $script:DiagTask 2>&1 | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor DarkGray }
    Start-Sleep -Seconds 1
    $left = Get-DiagInstances
    foreach ($p in $left) {
        Write-Host ("  清理残留 PID " + $p.ProcessId + " (" + $p.Name + ")") -ForegroundColor DarkYellow
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    for ($i = 0; $i -lt 10; $i++) {
        if (-not (Get-DiagHealth)) { break }
        Start-Sleep -Milliseconds 300
    }
    if (Get-DiagHealth) {
        Write-Host "  [警告] 8765 仍在响应" -ForegroundColor Yellow
        $script:LastActionOk = $false
        $script:LastActionError = "诊断服务停止后 8765 仍在响应"
    } else {
        Write-Host "  [成功] 8765 已停止" -ForegroundColor Green
    }
}

function Restart-DiagService {
    Stop-DiagService
    Start-Sleep -Seconds 1
    Start-DiagService
}

# ===================== JSON 输出（供 wsl-control HTTP 接口）=====================

function Get-GatewayBlock {
    $p = Get-ParseProcess
    $health = $null
    $healthErr = $null
    if ($p) {
        try {
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:$($script:ParsePort)/health" -TimeoutSec 3
            $health = @{ status = $r.status; dll_available = $r.dll_available }
        } catch {
            $healthErr = "$_"
        }
    }
    return @{
        port         = $script:ParsePort
        running      = [bool]$p
        pid          = $(if ($p) { $p.Id } else { $null })
        health       = $health
        health_error = $healthErr
    }
}

function Get-BridgeConfigBlock {
    $err = $null
    $ports = @()
    try {
        $raw = Get-Content -LiteralPath $script:BridgeWinConfig -Raw -Encoding UTF8 -ErrorAction Stop
        $cfg = $raw | ConvertFrom-Json
        $ports = @($cfg.ports | ForEach-Object {
            @{
                id          = $_.id
                windows_com = $_.windows_com
                label       = $_.label
                usage       = $_.usage
                enabled     = ($_.enabled -ne $false)
                baudrate    = $_.baudrate
                parity      = $_.parity
            }
        })
    } catch {
        $err = "$_"
    }
    return @{ path = $script:BridgeWinConfig; ports = $ports; error = $err }
}

function Get-BridgeBlock {
    $win = Get-BridgeWindowsProcess
    $st = Get-BridgeWslStatus
    if ($st -and $st.running -and $st.status) {
        $ports = @($st.status.ports.PSObject.Properties | ForEach-Object {
            $p = $_.Value
            @{
                id                = $p.id
                windows_com       = $p.windows_com
                client_connected  = [bool]$p.client_connected
                control_connected = [bool]$p.control_connected
                desired_baud      = $p.desired_baud
                link              = $p.link
                enabled           = ($p.enabled -ne $false)
            }
        })
        $wslBlock = @{ running = $true; pid = $st.status.pid; ports = $ports; error = $null }
    } else {
        $err = $null
        if (-not $st) { $err = 'WSL supervisor status 不可达或输出无法解析' }
        $wslBlock = @{ running = $false; pid = $null; ports = @(); error = $err }
    }
    return @{
        windows = @{ running = [bool]$win; pid = $(if ($win) { $win.Id } else { $null }) }
        config  = Get-BridgeConfigBlock
        wsl     = $wslBlock
    }
}

function Get-DiagBlock {
    $taskInstalled = $true
    $taskState = $null
    $q = schtasks /Query /TN $script:DiagTask /FO LIST 2>&1
    if ($LASTEXITCODE -ne 0) {
        $taskInstalled = $false
    } elseif ($q -match 'Running|正在运行') {
        $taskState = 'Running'
    } else {
        $taskState = 'Stopped'
    }
    $inst = @(Get-DiagInstances)
    $warnings = @()
    if ($inst.Count -gt 1) { $warnings += '检测到多个诊断服务实例，建议重启清理' }
    $h = Get-DiagHealth
    if ($h -and $taskInstalled -and $taskState -ne 'Running') {
        $warnings += '8765 有响应但计划任务未运行（残留实例），建议重启清理'
    }
    $state = $null
    if ($h) {
        try {
            $st = Invoke-RestMethod -Uri ("http://127.0.0.1:{0}/api/state" -f $script:DiagPort) -TimeoutSec 2
            $state = @{
                port         = $st.port
                port_open    = $st.port_open
                logged_in    = $st.logged_in
                mode         = $st.mode
                start_running = $st.start_running
                error        = $st.error
            }
        } catch {}
    }
    return @{
        task           = $script:DiagTask
        task_installed = $taskInstalled
        task_state     = $taskState
        instances      = @($inst | ForEach-Object { @{ pid = $_.ProcessId; name = $_.Name } })
        health_ok      = [bool]$h
        health         = $(if ($h) { @{ service = $h.service; version = $h.version } } else { $null })
        state          = $state
        warnings       = $warnings
    }
}

function Get-HostBlock {
    $py = $null
    foreach ($c in @('pythonw.exe', 'python.exe', 'py.exe')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source; break }
    }
    $coms = @()
    try { $coms = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object) } catch {}
    $wbOk = $false
    $wbCode = $null
    try {
        $r = Invoke-WebRequest -Uri 'http://127.0.0.1:8790/api/health' -TimeoutSec 1 -UseBasicParsing -ErrorAction Stop
        $wbCode = "$($r.StatusCode)"
        $wbOk = ($r.StatusCode -eq 200)
    } catch {
        $wbCode = 'unreachable'
    }
    return @{
        backend_version = $script:HostBackendVersion
        computer        = $env:COMPUTERNAME
        user            = $env:USERNAME
        python          = @{ path = $py }
        coms            = $coms
        wsl             = @{ distro = $script:WslDist; repo = $script:WslRepo }
        workbench       = @{ reachable = $wbOk; http_code = $wbCode; url = 'http://127.0.0.1:8790/api/health' }
        paths           = @{
            bridge_script   = $script:BridgeWinScript
            bridge_config   = $script:BridgeWinConfig
            bridge_log      = $script:BridgeLogFile
            gateway_log_dir = $script:BuildPlain
        }
    }
}

function Get-AggregateStatus {
    return @{
        host    = Get-HostBlock
        bridge  = Get-BridgeBlock
        gateway = Get-GatewayBlock
        diag    = Get-DiagBlock
    }
}

function Show-HostInfo {
    Write-Host "[宿主] $env:COMPUTERNAME / $env:USERNAME   后端 $script:HostBackendVersion" -ForegroundColor Cyan
    $py = $null
    foreach ($c in @('pythonw.exe', 'python.exe', 'py.exe')) {
        $cmd = Get-Command $c -ErrorAction SilentlyContinue
        if ($cmd) { $py = $cmd.Source; break }
    }
    Write-Host ("  Python: " + $(if ($py) { $py } else { '未找到' })) -ForegroundColor DarkGray
    $coms = @()
    try { $coms = @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object) } catch {}
    Write-Host ("  系统串口: " + $(if ($coms.Count) { ($coms -join ', ') } else { '无' })) -ForegroundColor DarkGray
    Write-Host "  桥配置: $script:BridgeWinConfig" -ForegroundColor DarkGray
    Write-Host "  桥日志: $script:BridgeLogFile" -ForegroundColor DarkGray
}

# ---------- 主流程 ----------
Get-ConsoleCodePage
Show-Banner

if (-not (Test-Preconditions)) {
    if ($Json) {
        @{ ok = $false; action = $Action; error = '前置检查失败：未找到 WSL（wsl.exe）'; data = $null } |
            ConvertTo-Json -Depth 8
        exit 1
    }
    Read-Host "按回车退出"
    exit 1
}

# 非交互动作（供自动化/脚本调用）
if ($Action -or $Json) {
    $script:LastActionOk = $true
    $script:LastActionError = $null
    if (-not $Action) { $Action = 'status' }   # 仅 -Json 时视同查询聚合状态

    if ($Json) {
        # JSON 模式：变更动作需要执行；纯查询动作跳过文本执行（避免重复探测），直接取数
        if (@('status','bridge-status','svc-status','host-info') -notcontains $Action) {
            switch ($Action) {
                'start-gateway'  { Start-ParseGateway }
                'stop-gateway'   { Stop-ParseGateway }
                'bridge-start'   { Start-PythonBridge }
                'bridge-stop'    { Stop-PythonBridge }
                'bridge-toggle'  { Toggle-PythonBridge }
                'bridge-restart' { Restart-PythonBridge }
                'svc-start'      { Start-DiagService }
                'svc-stop'       { Stop-DiagService }
                'svc-restart'    { Restart-DiagService }
            }
        }
        $data = switch ($Action) {
            'status'         { Get-AggregateStatus }
            'host-info'      { Get-HostBlock }
            'start-gateway'  { Get-GatewayBlock }
            'stop-gateway'   { Get-GatewayBlock }
            'bridge-start'   { Get-BridgeBlock }
            'bridge-stop'    { Get-BridgeBlock }
            'bridge-toggle'  { Get-BridgeBlock }
            'bridge-restart' { Get-BridgeBlock }
            'bridge-status'  { Get-BridgeBlock }
            'svc-start'      { Get-DiagBlock }
            'svc-stop'       { Get-DiagBlock }
            'svc-restart'    { Get-DiagBlock }
            'svc-status'     { Get-DiagBlock }
        }
        @{ ok = $script:LastActionOk; action = $Action; error = $script:LastActionError; data = $data } |
            ConvertTo-Json -Depth 8
        if ($script:LastActionOk) { exit 0 } else { exit 1 }
    }

    switch ($Action) {
        'status'         { Show-HostInfo; Show-GatewayStatus; Show-BridgeStatus; Show-DiagServiceStatus }
        'host-info'      { Show-HostInfo }
        'start-gateway'  { Start-ParseGateway }
        'stop-gateway'   { Stop-ParseGateway }
        'bridge-start'   { Start-PythonBridge }
        'bridge-stop'    { Stop-PythonBridge }
        'bridge-toggle'  { Toggle-PythonBridge }
        'bridge-restart' { Restart-PythonBridge }
        'bridge-status'  { Show-BridgeStatus }
        'svc-start'      { Start-DiagService }
        'svc-stop'       { Stop-DiagService }
        'svc-restart'    { Restart-DiagService }
        'svc-status'     { Show-DiagServiceStatus }
    }
    exit 0
}

if ($Test) {
    Show-HostInfo
    Show-GatewayStatus
    Show-BridgeStatus
    Show-DiagServiceStatus
    exit 0
}

# 交互菜单
$running = $true
while ($running) {
    Write-Host ""
    Write-Host "请选择操作：" -ForegroundColor White
    Write-Host "  [1] 查看宿主状态（串口 + 串口桥 + 解析网关 + 诊断服务）"
    Write-Host "  [2] 启动串口桥（Windows 桥接管 COM）"
    Write-Host "  [3] 停止串口桥（释放 COM 给 Windows 工具）"
    Write-Host "  [4] 重启串口桥"
    Write-Host "  [5] 启动解析网关（Windows 侧，端口 8700）"
    Write-Host "  [6] 停止解析网关"
    Write-Host "  [7] 启动诊断服务（FC-Diag-Service, 8765）"
    Write-Host "  [8] 停止诊断服务"
    Write-Host "  [9] 重启诊断服务"
    Write-Host "  [0] 查看诊断服务状态"
    Write-Host "  [Q] 退出"
    Write-Host ""
    $choice = Read-Host "请输入选项"

    switch ($choice.Trim().ToUpper()) {
        '1' { Show-HostInfo; Show-GatewayStatus; Show-BridgeStatus; Show-DiagServiceStatus; break }
        '2' { Start-PythonBridge; break }
        '3' { Stop-PythonBridge; break }
        '4' { Restart-PythonBridge; break }
        '5' { Start-ParseGateway; break }
        '6' { Stop-ParseGateway; break }
        '7' { Start-DiagService; break }
        '8' { Stop-DiagService; break }
        '9' { Restart-DiagService; break }
        '0' { Show-DiagServiceStatus; break }
        'Q' { $running = $false; break }
        default { Write-Host "无效选项，请重试。" -ForegroundColor Yellow }
    }
}
Write-Host "再见！" -ForegroundColor Cyan
