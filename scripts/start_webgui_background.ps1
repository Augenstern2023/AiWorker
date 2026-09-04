[CmdletBinding()]
param(
    [switch]$Describe
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logPath = Join-Path $projectRoot "logs\webgui-autostart.log"
$commonModulePath = Join-Path $PSScriptRoot "webgui_autostart_common.psm1"
$hostAddress = "0.0.0.0"
$probeAddress = "127.0.0.1"
$port = 8899

$description = [ordered]@{
    project_root = $projectRoot
    python = $pythonPath
    module = "WebGUI:app"
    host = $hostAddress
    port = $port
    log = $logPath
}

if ($Describe) {
    $description | ConvertTo-Json -Compress
    exit 0
}

$logDirectory = Split-Path -Parent $logPath
New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

function Write-AutostartLog {
    param([string]$Message)

    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $logPath -Value "[$timestamp] $Message" -Encoding UTF8
}

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    Write-AutostartLog "Virtual environment Python not found: $pythonPath"
    exit 1
}
if (-not (Test-Path -LiteralPath $commonModulePath -PathType Leaf)) {
    Write-AutostartLog "Autostart support module not found: $commonModulePath"
    exit 1
}

$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://${probeAddress}:$port/" -TimeoutSec 5
        if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
            Write-AutostartLog "WebGUI is already listening on ${hostAddress}:$port; skipping duplicate start."
            exit 0
        }
    }
    catch {
        Write-AutostartLog "Port $port is occupied but WebGUI did not respond: $($_.Exception.Message)"
        exit 1
    }
}

Set-Location -LiteralPath $projectRoot
Write-AutostartLog "Starting WebGUI on http://${hostAddress}:$port"

try {
    Import-Module $commonModulePath -Force
    $uvicornArguments = @("-m", "uvicorn", "WebGUI:app", "--host", $hostAddress, "--port", "$port")
    $processExitCode = Invoke-LoggedProcess `
        -FilePath $pythonPath `
        -ArgumentList $uvicornArguments `
        -LogPath $logPath
    Write-AutostartLog "WebGUI exited with code $processExitCode"
    exit $processExitCode
}
catch {
    Write-AutostartLog "WebGUI failed to start: $($_.Exception.Message)"
    exit 1
}
