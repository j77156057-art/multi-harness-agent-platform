param(
    [string]$Url = 'http://127.0.0.1:8848',
    [string]$OfficeUrl = 'http://127.0.0.1:4174',
    [string]$Project = 'fresh-project',
    [string]$Group = 'fresh-group',
    [string]$Admin = 'codex-window-admin',
    [string]$AdminAdapter = '',
    [int]$WatchInterval = 60,
    [switch]$RequireAllocation,
    [switch]$NoBrowser,
    [switch]$NoWatch
)

# start-fresh.ps1 - from "I just unzipped this" to "tasks can be dispatched".
#
# ASCII only on purpose: PowerShell 5.1 reads .ps1 files with the active ANSI
# code page unless a BOM says otherwise (see checks-ascii.ps1).
#
# What it does, in order (every step is idempotent - run it twice, nothing breaks):
#   1. installs the dependencies if they are missing (setup.ps1)
#   2. starts the backend in the forced-auth mode the office gateway requires
#   3. issues the operator token and the administrator window's token
#   4. creates the project + group
#   5. starts the loops that make a dispatched task actually finish:
#      verify/merge runtime, worktree sweeper, administrator worker
#   6. starts Agent Office and opens it
#   7. registers one window per CLI that is ALREADY installed on this machine,
#      and starts that window's worker (the office does the provisioning)
#   8. keeps watching: install another CLI in the office, and within
#      -WatchInterval seconds its window is registered and starts working.
#
# So the only thing left for a person is: open the office, install a CLI from the
# "Harness CLI" card, sign in to it. Everything else is already running.
#
# Why the last two steps exist: a worker whose CLI is not on PATH claims a task
# and then fails it (the platform counts that against the attempt budget). So a
# window is only started once its CLI answers --version.

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$backend = Join-Path $PSScriptRoot 'backend'
$office = Join-Path $PSScriptRoot 'office'
$dataDir = Join-Path $PSScriptRoot 'data'
$logDir = Join-Path $dataDir 'logs'
$canonicalRepo = Join-Path $PSScriptRoot 'canonical.git'
$worktreeRoot = Join-Path $PSScriptRoot 'wt'

# Which CLI backs which window. Only the ones with an adapter are listed: a
# window here can take real tasks. Provider/adapter strings are the platform's
# own (backend/app/adapter/harnesses/*.py), not invented here.
$BaseTable = @{}
foreach ($entry in @(
    @{ Id = 'codex'; Name = 'Codex CLI'; Provider = 'openai'; Adapter = 'codex-exec' },
    @{ Id = 'claude-code'; Name = 'Claude Code'; Provider = 'anthropic'; Adapter = 'claude-print' },
    @{ Id = 'opencode'; Name = 'OpenCode'; Provider = 'sst'; Adapter = 'opencode-run' },
    @{ Id = 'qoder'; Name = 'Qoder CLI CN'; Provider = 'qoder'; Adapter = 'qoderclicn-print' },
    @{ Id = 'codebuddy'; Name = 'CodeBuddy'; Provider = 'tencent'; Adapter = 'codebuddy-print' },
    @{ Id = 'pi'; Name = 'Pi CLI'; Provider = 'ollama'; Adapter = 'pi-print' }
)) {
    $BaseTable[$entry.Id] = $entry
}

$script:Base = $Url
$script:OfficeUrl = $OfficeUrl

function Write-Step([string]$text) { Write-Host "[fresh] $text" }

function Get-Json([string]$path, [string]$token, [int]$timeoutSec = 30) {
    $headers = @{}
    if ($token) { $headers['Authorization'] = "Bearer $token" }
    try {
        $response = Invoke-WebRequest -Uri ($script:Base + $path) -Method Get -Headers $headers `
            -TimeoutSec $timeoutSec -UseBasicParsing
        if (-not $response.Content) { return $null }
        return ($response.Content | ConvertFrom-Json)
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [pscustomobject]@{ __status = $status; __error = $_.Exception.Message }
    }
}

function Post-Json([string]$path, $body, [string]$token, [hashtable]$extraHeaders, [int]$timeoutSec = 60) {
    $headers = @{}
    if ($token) { $headers['Authorization'] = "Bearer $token" }
    if ($extraHeaders) { foreach ($key in $extraHeaders.Keys) { $headers[$key] = $extraHeaders[$key] } }
    $json = if ($null -eq $body) { '{}' } else { $body | ConvertTo-Json -Compress -Depth 8 }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $response = Invoke-WebRequest -Uri ($script:Base + $path) -Method Post -Headers $headers `
            -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec $timeoutSec -UseBasicParsing
        if (-not $response.Content) { return $null }
        return ($response.Content | ConvertFrom-Json)
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [pscustomobject]@{ __status = $status; __error = $_.Exception.Message }
    }
}

