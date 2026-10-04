<#
.SYNOPSIS
    Provision an isolated git worktree for one AI agent (dev-convention.md H-7).

.DESCRIPTION
    Every agent sharing one working tree loses commits silently: any checkout,
    switch or reset by one agent moves everyone else's HEAD and working tree,
    and uncommitted edits vanish while git status still reports clean.

    This creates .worktrees/<harness> on branch agent/workspace/<harness> and
    junctions the main .venv into it so ./scripts.sh check runs unchanged.

    Idempotent: if the worktree already exists this exits without touching anything.

    NOTE: this fixes working-tree collisions, NOT branch-ref collisions. H-7
    rule 2 (only move refs you own) still has to be honoured by hand.
    For the bash equivalent see dev-convention.md H-7.

    Kept ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as
    ANSI, and non-ASCII text corrupts the parse. Chinese docs live in the .md.

.PARAMETER Harness
    Agent name, e.g. workbuddy, codex, claude, qoder. Used for the directory
    and the resident branch name.

.PARAMETER From
    Base ref for the resident branch. Defaults to main.

.EXAMPLE
    ./scripts/new-ai.ps1 -Harness qoder
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$Harness,

    [string]$From = 'main'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$worktreePath = Join-Path $repoRoot ".worktrees/$Harness"
$branch = "agent/workspace/$Harness"

# PowerShell 5.1 turns native stderr into terminating errors under ErrorActionPreference
# 'Stop', and git reports routine progress ("Preparing worktree") on stderr. Relax the
# preference for the duration of each git call and judge success by exit code instead.
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & git @GitArgs 2>&1 | ForEach-Object { $_.ToString() }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = $output }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

Push-Location $repoRoot

try {
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw 'git not found on PATH.'
    }

    $resolved = Invoke-Git rev-parse --verify $From
    if ($resolved.ExitCode -ne 0) { throw "base ref '$From' does not exist." }
    # @() matters: a single-line result is a bare string, and $str[0] would be its
    # first character rather than the whole line.
    $base = (@($resolved.Output) | Select-Object -First 1).Trim()

    $listed = Invoke-Git worktree list --porcelain
    if ($listed.Output -match [regex]::Escape("worktrees/$Harness")) {
        throw "worktree .worktrees/$Harness already exists; nothing changed."
    }

    Write-Host "== provisioning worktree for $Harness ==" -ForegroundColor Cyan
    Write-Host "base   : $From @ $base"
    Write-Host "path   : .worktrees/$Harness"
    Write-Host "branch : $branch"

    # git ref updates intermittently fail with "couldn't set refs/heads/..." when
    # another agent holds the ref lock. Retry a few times before giving up; if it
    # still fails the branch may already exist, which is what the caller's next
    # run will report as an existing worktree.
    $added = $null
    foreach ($attempt in 1..3) {
        $added = Invoke-Git worktree add -b $branch ".worktrees/$Harness" $From
        if ($added.ExitCode -eq 0) { break }
        if ($attempt -lt 3) {
            Write-Host "git    : attempt $attempt failed (ref lock contention?), retrying..." -ForegroundColor Yellow
            Start-Sleep -Seconds 2
        }
    }
    $added.Output | ForEach-Object { Write-Host "git    : $_" -ForegroundColor DarkGray }
    if ($added.ExitCode -ne 0) {
        throw "git worktree add failed after 3 attempts. Output: $($added.Output -join ' | ')"
    }

    # Junction the main venv so ./scripts.sh check works with no edits.
    $sourceVenv = Join-Path $repoRoot '.venv'
    $targetVenv = Join-Path $repoRoot ".worktrees/$Harness/.venv"
    if ((Test-Path $sourceVenv) -and -not (Test-Path $targetVenv)) {
        New-Item -ItemType Junction -Path $targetVenv -Target (Resolve-Path $sourceVenv).Path | Out-Null
        Write-Host 'venv   : junctioned to main .venv'
    }
    elseif (-not (Test-Path $sourceVenv)) {
        Write-Warning 'main .venv not found; this worktree needs its own virtualenv.'
    }

    # Smoke-test the interpreter so the problem surfaces now, not at kickoff.
    # --version keeps us clear of native quoting rules; stderr is relaxed for the
    # same reason as the git calls above.
    $python = Join-Path $repoRoot ".worktrees/$Harness/.venv/Scripts/python.exe"
    if (Test-Path $python) {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $ver = (& $python --version 2>&1 | ForEach-Object { $_.ToString() }) -join ' '
        }
        finally {
            $ErrorActionPreference = $previous
        }
        Write-Host "python : $ver"
    }

    # The resident branch is a base, not a place to work: cut a task branch from main.
    Write-Host ''
    Write-Host 'next (run inside that worktree):' -ForegroundColor Yellow
    Write-Host "  cd .worktrees/$Harness"
    Write-Host "  git switch -c agent/<group-short>/$Harness/<task-id>/<attempt-id> $From"
    Write-Host ''
    Write-Host 'then register the claim in TASKS.md before starting work.'

    Write-Host ''
    Write-Host '== all worktrees ==' -ForegroundColor Cyan
    (Invoke-Git worktree list).Output | ForEach-Object { Write-Host $_ }
}
finally {
    Pop-Location
}