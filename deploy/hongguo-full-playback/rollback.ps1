# SPDX-License-Identifier: GPL-3.0-only
[CmdletBinding()]
param([Parameter(Mandatory = $true)][string]$BackendRoot)
$ErrorActionPreference = 'Stop'
$backend = Join-Path (Resolve-Path -LiteralPath $BackendRoot).Path 'backend'
$listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
if (@($listeners | Where-Object { $_.Port -eq 11800 }).Count) { throw 'Stop the backend and its children first.' }
$before = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'original-manifest.json') -Raw | ConvertFrom-Json)
$after = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deployed-manifest.json') -Raw | ConvertFrom-Json)
foreach ($item in $before) {
    $copy = Join-Path (Join-Path $PSScriptRoot 'original') $item.file
    if ((Get-FileHash -LiteralPath $copy).Hash -ne $item.sha256) { throw 'Original backup hash mismatch.' }
    $current = (Get-FileHash -LiteralPath (Join-Path $backend $item.file)).Hash
    $deployed = @($after | Where-Object { $_.file -eq $item.file })[0].sha256
    if ($current -ne $deployed -and $current -ne $item.sha256) { throw 'Backend changed since deployment; refusing overwrite.' }
}
foreach ($item in $before) {
    Copy-Item -LiteralPath (Join-Path (Join-Path $PSScriptRoot 'original') $item.file) -Destination (Join-Path $backend $item.file)
    if ((Get-FileHash -LiteralPath (Join-Path $backend $item.file)).Hash -ne $item.sha256) { throw 'Restore hash mismatch.' }
}
Write-Output 'RESTORED: original five files. New, now-unused payload files and caches are deliberately retained. No service started.'