function Test-Http([string]$url, [int]$timeoutSec = 3) {
    try {
        $response = Invoke-WebRequest -Uri $url -Method Get -TimeoutSec $timeoutSec -UseBasicParsing
        return [bool]($response.StatusCode -ge 200 -and $response.StatusCode -lt 500)
    } catch { return $false }
}

# The office gateway lives on a different port than the platform; these two keep
# the two addresses apart so a call cannot silently land on the wrong service.
function Get-Office([string]$path, [int]$timeoutSec = 30) {
    try {
        $response = Invoke-WebRequest -Uri ($script:OfficeUrl + $path) -Method Get `
            -TimeoutSec $timeoutSec -UseBasicParsing
        if (-not $response.Content) { return $null }
        return ($response.Content | ConvertFrom-Json)
    } catch { return $null }
}

function Post-Office([string]$path, $body, [int]$timeoutSec = 120) {
    $headers = @{ Origin = $script:OfficeUrl; 'X-Office-Formal' = '1' }
    $json = if ($null -eq $body) { '{}' } else { $body | ConvertTo-Json -Compress -Depth 8 }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    try {
        $response = Invoke-WebRequest -Uri ($script:OfficeUrl + $path) -Method Post -Headers $headers `
            -ContentType 'application/json; charset=utf-8' -Body $bytes -TimeoutSec $timeoutSec -UseBasicParsing
        if (-not $response.Content) { return $null }
        return ($response.Content | ConvertFrom-Json)
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [pscustomobject]@{ __status = $status; __error = $_.Exception.Message }
    }
}

