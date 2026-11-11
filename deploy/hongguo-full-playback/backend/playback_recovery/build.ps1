param([Parameter(Mandatory=$true)][string]$CompilerHome,
      [string]$Java = 'java')
$ErrorActionPreference='Stop'
$compiler = @('compiler-curl.jar','kotlin-stdlib-2.2.20.jar','kotlin-script-runtime-2.2.20.jar','kotlin-reflect-1.6.10.jar','kotlin-daemon-embeddable-2.2.20.jar','kotlinx-coroutines-core-jvm-1.8.0.jar','annotations-13.0.jar') | ForEach-Object {Join-Path $CompilerHome $_}
foreach($file in $compiler) {if(!(Test-Path -LiteralPath $file)){throw 'Missing Kotlin 2.2.20 compiler dependencies'}}
$runtime = @((Join-Path $PSScriptRoot 'lib/kotlin-stdlib-2.2.20.jar'),(Join-Path $PSScriptRoot 'lib/bcprov-jdk18on-1.79.jar'),(Join-Path $CompilerHome 'annotations-13.0.jar'))
$sources = @(Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'src') -Filter '*.kt' | ForEach-Object {$_.FullName})
& $Java -cp ($compiler -join [IO.Path]::PathSeparator) org.jetbrains.kotlin.cli.jvm.K2JVMCompiler -no-stdlib -no-reflect -classpath ($runtime -join [IO.Path]::PathSeparator) -jvm-target 17 -d (Join-Path $PSScriptRoot 'playback.jar') @sources
if($LASTEXITCODE -ne 0){throw 'Kotlin compilation failed'}
& $Java -cp ((@((Join-Path $PSScriptRoot 'playback.jar'))+$runtime) -join [IO.Path]::PathSeparator) com.hongguotv.core.PlaybackCliKt --selftest
if($LASTEXITCODE -ne 0){throw 'Signer self-test failed'}
