# One-click AI admin key generator + installer
# Generates "WK<timestamp><random>" and writes it to the USER environment variable
# WORKBENCH_AI_ADMIN_KEY so the workbench picks it up without manual copying.
#
# Usage: double-click 一键生成AI密钥.bat (this script is called by it), or:
#   powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gen_ai_key.ps1"

$ErrorActionPreference = "Stop"

$varName = "WORKBENCH_AI_ADMIN_KEY"
$prefix = "WK"
$stamp  = Get-Date -Format "yyyyMMddHHmmss"
$rand   = [int](Get-Random -Maximum 99999999)
$key    = "{0}{1}{2:D8}" -f $prefix, $stamp, $rand

# Persist at the user level (permanent for this Windows account).
[System.Environment]::SetEnvironmentVariable($varName, $key, "User")
# Also set it for the current process so it applies immediately if workbench is
# launched from this same cmd window afterwards.
[System.Environment]::SetEnvironmentVariable($varName, $key, "Process")

Write-Output ""
Write-Output "[OK] AI control-plane admin key generated and installed:"
Write-Output ("     WORKBENCH_AI_ADMIN_KEY = " + $key)
Write-Output ""
Write-Output "Saved to your Windows USER environment variable (permanent)."
Write-Output "Restart the workbench (close 8790, run 启动工作台.bat again) to apply."
Write-Output ""
