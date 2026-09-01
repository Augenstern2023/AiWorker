[CmdletBinding()]
param(
    [switch]$Describe
)

$ErrorActionPreference = "Stop"
$taskName = "AiWorker WebGUI"

if ($Describe) {
    [ordered]@{
        task_name = $taskName
        operation = "unregister"
    } | ConvertTo-Json -Compress
    exit 0
}

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Output "Scheduled task '$taskName' is not installed."
    exit 0
}

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
Write-Output "Removed scheduled task '$taskName'."
