# SPDX-License-Identifier: GPL-3.0-only
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$OriginalFiles,
    [string]$Python = 'python',
    [string]$Node = 'node',
    [string]$Java = 'java'
)
$ErrorActionPreference = 'Stop'
$before = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'original-manifest.json') -Raw | ConvertFrom-Json)
$after = @(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deployed-manifest.json') -Raw | ConvertFrom-Json)
$temp = Join-Path ([IO.Path]::GetTempPath()) ('hongguo-bundle-test-' + [guid]::NewGuid().ToString('N'))
# No automatic deletion: leave the uniquely named test evidence for inspection.
foreach ($item in $before) {
    $source = Join-Path $OriginalFiles $item.file
    if ((Get-FileHash -LiteralPath $source).Hash -ne $item.sha256) { throw 'Original fixture does not match baseline.' }
    $target = Join-Path $temp $item.file
    New-Item -ItemType Directory -Path (Split-Path $target -Parent) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $target
}
& git -c core.autocrlf=false -C $temp apply --check -- (Join-Path $PSScriptRoot 'full-playback.patch')
if ($LASTEXITCODE -ne 0) { throw 'Patch check failed.' }
& git -c core.autocrlf=false -C $temp apply -- (Join-Path $PSScriptRoot 'full-playback.patch')
if ($LASTEXITCODE -ne 0) { throw 'Patch application failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'backend/full_playback.py') -Destination $temp
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'backend/playback_recovery') -Destination $temp -Recurse
foreach ($item in $after) {
    if ((Get-FileHash -LiteralPath (Join-Path $temp $item.file)).Hash -ne $item.sha256) {
        throw 'Reconstructed result differs from the deployed version.'
    }
}
$checks = @'
import ast, importlib.util, sys
from pathlib import Path
from types import SimpleNamespace
sys.dont_write_bytecode = True
root = Path(sys.argv[1])
for path in root.rglob('*.py'):
    ast.parse(path.read_text(encoding='utf-8'))
spec = importlib.util.spec_from_file_location('bundle_playback', root/'full_playback.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
class Media:
    duration = 183600000
    streams = SimpleNamespace(video=[1], audio=[1])
    def __enter__(self): return self
    def __exit__(self, *args): pass
sys.modules['av'] = SimpleNamespace(open=lambda _: Media(), time_base=1000000)
assert module.verify_media('synthetic.mp4', 183.6) == 183.6
for duration, audio in [(10, [1]), (183.6, []), (None, [1])]:
    Media.streams.audio = audio
    try: module.verify_media('synthetic.mp4', duration)
    except module.PlaybackError as e: assert str(e) == 'FULL_EPISODE_VALIDATION_FAILED'
    else: raise AssertionError('Invalid full episode accepted')
for filename, name in [('sixgod.py','video_model'), ('offline_dl.py','_video_model'), ('server.py','_ensure_decrypted')]:
    tree = ast.parse((root/filename).read_text(encoding='utf-8'))
    function = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name == name)
    body = ast.unparse(function)
    assert 'mirror_track' not in body and '_post_model' not in body and '_legacy_video_model' not in body
assert 'full-playback-v1' in (root/'server.py').read_text(encoding='utf-8')
print('PASS: reconstructed 23 hashes; Python syntax; preview/legacy exclusion; synthetic duration/audio validation')
'@
& $Python -c $checks $temp
if ($LASTEXITCODE -ne 0) { throw 'Python regression failed.' }
$previousJava = $env:HONGGUO_PLAYBACK_JAVA
try {
    $env:HONGGUO_PLAYBACK_JAVA = (Get-Command $Java -ErrorAction Stop).Source
    & $Node (Join-Path $PSScriptRoot 'backend/playback_recovery/worker.test.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Node regression failed.' }
} finally {
    $env:HONGGUO_PLAYBACK_JAVA = $previousJava
}
& $Java -cp ((Join-Path $PSScriptRoot 'backend/playback_recovery/playback.jar') + [IO.Path]::PathSeparator + (Join-Path $PSScriptRoot 'backend/playback_recovery/lib/*')) com.hongguotv.core.PlaybackCliKt --selftest
if ($LASTEXITCODE -ne 0) { throw 'Java self-test failed.' }
Write-Output "PASS: offline bundle regression. Evidence: $temp"
