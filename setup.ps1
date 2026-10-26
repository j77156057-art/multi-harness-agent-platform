$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python 3.11 or newer is required. Install Python from https://www.python.org/downloads/ and run setup.ps1 again.'
}

$version = & $python.Source --version 2>&1
if ($version -notmatch 'Python 3\.(1[1-9]|[2-9][0-9])') {
    throw "Python 3.11 or newer is required. Found: $version"
}

$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    & $python.Source -m venv (Join-Path $PSScriptRoot '.venv')
}

& $venvPython -m pip install -r (Join-Path $PSScriptRoot 'backend\requirements-dev.txt')

$npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $npm) {
    throw 'Node.js 18 or newer is required for Agent Office. Install Node.js from https://nodejs.org/ and run setup.ps1 again.'
}

Push-Location (Join-Path $PSScriptRoot 'office')
try {
    & $npm.Source ci
} finally {
    Pop-Location
}

Write-Host 'Setup complete.'
Write-Host 'Local demo: .\start.bat and .\start-office.bat'
Write-Host 'Formal Harness mode: set up registered token files, then run .\start-formal.ps1 and .\start-office.bat'
