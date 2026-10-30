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
    [string]$Admin = 'codex-window-admin'
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
    $kwargs = @{
        Uri = "$base$path"
        Method = $method
        TimeoutSec = 15
        Headers = $headers
        UseBasicParsing = $true
    }
    if ($null -ne $body) {
        $kwargs['Body'] = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 6))
        $kwargs['ContentType'] = 'application/json; charset=utf-8'
    }
    try {
        return (Invoke-WebRequest @kwargs).Content | ConvertFrom-Json
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
$operatorToken = Ensure-Token $operatorKey 'human' 'operator'
Write-Host "[demo] operator token: data\office-operator.key"

# 3. admin harness + demo project / group ------------------------------------
$registered = Invoke-Json 'POST' '/api/v1/harnesses' @{
    registered_by = 'operator'
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
Start-Process -FilePath 'cmd.exe' -WorkingDirectory $office -ArgumentList '/c', 'npm run dev' -WindowStyle Normal

Write-Host ''
Write-Host "[demo] Agent Office: http://127.0.0.1:$officePort"
Write-Host "[demo] backend:      $base   (docs: $base/docs)"
Write-Host "[demo] next: open the office, create a plan, then start a harness"
Write-Host "       worker with:  python -m app.adapter.formal_worker --url $base --token-file data\$Admin.key --harness-id $Admin --group $Group"
