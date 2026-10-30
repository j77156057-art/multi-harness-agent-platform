# start-demo.ps1 - one command from a fresh clone to a usable Agent Office.
#
# ASCII only on purpose: PowerShell 5.1 reads .ps1 files with the active ANSI
# code page unless a BOM says otherwise, so Chinese comments here would be
# garbled before any parsing happens.
#
# What it does, in order (all of it idempotent - run it twice, nothing breaks):
#   1. starts the backend in the FORCED-AUTH mode the office gateway requires
#   2. issues the two token files it needs (human operator + one admin harness)
#   3. registers that admin harness and creates a demo project + group
#   4. starts Agent Office with the five OFFICE_FORMAL_* variables set
#   5. opens the browser, but only once the office actually answers
#
# Use -NoBrowser when you just want to restart it.
#
# Why forced auth for a local demo: the office can dispatch tasks and approve
# plans, so its gateway refuses to run against an unauthenticated backend
# (formal_authentication_required). With AP_AUTH_MODE=off the browser would open
# and then do nothing.
#
# Knobs (all optional):
#   AP_PORT     backend port, default 8848
#   OFFICE_PORT office port, default 4174
param(
    [string]$Project = 'demo-project',
    [string]$Group = 'demo-group',
    [string]$Admin = 'codex-window-admin',
    # Skip opening the browser (plain restart, or automation).
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
$backend = Join-Path $root 'backend'
$office = Join-Path $root 'office'
$data = Join-Path $root 'data'
$port = if ($env:AP_PORT) { $env:AP_PORT } else { '8848' }
$officePort = if ($env:OFFICE_PORT) { $env:OFFICE_PORT } else { '4174' }
$base = "http://127.0.0.1:$port"

if (-not (Test-Path $python)) {
    throw "Python environment is missing. Run .\setup.ps1 first."
}

# The office front end needs its own dependencies. Install them here instead of
# letting a dev server die in a window that closes before anyone can read it.
if (-not (Test-Path (Join-Path $office 'node_modules'))) {
    Write-Host '[demo] installing the office front end (npm ci, a minute or two)...'
    Push-Location $office
    try { & cmd.exe /c 'npm ci' } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) {
        throw 'npm ci failed. Install Node.js 18 or newer, then run .\setup.ps1 again.'
    }
}

New-Item -ItemType Directory -Force -Path $data | Out-Null

function Get-Health {
    try {
        return Invoke-RestMethod -Uri "$base/health" -TimeoutSec 3
    } catch {
        return $null
    }
}

