[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logPath = Join-Path $projectRoot "logs\rank-zentao-push.log"

New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null
Set-Location -LiteralPath $projectRoot
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "[$timestamp] start" -Encoding UTF8

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Add-Content -LiteralPath $logPath -Value "Python not found: $pythonPath" -Encoding UTF8
    exit 1
}

$previousErrorActionPreference = $ErrorActionPreference
try {
    $ErrorActionPreference = "Continue"
    & $pythonPath -m scripts.push_latest_rank_updates 2>&1 | ForEach-Object {
        Add-Content -LiteralPath $logPath -Value $_.ToString() -Encoding UTF8
    }
    $exitCode = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $previousErrorActionPreference
}
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Add-Content -LiteralPath $logPath -Value "[$timestamp] exit=$exitCode" -Encoding UTF8
exit $exitCode
