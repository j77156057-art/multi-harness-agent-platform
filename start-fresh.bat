@echo off
rem Double-click entry point: from a fresh copy to "tasks can be dispatched".
rem
rem ASCII only on purpose: cmd parses .bat files with the active code page.
rem
rem What is left for a person afterwards: open the office, install a CLI from
rem the "Harness CLI" card, sign in to it. The window for it is registered and
rem started automatically within a minute.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

rem The launcher keeps watching for newly installed CLIs, so this window has to
rem stay open. Closing it stops the watching, not the workers already started.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-fresh.ps1"
if errorlevel 1 goto :failed
exit /b 0

:failed
echo.
echo [fresh] Something went wrong. The message above says what.
echo [fresh] Common causes: Python 3.11+ or Node.js 18+ is missing, or the
echo [fresh] backend port (8848) / office port (4174) is taken by something
echo [fresh] else. Logs are in data\logs\.
echo.
pause
exit /b 1
