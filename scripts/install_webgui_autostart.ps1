[CmdletBinding()]
param(
    [switch]$Describe
)

$ErrorActionPreference = "Stop"
$taskName = "AiWorker WebGUI"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$launcherPath = Join-Path $PSScriptRoot "start_webgui_background.ps1"
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$delaySeconds = 15
$restartCount = 3
$restartIntervalMinutes = 1

$description = [ordered]@{
    task_name = $taskName
    trigger = "logon"
    delay_seconds = $delaySeconds
    restart_count = $restartCount
    restart_interval_minutes = $restartIntervalMinutes
    multiple_instances = "IgnoreNew"
    run_level = "Limited"
    launcher = $launcherPath
}

if ($Describe) {
    $description | ConvertTo-Json -Compress
    exit 0
}

if (-not (Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
    throw "WebGUI background launcher not found: $launcherPath"
}
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Virtual environment Python not found: $pythonPath. Run 'uv sync' first."
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$powershellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcherPath`""

$action = New-ScheduledTaskAction `
    -Execute $powershellPath `
    -Argument $arguments `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$trigger.Delay = "PT${delaySeconds}S"
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances IgnoreNew `
    -RestartCount $restartCount `
    -RestartInterval (New-TimeSpan -Minutes $restartIntervalMinutes) `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -Hidden

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Start AiWorker WebGUI silently after user logon." `
    -Force | Out-Null

Write-Output "Registered scheduled task '$taskName' for $currentUser."
