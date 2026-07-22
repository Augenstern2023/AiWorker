[CmdletBinding()]
param(
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repo = 'D:\Crawler\AiWorker'
$git = 'C:\Users\Administrator\AppData\Local\Programs\Git\cmd\git.exe'
$remote = 'origin'
$branch = 'main'
$lockName = 'Global\AiWorker-Daily-Git-Update'
$logDir = Join-Path $repo 'logs\scheduled-git-update'
$logFile = Join-Path $logDir ((Get-Date -Format 'yyyy-MM-dd') + '.log')

New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Write-TaskLog {
    param([string]$Message)
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Invoke-Git {
    param([string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    try {
        # PowerShell 5.1 converts native stderr (including harmless Git warnings) to ErrorRecord.
        # Keep collecting it for the log without letting $ErrorActionPreference='Stop' abort the script.
        $ErrorActionPreference = 'Continue'
        $output = & $git -C $repo @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($output) {
        foreach ($line in $output) {
            Write-TaskLog ("git {0}: {1}" -f ($Arguments -join ' '), $line)
        }
    }
    if ($exitCode -ne 0) {
        throw "git $($Arguments -join ' ') failed with exit code $exitCode"
    }
    return $output
}

try {
    $mutex = New-Object System.Threading.Mutex($false, $lockName)
    if (-not $mutex.WaitOne(0)) {
        Write-TaskLog 'SKIP another update process is already running'
        exit 0
    }
    Write-TaskLog 'BEGIN daily repository update'

    if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
        throw "Git repository not found: $repo"
    }
    if (-not (Test-Path -LiteralPath $git)) {
        throw "git.exe not found: $git"
    }

    $currentBranch = (& $git -C $repo branch --show-current 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to determine current branch'
    }
    if ($currentBranch -ne $branch) {
        throw "Expected branch '$branch', found '$currentBranch'; aborting to avoid updating the wrong branch"
    }

    $status = (& $git -C $repo status --porcelain=v1 --untracked-files=all 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to read repository status'
    }
    if (-not $status) {
        Write-TaskLog 'SKIP working tree has no changes'
        exit 0
    }

    if ($DryRun) {
        Write-TaskLog 'DRY-RUN changes detected; commit and push were not executed'
        Write-TaskLog $status
        exit 0
    }

    $stagedBeforeRun = (& $git -C $repo diff --cached --name-only 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw 'Unable to inspect the staged index'
    }
    if ($stagedBeforeRun) {
        throw 'Repository already contains staged changes; aborting so the task does not commit a partial/manual staging selection'
    }

    Invoke-Git @('add', '--all') | Out-Null

    $staged = (& $git -C $repo diff --cached --quiet 2>&1)
    $diffExit = $LASTEXITCODE
    if ($diffExit -eq 0) {
        Write-TaskLog 'SKIP no staged changes after git add --all'
        exit 0
    }
    if ($diffExit -ne 1) {
        throw "git diff --cached --quiet failed with exit code $diffExit"
    }

    $commitMessage = 'chore(auto-update): daily sync {0}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    Invoke-Git @('commit', '-m', $commitMessage) | Out-Null

    try {
        Invoke-Git @('fetch', $remote, $branch) | Out-Null
    } catch {
        throw "Fetch failed after local commit. The commit remains local and will be retried next run. $($_.Exception.Message)"
    }

    & $git -C $repo merge-base --is-ancestor "$remote/$branch" 'HEAD' 2>&1 | ForEach-Object { Write-TaskLog "git merge-base: $_" }
    if ($LASTEXITCODE -ne 0) {
        throw "Remote '$remote/$branch' is not an ancestor of local HEAD; push aborted to avoid non-fast-forward overwrite"
    }

    Invoke-Git @('push', $remote, "HEAD:$branch") | Out-Null
    $head = (& $git -C $repo rev-parse --short 'HEAD' 2>&1 | Out-String).Trim()
    Write-TaskLog "SUCCESS pushed commit $head to $remote/$branch"
    exit 0
} catch {
    Write-TaskLog "ERROR $($_.Exception.Message)"
    exit 1
}
