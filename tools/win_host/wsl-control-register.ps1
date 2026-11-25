#Requires -Version 5.1
<#
    wsl-control-register.ps1 - 一次性注册（需管理员）：
      1) 计划任务 FC-Wsl-Control：登录自启、最高权限（usbipd/schtasks 不再弹 UAC）、
         失败 1 分钟后自动重启 x3、无执行时限（防默认 3 天被杀）
      2) 防火墙放行 TCP 8766（限 WSL2 NAT 网段 172.16.0.0/12）
      3) 立即启动并验证 /health
    幂等：重复执行覆盖旧任务与旧规则。
#>
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$servicePy = Join-Path $dir 'wsl-control.py'
$taskName = 'FC-Wsl-Control'
$port = 8766

if (-not (Test-Path $servicePy)) {
    Write-Host "[错误] 未找到 $servicePy" -ForegroundColor Red
    exit 1
}

# 找 Python：优先 pythonw.exe（无窗口）；只有 python.exe 时从同目录找 pythonw.exe
$py = $null
foreach ($c in @('pythonw.exe', 'python.exe')) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) {
    Write-Host "[错误] 未找到 Windows Python（pythonw.exe / python.exe）" -ForegroundColor Red
    exit 1
}
if ((Split-Path -Leaf $py) -ieq 'python.exe') {
    $w = Join-Path (Split-Path -Parent $py) 'pythonw.exe'
    if (Test-Path $w) { $py = $w }
}
Write-Host "Python: $py"
Write-Host "Service: $servicePy (port $port)"

# ---- [1/3] 计划任务 ----
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$servicePy`" $port" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "[1/3] 计划任务 $taskName 已注册（登录自启、最高权限、失败 1 分钟后自动重启）"

# ---- [2/3] 防火墙放行（限 WSL 网段）----
$ruleName = 'FC-Wsl-Control HTTP 8766 (WSL)'
netsh advfirewall firewall delete rule name="$ruleName" | Out-Null
netsh advfirewall firewall add rule name="$ruleName" dir=in action=allow `
    protocol=TCP localport=$port remoteip=172.16.0.0/12 | Out-Null
Write-Host "[2/3] 防火墙规则已添加（TCP $port，限 WSL 网段 172.16.0.0/12）"

# ---- [3/3] 启动并验证 ----
schtasks /Run /TN $taskName | Out-Null
$ok = $false
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $r = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($r.status -eq 'ok') { $ok = $true; break }
    } catch {}
}
if ($ok) {
    Write-Host "[3/3] 服务已启动：http://127.0.0.1:$port/health" -ForegroundColor Green
    Write-Host "      WSL 侧访问：http://<宿主机IP>:$port（WSL 内 'ip route show default' 取网关 IP）" -ForegroundColor DarkGray
} else {
    Write-Host "[3/3] [警告] 服务未就绪，查看日志：$env:USERPROFILE\.wsl-control\service.log" -ForegroundColor Yellow
}