function Wait-ForHealth([int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $health = Get-Health
        if ($health -and $health.status -eq 'ok') { return $health }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Invoke-Json([string]$method, [string]$path, $body, [string]$token) {
    $headers = @{}
    if ($token) { $headers['Authorization'] = "Bearer $token" }
    $uri = "$base$path"
    # Named parameters, not splatting: on Windows PowerShell 5.1 a splatted
    # call to Invoke-WebRequest with no body kills the process silently (exit
    # code 0, nothing printed, try/catch never runs), while the same arguments
    # written out by name work. Requests with a body were fine either way, which
    # is why this only showed up on the first GET the script ever made.
    try {
        if ($null -ne $body) {
            $payload = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 6))
            $response = Invoke-WebRequest -Uri $uri -Method $method -TimeoutSec 15 -Headers $headers `
                -ContentType 'application/json; charset=utf-8' -Body $payload -UseBasicParsing
        } else {
            $response = Invoke-WebRequest -Uri $uri -Method $method -TimeoutSec 15 -Headers $headers -UseBasicParsing
        }
        return $response.Content | ConvertFrom-Json
    } catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        return [pscustomobject]@{ __status = $status; __error = "$($_.Exception.Message)" }
    }
}

function Ensure-Token([string]$path, [string]$type, [string]$id) {
    if (Test-Path $path) {
        $existing = (Get-Content -Path $path -Raw).Trim()
        if ($existing) { return $existing }
    }
    Push-Location $backend
    try {
        $out = & $python -m app.auth issue --type $type --id $id --label 'local demo' 2>&1
    } finally {
        Pop-Location
    }
    $line = ($out | Where-Object { "$_" -match '"issued"' } | Select-Object -First 1)
    if (-not $line) {
        throw "Could not issue a $type token for $id. Output: $out"
    }
    $token = ($line | ConvertFrom-Json).issued.token
    Set-Content -Path $path -Value $token -NoNewline -Encoding ascii
    return $token
}

# 1. backend, in the mode the office needs -----------------------------------
$health = Get-Health
if ($health -and $health.config.auth_mode -ne 'enforce') {
    throw "A backend is already running on port $port with auth_mode=$($health.config.auth_mode). Stop it first (Ctrl+C in its window), then run this script again."
}
if (-not $health) {
    Write-Host "[demo] starting backend on $base (auth_mode=enforce)"
    $env:AP_PORT = $port
    $env:AP_AUTH_MODE = 'enforce'
    $env:AP_ADMIN_SUBJECTS = $Admin
    Start-Process -FilePath $python -WorkingDirectory $backend -ArgumentList '-m', 'app.main' -WindowStyle Minimized
    $health = Wait-ForHealth 60
    if (-not $health) { throw "The backend did not become healthy on $base. Check the backend window for errors." }
}
Write-Host "[demo] backend ok (auth_mode=$($health.config.auth_mode))"

# 2. tokens ------------------------------------------------------------------
$operatorKey = Join-Path $data 'office-operator.key'
$adminKey = Join-Path $data "$Admin.key"
# The id here is only a hint: an existing key file wins, and the platform is the
# one that knows which subject that key belongs to. Registering a Harness
# declares "who is doing the registering", so the declared id has to be the
# token's real subject -- otherwise the platform refuses with 403
# identity_mismatch. Ask instead of assuming, or a machine that already has a
# key file (issued under another id) can never run this script.
$operatorToken = Ensure-Token $operatorKey 'human' 'operator'
$whoami = Invoke-Json 'GET' '/api/v1/auth/whoami' $null $operatorToken
if (-not $whoami.subject -or -not $whoami.subject.id) {
    throw "The operator token in data\office-operator.key is not accepted by the platform ($($whoami.__error)). Delete that file and run this script again."
}
$operatorId = [string]$whoami.subject.id
Write-Host "[demo] operator token: data\office-operator.key (subject $operatorId)"

# 3. admin harness + demo project / group ------------------------------------
$registered = Invoke-Json 'POST' '/api/v1/harnesses' @{
    registered_by = $operatorId
    harness_id = $Admin
    name = 'Codex (admin window)'
    provider = 'openai'
    adapter = 'codex-exec'
    adapter_version = '1.0.0'
    capabilities = @('coding')
    max_parallel_tasks = 1
    status = 'online'
    registration_version = 'reg-001'
} $operatorToken
if ($registered.__status -and $registered.__status -ge 400 -and $registered.__status -ne 409) {
    throw "Could not register the admin harness: $($registered.__error)"
}
$null = Ensure-Token $adminKey 'harness' $Admin
Write-Host "[demo] admin harness '$Admin' registered, token: data\$Admin.key"

# NOTE: PowerShell variable names are case-insensitive, so the API results must
# not be stored in $project / $group -- that would overwrite the parameters.
$projectResult = Invoke-Json 'POST' '/api/v1/projects' @{ project_id = $Project; name = $Project } $operatorToken
if ($projectResult.__status -and $projectResult.__status -ge 400 -and $projectResult.__status -ne 409) {
    throw "Could not create the project: $($projectResult.__error)"
}
$groupResult = Invoke-Json 'POST' "/api/v1/projects/$Project/groups" @{ group_id = $Group; name = $Group } $operatorToken
if ($groupResult.__status -and $groupResult.__status -ge 400 -and $groupResult.__status -ne 409) {
    throw "Could not create the group: $($groupResult.__error)"
}
Write-Host "[demo] project '$Project' / group '$Group' ready"

# 4. office ------------------------------------------------------------------
Write-Host "[demo] starting Agent Office on http://127.0.0.1:$officePort"
$env:OFFICE_FORMAL_UPSTREAM = $base
$env:OFFICE_FORMAL_TOKEN_FILE = $operatorKey
$env:OFFICE_FORMAL_ADMIN_TOKEN_FILE = $adminKey
$env:OFFICE_FORMAL_PROJECT = $Project
$env:OFFICE_FORMAL_GROUP = $Group
$env:OFFICE_PORT = $officePort
# Reuse an office that is already answering: double-clicking twice must not
# start a second dev server on the same port.
$officeUp = $false
try {
    $officeUp = (Invoke-WebRequest -Uri "http://127.0.0.1:$officePort/" -UseBasicParsing -TimeoutSec 2).StatusCode -ge 200
} catch {
    $officeUp = $false
}
if ($officeUp) {
    Write-Host "[demo] Agent Office already answering on http://127.0.0.1:$officePort - reusing it."
} else {
    Start-Process -FilePath 'cmd.exe' -WorkingDirectory $office -ArgumentList '/c', 'npm run dev' -WindowStyle Normal
}

Write-Host ''
$officeUrl = "http://127.0.0.1:$officePort/"
Write-Host "[demo] Agent Office: $officeUrl"
Write-Host "[demo] backend:      $base   (docs: $base/docs)"
Write-Host "[demo] first screen: the lobby lists every Harness CLI with its official"
Write-Host "       download page and install command. Install what is missing, sign in,"
Write-Host "       then create a project and press Start setup."
Write-Host "[demo] next: open the office, create a plan, then start a harness"
Write-Host "       worker with:  python -m app.adapter.formal_worker --url $base --token-file data\$Admin.key --harness-id $Admin --group $Group"

# Open the browser only after the office answers. Opening it first shows a
# connection-refused page, and a person reads that as "it is broken".
if (-not $NoBrowser) {
    Write-Host "[demo] waiting for the office to answer, then opening the browser..."
    $deadline = (Get-Date).AddSeconds(180)
    $opened = $false
    while ((Get-Date) -lt $deadline) {
        $code = 0
        try {
            $code = (Invoke-WebRequest -Uri $officeUrl -UseBasicParsing -TimeoutSec 3).StatusCode
        } catch {
            $code = 0
        }
        if ($code -ge 200 -and $code -lt 500) {
            Start-Process $officeUrl
            $opened = $true
            break
        }
        Start-Sleep -Milliseconds 700
    }
    if (-not $opened) {
        Write-Host "[demo] the office has not answered yet. Open $officeUrl yourself in a moment."
    }
}
