param(
    [string]$Url = 'http://127.0.0.1:8848',
    [string]$Group = 'office-formal',
    [string]$AdminHarnessId = 'codex-window-admin',
    [string]$CanonicalRepo = '',
    [string]$WorktreeRoot = ''
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Python environment is missing. Run .\setup.ps1 first.'
}

$tokenNames = @(
    'claude-code',
    'codebuddy-member-1',
    'opencode',
    'game-pi',
    'qoder-member-1',
    'qoder-member-2',
    'codex-window-admin'
)
foreach ($name in $tokenNames) {
    $tokenPath = Join-Path $PSScriptRoot "data\$name.key"
    if (-not (Test-Path $tokenPath)) {
        throw "Missing credential file: $tokenPath. Formal mode needs tokens issued for the registered Harnesses."
    }
}

$env:AP_AUTH_MODE = 'enforce'
$env:AP_ADMIN_SUBJECTS = $AdminHarnessId
$env:AP_ALLOCATION_REQUIRED_GROUPS = $Group
$env:AP_PORT = ([Uri]$Url).Port

if (-not $CanonicalRepo) { $CanonicalRepo = Join-Path $PSScriptRoot 'canonical.git' }
if (-not $WorktreeRoot) { $WorktreeRoot = Join-Path $PSScriptRoot 'wt' }
$backend = Join-Path $PSScriptRoot 'backend'
$logDir = Join-Path $PSScriptRoot 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:AP_CANONICAL_REPO = $CanonicalRepo
$env:AP_WORKTREE_ROOT = $WorktreeRoot

function Quote-Arg([string]$value) { return '"' + $value.Replace('"', '\"') + '"' }
function Start-Python([string]$title, [string[]]$arguments) {
    $argumentLine = ($arguments | ForEach-Object {
        if ($_ -match '[\s"]') { Quote-Arg $_ } else { $_ }
    }) -join ' '
    $safeTitle = $title -replace '[^A-Za-z0-9_.-]', '-'
    $stdout = Join-Path $logDir "$safeTitle.out.log"
    $stderr = Join-Path $logDir "$safeTitle.err.log"
    $process = Start-Process -FilePath $python -WorkingDirectory $backend -ArgumentList $argumentLine -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        $details = (Get-Content -Raw $stderr -ErrorAction SilentlyContinue).Trim()
        if (-not $details) { $details = "process exited with code $($process.ExitCode)" }
        throw "[$title] failed to start: $details"
    }
    Write-Host "[$title] started (pid $($process.Id))"
}

Start-Python 'backend' @('-m', 'app.main')
Start-Sleep -Seconds 2
try {
    $health = Invoke-RestMethod -TimeoutSec 5 "$Url/health"
    if ($health.status -ne 'ok' -or $health.config.auth_mode -ne 'enforce') {
        throw 'backend health check did not report auth_mode=enforce'
    }
} catch {
    throw "Formal backend did not become ready: $($_.Exception.Message)"
}

$common = @('--url', $Url, '--group', $Group, '--canonical-repo', $CanonicalRepo, '--worktree-root', $WorktreeRoot)
$members = @(
    @{ Id = 'claude-code'; Token = 'claude-code.key' },
    @{ Id = 'codebuddy-member-1'; Token = 'codebuddy-member-1.key' },
    @{ Id = 'opencode'; Token = 'opencode.key' },
    @{ Id = 'pi'; Token = 'game-pi.key' },
    @{ Id = 'qoder-member-1'; Token = 'qoder-member-1.key' },
    @{ Id = 'qoder-member-2'; Token = 'qoder-member-2.key' }
)
foreach ($member in $members) {
    $token = Join-Path $PSScriptRoot "data\$($member.Token)"
    Start-Python "member $($member.Id)" (@('-m', 'app.adapter.formal_worker') + $common + @('--harness-id', $member.Id, '--token-file', $token))
}
$adminToken = Join-Path $PSScriptRoot 'data\codex-window-admin.key'
Start-Python 'administrator' @('-m', 'app.admin.worker', '--url', $Url, '--group', $Group, '--admin-harness-id', $AdminHarnessId, '--token-file', $adminToken)

Write-Host "Formal platform is ready at $Url"
Write-Host "Group: $Group; administrator: $AdminHarnessId"
Write-Host 'Keep the opened worker windows running while submitting work through Agent Office.'