function Wait-Http([string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Http $url) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Issue-Token([string]$type, [string]$id) {
    Push-Location $backend
    try {
        $output = & $python -m app.auth issue --type $type --id $id --label 'start-fresh' 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) { return $null }
    try { $parsed = ($output | Select-Object -Last 1) | ConvertFrom-Json } catch { return $null }
    if (-not $parsed -or -not $parsed.issued -or -not $parsed.issued.token) { return $null }
    return [string]$parsed.issued.token
}

function Read-Key([string]$name) {
    $path = Join-Path $dataDir "$name.key"
    if (-not (Test-Path $path)) { return '' }
    $text = (Get-Content -Raw -Path $path -ErrorAction SilentlyContinue)
    if (-not $text) { return '' }
    $text = $text.Trim()
    if ($text -match '^apt_[a-f0-9]{64}$') { return $text }
    return ''
}

function Save-Key([string]$name, [string]$token) {
    New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
    Set-Content -Path (Join-Path $dataDir "$name.key") -Value $token -NoNewline -Encoding ascii
}

function Start-Python([string]$title, [string[]]$arguments) {
    $safe = $title -replace '[^A-Za-z0-9_.-]', '-'
    $stdout = Join-Path $logDir "$safe.out.log"
    $stderr = Join-Path $logDir "$safe.err.log"
    $line = ($arguments | ForEach-Object { if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ } }) -join ' '
    $process = Start-Process -FilePath $python -WorkingDirectory $backend -ArgumentList $line `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    Start-Sleep -Milliseconds 400
    if ($process.HasExited) {
        $details = (Get-Content -Raw $stderr -ErrorAction SilentlyContinue)
        if (-not $details) { $details = "exited with code $($process.ExitCode)" }
        Write-Step "WARNING: $title did not stay up: $details"
        return $null
    }
    Write-Step "$title started (pid $($process.Id))"
    return $process
}

# Is a process for this window already running? Two workers on one group fight
# over the same tasks, which is worse than no worker at all. The match is exact:
# `--harness-id codex` must not match `--harness-id codex-member-1`.
function Test-Worker([string]$harnessId) {
    $pattern = '--harness-id\s+' + [regex]::Escape($harnessId) + '(\s|$)'
    foreach ($process in Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue) {
        if ($process.CommandLine -and $process.CommandLine -match $pattern) {
            return $true
        }
    }
    return $false
}

# Which of the shipped CLIs answer their own --version right now.
function Get-ReadyBases([string]$token) {
    $payload = Get-Json -path '/api/v1/harnesses/installers?probe_local=true' -token $token -timeoutSec 180
    $ready = @()
    if (-not $payload -or -not $payload.installers) { return $ready }
    foreach ($item in $payload.installers) {
        $id = [string]$item.harness_id
        if ([string]$item.status -eq 'ready' -and $BaseTable.ContainsKey($id)) {
            $ready += $BaseTable[$id]
        }
    }
    return $ready
}

function Register-Window($entry, [string]$operatorId, [string]$token) {
    $body = @{
        registered_by = $operatorId
        harness_id = $entry.Id
        name = $entry.Name
        provider = $entry.Provider
        adapter = $entry.Adapter
        adapter_version = '1.0.0'
        capabilities = @('coding')
        max_parallel_tasks = 1
        status = 'online'
        registration_version = 'reg-001'
    }
    $result = Post-Json -path '/api/v1/harnesses' -body $body -token $token
    if ($result -and $result.__status -and $result.__status -ge 400 -and $result.__status -ne 409) {
        return "registration failed: $($result.__error)"
    }
    return ''
}

# Ask the office to make this window able to work: it issues the credential,
# writes data/<id>.key and starts that window's worker. The office only does
# this for windows the platform has registered.
function Start-Window([string]$harnessId) {
    if (Test-Worker $harnessId) { return 'worker already running' }
    $result = Post-Office '/api/formal/harnesses/provision' @{ harness_id = $harnessId }
    if ($result -and $result.__status -and $result.__status -ge 400) {
        return "failed: $($result.__error)"
    }
    # The office answers in Chinese (it is the product's own wording); printing it
    # here would mix encodings, so only the facts are repeated.
    if ($result -and $result.started) { return "worker started (pid $($result.pid))" }
    if ($result -and $result.reason) { return 'worker already running' }
    return 'worker started'
}

# Was the office already running for a *different* group? Provisioning would
# then start workers that never see this project's tasks, and the run would look
# successful while nothing could be dispatched.
function Assert-OfficeScope([string]$group) {
    $tasks = Get-Office '/api/formal/tasks'
    if ($tasks -and $tasks.tasks) {
        foreach ($task in $tasks.tasks) {
            if ($task.group_id -and $task.group_id -ne $group) {
                throw "the office on $OfficeUrl is serving group '$($task.group_id)', not '$group'. Stop it (or pass -OfficeUrl) and run this script again."
            }
        }
    }
}

Write-Step 'working in ' + $PSScriptRoot

# -- 1. dependencies --------------------------------------------------------
if (-not (Test-Path $python) -or -not (Test-Path (Join-Path $office 'node_modules\vite\bin\vite.js'))) {
    Write-Step 'dependencies are missing; running setup.ps1 (a few minutes, once)'
    & (Join-Path $PSScriptRoot 'setup.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'setup.ps1 failed; fix the message above and run start-fresh.ps1 again.' }
}

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# -- 2. backend -------------------------------------------------------------
$port = ([Uri]$Url).Port
$officePort = ([Uri]$OfficeUrl).Port
$health = Get-Json -path '/health'
if ($health -and $health.status) {
    if ($health.config.auth_mode -ne 'enforce') {
        throw "Something is already listening on $Url with auth_mode=off (probably start.bat). Stop it first: the office refuses to run against a backend without forced authentication."
    }
    Write-Step "backend already running on $Url"
} else {
    $env:AP_AUTH_MODE = 'enforce'
    $env:AP_ADMIN_SUBJECTS = $Admin
    $env:AP_PORT = $port
    $env:AP_CANONICAL_REPO = $canonicalRepo
    $env:AP_WORKTREE_ROOT = $worktreeRoot
    if ($RequireAllocation) { $env:AP_ALLOCATION_REQUIRED_GROUPS = $Group }
    Start-Python 'backend' @('-m', 'app.main') | Out-Null
    if (-not (Wait-Http "$Url/health" 60)) { throw "the backend did not become ready on $Url (see data\logs\backend.err.log)" }
    Write-Step "backend ready on $Url (auth_mode=enforce)"
}

# These have to be set for every process this script starts, including when the
# backend was already up.
$env:AP_AUTH_MODE = 'enforce'
$env:AP_ADMIN_SUBJECTS = $Admin
$env:AP_CANONICAL_REPO = $canonicalRepo
$env:AP_WORKTREE_ROOT = $worktreeRoot
if ($RequireAllocation) {
    $env:AP_ALLOCATION_REQUIRED_GROUPS = $Group
} else {
    # Off on purpose for a first run. With allocation governance on, a task can
    # only be claimed after an allocation round, and a round can only be opened
    # after the plan finished its circulation review -- which needs a model call
    # from the administrator window. On a machine where nobody has signed in to a
    # CLI yet, that means "I dispatched a task and nobody ever picks it up"
    # (measured on 2026-11-01: the administrator worker logs
    # allocation.refused ... 'no completed circulation round' forever).
    Remove-Item Env:\AP_ALLOCATION_REQUIRED_GROUPS -ErrorAction SilentlyContinue
}

# -- 3. tokens --------------------------------------------------------------
$operatorToken = Read-Key 'office-operator'
if (-not $operatorToken) {
    $operatorToken = Issue-Token 'human' 'operator'
    if (-not $operatorToken) { throw 'could not issue the operator token (data\office-operator.key).' }
    Save-Key 'office-operator' $operatorToken
    Write-Step 'operator token issued -> data\office-operator.key'
}
$whoami = Get-Json -path '/api/v1/auth/whoami' -token $operatorToken
if (-not $whoami -or -not $whoami.subject) {
    throw "the operator token in data\office-operator.key is not accepted ($($whoami.__error)). Delete that file and run this script again."
}
$operatorId = [string]$whoami.subject.id
Write-Step "operator: $operatorId"

# -- 4. project + group -----------------------------------------------------
$projectResult = Post-Json -path '/api/v1/projects' -body @{ project_id = $Project; name = $Project } -token $operatorToken
if ($projectResult -and $projectResult.__status -and $projectResult.__status -ge 400 -and $projectResult.__status -ne 409) {
    throw "could not create project '$Project': $($projectResult.__error)"
}
$groupResult = Post-Json -path "/api/v1/projects/$Project/groups" -body @{ group_id = $Group; name = $Group } -token $operatorToken
if ($groupResult -and $groupResult.__status -and $groupResult.__status -ge 400 -and $groupResult.__status -ne 409) {
    throw "could not create group '$Group': $($groupResult.__error)"
}
Write-Step "project $Project / group $Group ready"

# -- 5. which CLIs can work here right now ----------------------------------
$readyBases = @(Get-ReadyBases $operatorToken)
$readyIds = @($readyBases | ForEach-Object { $_.Id })
Write-Step ('CLIs answering on this machine: ' + $(if ($readyIds.Count) { $readyIds -join ', ' } else { 'none yet' }))

# The administrator window reviews submitted work. Its adapter is a deployment
# decision, so it is made here, once: use the CLI that is actually installed
# (Codex when available), and never silently switch it later.
$adminEntry = $null
if ($AdminAdapter) {
    foreach ($entry in $BaseTable.Values) {
        if ($entry.Adapter -eq $AdminAdapter) { $adminEntry = $entry }
    }
    if (-not $adminEntry) { throw "unknown -AdminAdapter '$AdminAdapter'. Known: $(($BaseTable.Values | ForEach-Object { $_.Adapter }) -join ', ')" }
} elseif ($readyIds -contains 'codex') {
    $adminEntry = $BaseTable['codex']
} elseif ($readyIds.Count -gt 0) {
    $adminEntry = $readyBases[0]
} else {
    $adminEntry = $BaseTable['codex']
}
$adminBody = @{
    registered_by = $operatorId
    harness_id = $Admin
    name = 'Administrator window'
    provider = $adminEntry.Provider
    adapter = $adminEntry.Adapter
    adapter_version = '1.0.0'
    capabilities = @('coding')
    max_parallel_tasks = 1
    status = 'online'
    registration_version = 'reg-001'
}
$registered = Post-Json -path '/api/v1/harnesses' -body $adminBody -token $operatorToken
if ($registered -and $registered.__status -and $registered.__status -ge 400 -and $registered.__status -ne 409) {
    Write-Step "WARNING: could not register the administrator window: $($registered.__error)"
}
Write-Step "administrator window $Admin runs on $($adminEntry.Id) ($($adminEntry.Adapter))"
$adminToken = Read-Key $Admin
if (-not $adminToken) {
    $adminToken = Issue-Token 'harness' $Admin
    if ($adminToken) {
        Save-Key $Admin $adminToken
        Write-Step "administrator token issued -> data\$Admin.key"
    } else {
        Write-Step "WARNING: could not issue a token for $Admin; approval will have to be done by hand in the office."
    }
}

# -- 6. the loops that make a dispatched task actually finish ---------------
$script:loopsUp = $false
foreach ($running in @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue)) {
    if ($running.CommandLine -and ($running.CommandLine -like '*app.runtime*' -or $running.CommandLine -like '*app.scheduler*')) {
        $script:loopsUp = $true
    }
}
if ($script:loopsUp) {
    Write-Step 'runtime/scheduler already running'
} else {
    Start-Python 'runtime' @('-m', 'app.runtime', '--loop', '--interval', '15') | Out-Null
    # Without the scheduler nothing recycles worktrees: the cap (8) fills up with
    # dead attempts and every new task fails to get one.
    Start-Python 'scheduler' @('-m', 'app.scheduler', '--loop') | Out-Null
}
$script:adminStarted = $false
function Start-AdminWorker {
    if (Test-Worker $Admin) { $script:adminStarted = $true; return }
    $args = @('-m', 'app.admin.worker', '--url', $Url, '--group', $Group, '--admin-harness-id', $Admin,
        '--token-file', (Join-Path $dataDir "$Admin.key"), '--review-rounds', '2')
    Start-Python 'administrator' $args | Out-Null
    $script:adminStarted = $true
}
if ($readyIds -contains $adminEntry.Id) {
    Start-AdminWorker
} else {
    Write-Step "administrator window stays idle until $($adminEntry.Id) is installed (until then approve submitted work by hand in the office)"
}

# -- 7. the office ----------------------------------------------------------
if (Test-Http "$OfficeUrl/api/formal/status") {
    Write-Step "office already running on $OfficeUrl"
} else {
    $env:OFFICE_PORT = $officePort
    $env:OFFICE_FORMAL_UPSTREAM = $Url
    $env:OFFICE_FORMAL_TOKEN_FILE = (Join-Path $dataDir 'office-operator.key')
    $env:OFFICE_FORMAL_ADMIN_TOKEN_FILE = (Join-Path $dataDir "$Admin.key")
    $env:OFFICE_FORMAL_PROJECT = $Project
    $env:OFFICE_FORMAL_GROUP = $Group
    $stdout = Join-Path $logDir 'office.out.log'
    $stderr = Join-Path $logDir 'office.err.log'
    $officeProcess = Start-Process -FilePath 'node' -ArgumentList 'scripts/start-office.mjs' -WorkingDirectory $office `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden
    if (-not (Wait-Http "$OfficeUrl/api/formal/status" 90)) {
        throw "the office did not come up on $OfficeUrl (see data\logs\office.err.log); pid $($officeProcess.Id)"
    }
    Write-Step "office ready on $OfficeUrl"
}
Assert-OfficeScope $Group

