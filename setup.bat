@echo off
rem One-click setup entry for Windows cmd.
rem ASCII-only on purpose: cmd parses .bat files with the active code page, so
rem non-ASCII text here would garble before any chcp could take effect.
rem
rem Why this wrapper exists: a stock Windows install ships PowerShell with the
rem execution policy set to Restricted, so ".\setup.ps1" is refused before a
rem single line of it runs. -ExecutionPolicy Bypass applies to this one
rem invocation only; it does not change the machine-wide policy. start.bat uses
rem the same form, so both entry points behave the same way.
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
set "RC=%ERRORLEVEL%"
exit /b %RC%
