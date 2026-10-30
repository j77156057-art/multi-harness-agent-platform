# checks-ascii.ps1 - the repository rule for PowerShell scripts and .bat files.
#
# Windows parses .ps1 with the active ANSI code page unless the file carries a
# BOM, and .bat with the console code page. Non-ASCII text in either file is
# therefore read as something else entirely: at best it shows up as mojibake, at
# worst a mangled byte sequence changes how the file parses (a comment can eat
# the next line, a quote can appear out of nowhere). So those files stay ASCII.
#
# It checks the files git tracks, not everything on disk: local clones, worktrees
# and build output sit under this folder too, and their scripts are not ours.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Push-Location $root
try {
    # --others: a script that has not been committed yet still ships, and it is
    # exactly the file most likely to carry a fresh non-ASCII mistake.
    $tracked = & git ls-files --cached --others --exclude-standard -- '*.ps1' '*.bat'
    if ($LASTEXITCODE -ne 0) { throw 'git ls-files failed; run this inside the repository.' }
} finally {
    Pop-Location
}

$failed = 0
foreach ($relative in $tracked) {
    $path = Join-Path $root $relative
    if (-not (Test-Path $path)) { continue }
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $bad = 0
    foreach ($byte in $bytes) { if ($byte -gt 127) { $bad++ } }
    if ($bad -gt 0) {
        Write-Host ("[ascii] FAIL {0}: {1} non-ASCII bytes" -f $relative, $bad)
        $failed++
        continue
    }
    if ($relative -like '*.ps1') {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($path, [ref]$tokens, [ref]$errors)
        if ($errors -and $errors.Count -gt 0) {
            Write-Host ("[ascii] FAIL {0}: {1} parse error(s), first: {2}" -f $relative, $errors.Count, $errors[0].Message)
            $failed++
            continue
        }
    }
    Write-Host ("[ascii] ok   {0}" -f $relative)
}

if ($failed -gt 0) {
    Write-Host ("[ascii] {0} file(s) failed" -f $failed)
    exit 1
}
Write-Host ("[ascii] all {0} tracked script(s) are ASCII and parse cleanly" -f $tracked.Count)