# -- 8. one window per installed CLI ---------------------------------------
foreach ($entry in $readyBases) {
    $problem = Register-Window $entry $operatorId $operatorToken
    if ($problem) { Write-Step "WARNING: window $($entry.Id): $problem"; continue }
    $outcome = Start-Window $entry.Id
    Write-Step "window $($entry.Id): $outcome"
}

function Find-NewWork {
    $ready = @(Get-ReadyBases $operatorToken)
    foreach ($entry in $ready) {
        $problem = Register-Window $entry $operatorId $operatorToken
        if ($problem) { Write-Host "[fresh] $((Get-Date).ToString('HH:mm:ss')) WARNING $($entry.Id): $problem"; continue }
        if (Test-Worker $entry.Id) { continue }
        $outcome = Start-Window $entry.Id
        Write-Host "[fresh] $((Get-Date).ToString('HH:mm:ss')) $($entry.Id): $outcome"
    }
    if (-not $script:adminStarted -and ($ready | Where-Object { $_.Id -eq $adminEntry.Id })) {
        Write-Host "[fresh] $((Get-Date).ToString('HH:mm:ss')) $($adminEntry.Id) is in; starting the administrator worker"
        Start-AdminWorker
    }
}

Write-Host ''
Write-Step 'ready. Open the office and install what you want to use:'
Write-Host ''
Write-Host "    $OfficeUrl"
Write-Host ''
Write-Host '    Right column -> "Harness CLI" -> install a CLI -> sign in to it.'
Write-Host "    A window for it is registered and started automatically within $WatchInterval seconds."
Write-Host '    Then describe a goal in "Opening" and dispatch: that task will be picked up.'
Write-Host ''
Write-Host '    Sign in before you dispatch. "Installed" only proves the command answers'
Write-Host '    its --version: a window whose CLI is not signed in still claims the task'
Write-Host '    and then fails it, and the attempts are gone. If that happens, sign in'
Write-Host '    and reopen the task from its detail page.'
Write-Host ''

if (-not $NoBrowser) { Start-Process $OfficeUrl | Out-Null }

if ($NoWatch) {
    Write-Step 'watch loop disabled (-NoWatch); run this script again after installing a CLI.'
    exit 0
}

Write-Step "watching for newly installed CLIs every $WatchInterval seconds (Ctrl+C stops watching, running windows keep working)"
while ($true) {
    Start-Sleep -Seconds $WatchInterval
    Find-NewWork
}
