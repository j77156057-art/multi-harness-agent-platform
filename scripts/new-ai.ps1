<#
.SYNOPSIS
    Provision an isolated git worktree for one AI agent (dev-convention.md H-7).

.DESCRIPTION
    Every agent sharing one working tree loses commits silently: any checkout,
    switch or reset by one agent moves everyone else's HEAD and working tree,
    and uncommitted edits vanish while git status still reports clean.

    This creates .worktrees/<harness> on branch agent/workspace/<harness> and
    gives it an INDEPENDENT virtualenv (installed from
    backend/requirements-dev.txt) so ./scripts.sh check runs unchanged.

    Why independent instead of a junction to the main .venv: on 2026-10-05 the
    main .venv was wiped and rebuilt while all 8 worktrees were junctions to it.
    Every agent broke at once and each one's gate run failed for a reason that
    had nothing to do with their own code. Cost is 106 MB per worktree.
    Pass -SharedVenv to restore the old junction behaviour.

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

.PARAMETER SharedVenv
    Junction the main .venv into the worktree instead of creating an
    independent one. Faster and smaller, but a broken main .venv then breaks
    every worktree at once. Off by default since 2026-10-06.

.EXAMPLE
    ./scripts/new-ai.ps1 -Harness qoder
    ./scripts/new-ai.ps1 -Harness qoder -SharedVenv
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*$')]
    [string]$Harness,

    [string]$From = 'main',

    [switch]$SharedVenv
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

    # 每个 worktree 一个**独立** venv，不用 junction 指向根目录。
    #
    # 为什么改（2026-10-05 23:48 事故）：根 .venv 被清空重建过（只剩 pip，2 个包），
    # 而当时 8 个 worktree 的 .venv 全是指向它的 junction —— 根一坏，全部一起坏，
    # 且所有人的门禁都莫名失败（WorkBuddy 因此误判过一次假信号）。
    # 代价：106 MB x N（磁盘可用 80 G，可接受）。需要共享时用 -SharedVenv 走 junction。
    $targetVenv = Join-Path $repoRoot ".worktrees/$Harness/.venv"
    $reqFile = Join-Path $repoRoot 'backend/requirements-dev.txt'

    if ($SharedVenv) {
        $sourceVenv = Join-Path $repoRoot '.venv'
        if ((Test-Path $sourceVenv) -and -not (Test-Path $targetVenv)) {
            New-Item -ItemType Junction -Path $targetVenv -Target (Resolve-Path $sourceVenv).Path | Out-Null
            Write-Host 'venv   : junctioned to main .venv (-SharedVenv)'
        }
        elseif (-not (Test-Path $sourceVenv)) {
            Write-Warning 'main .venv not found; run without -SharedVenv.'
        }
    }
    elseif (Test-Path $targetVenv) {
        Write-Host 'venv   : already present, kept as is'
    }
    else {
        $basePython = Get-Command python -ErrorAction SilentlyContinue
        if (-not $basePython) {
            throw 'python not found on PATH; cannot create an independent venv.'
        }
        Write-Host 'venv   : creating an independent virtualenv (~1 min)...'
        $created = & $basePython.Source -m venv $targetVenv 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "venv creation failed. Output: $($created -join ' | ')"
        }
        $venvPy = Join-Path $targetVenv 'Scripts/python.exe'
        & $venvPy -m pip install --quiet --upgrade pip 2>&1 | Out-Null
        $installed = & $venvPy -m pip install --quiet -r $reqFile 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "dependency install failed. Output: $($installed -join ' | ')"
        }
        $probe = & $venvPy -c 'import pytest, fastapi; print("ok")' 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "venv smoke test failed. Output: $($probe -join ' | ')"
        }
        Write-Host 'venv   : independent venv ready (pytest + fastapi importable)'
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