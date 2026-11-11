# SPDX-License-Identifier: GPL-3.0-only
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BackendRoot,
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$root = (Resolve-Path -LiteralPath $BackendRoot).Path
$backend = Join-Path $root 'backend'
if (-not (Test-Path -LiteralPath (Join-Path $backend 'desktop_standalone.py') -PathType Leaf)) {
    throw 'BackendRoot must contain backend/desktop_standalone.py.'
}
$before = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'original-manifest.json') -Raw | ConvertFrom-Json)
$after = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deployed-manifest.json') -Raw | ConvertFrom-Json)
$existing = @($before | ForEach-Object { $_.file })
function Matches($base, $item) {
    $path = Join-Path $base $item.file
    return ((Test-Path -LiteralPath $path -PathType Leaf) -and
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $item.sha256)
}
foreach ($item in $after) {
    if ($item.file -notin $existing -and -not (Matches (Join-Path $PSScriptRoot 'backend') $item)) {
        throw 'Bundle payload hash mismatch.'
    }
}
if (@($after | Where-Object { -not (Matches $backend $_) }).Count -eq 0) {
    Write-Output 'ALREADY_DEPLOYED: all 23 files match the verified deployment.'
    return
}
foreach ($item in $before) {
    if (-not (Matches $backend $item)) { throw 'Backend baseline differs; refusing to overwrite unknown edits.' }
}
foreach ($name in @('full_playback.py', 'playback_recovery')) {
    if (Test-Path -LiteralPath (Join-Path $backend $name)) { throw 'New payload path already exists; inspect manually.' }
}
if ($Check) {
    Write-Output 'BASELINE_MATCHED: stop the backend and its children before installing.'
    return
}
$listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
if (@($listeners | Where-Object { $_.Port -eq 11800 }).Count) {
    throw 'Port 11800 is occupied. Stop the original backend and its children manually; no service was stopped.'
}
# Prepare and verify the complete result before touching the installation.
$backup = Join-Path $root ('data/deployment-backups/full-playback-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
$prepared = Join-Path $backup 'prepared'
foreach ($item in $before) {
    foreach ($folder in @('original', 'prepared')) {
        $target = Join-Path (Join-Path $backup $folder) $item.file
        New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
        Copy-Item -LiteralPath (Join-Path $backend $item.file) -Destination $target
    }
}
& git -c core.autocrlf=false -C $prepared apply --check -- (Join-Path $PSScriptRoot 'full-playback.patch')
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed; installation unchanged.' }
& git -c core.autocrlf=false -C $prepared apply -- (Join-Path $PSScriptRoot 'full-playback.patch')
if ($LASTEXITCODE -ne 0) { throw 'Patch preparation failed; installation unchanged.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'backend/full_playback.py') -Destination $prepared
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'backend/playback_recovery') -Destination $prepared -Recurse
foreach ($item in $after) {
    if (-not (Matches $prepared $item)) { throw 'Prepared deployment hash mismatch; installation unchanged.' }
}
foreach ($name in @('original-manifest.json', 'deployed-manifest.json', 'rollback.ps1')) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot $name) -Destination $backup
}
# Recheck the baseline after preparation; do not overwrite concurrent changes.
foreach ($item in $before) {
    if (-not (Matches $backend $item)) { throw 'Backend changed during preparation; installation unchanged.' }
}
foreach ($item in $after) {
    if ($item.file -notin $existing -and (Test-Path -LiteralPath (Join-Path $backend $item.file))) {
        throw 'Payload path appeared during preparation; installation unchanged.'
    }
}
foreach ($item in $after) {
    $target = Join-Path $backend $item.file
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $prepared $item.file) -Destination $target
    if (-not (Matches $backend $item)) { throw "Deployment incomplete. Keep the backend stopped; inspect backup: $backup" }
}
Write-Output "DEPLOYED: 23 files verified. Backup: $backup"
Write-Output 'Restart using the original command/environment, then verify authenticated /stream and full decoding.'
