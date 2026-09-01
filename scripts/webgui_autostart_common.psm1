function Invoke-LoggedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,

        [Parameter(Mandatory = $true)]
        [string]$LogPath
    )

    $logDirectory = Split-Path -Parent $LogPath
    if ($logDirectory) {
        New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    }

    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Native programs commonly use stderr for ordinary logs. Consume both
        # streams without promoting stderr lines to terminating PowerShell errors.
        $ErrorActionPreference = "Continue"
        & $FilePath @ArgumentList 2>&1 | ForEach-Object {
            Add-Content -LiteralPath $LogPath -Value $_.ToString() -Encoding UTF8
        }
        $processExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }

    return [int]$processExitCode
}

Export-ModuleMember -Function Invoke-LoggedProcess
