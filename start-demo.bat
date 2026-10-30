@echo off
rem Double-click entry point: bring the platform and the office up, then open
rem the browser. Everything it needs is in this folder.
rem
rem ASCII only on purpose: cmd parses .bat files with the active code page, so
rem non-ASCII text here would be garbled before any chcp could take effect.
rem
rem Why a .bat and not just the .ps1: a .ps1 opened from Explorer only lands in
rem Notepad (or is blocked by the execution policy). Double-clicking this file
rem runs it, and the window stays open long enough to read what went wrong.
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if not exist ".venv\Scripts\python.exe" goto :setup
if not exist "office\node_modules" goto :setup
goto :run

:setup
echo [demo] First run: installing the Python environment and the office front end.
echo [demo] This takes a few minutes and only happens once.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 goto :failed

:run
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-demo.ps1"
if errorlevel 1 goto :failed
echo.
echo [demo] The browser should be open now.
echo [demo] Keep the two windows that just opened (backend + office) running;
echo [demo] closing them stops the platform. This window can be closed.
echo.
pause
exit /b 0

:failed
echo.
echo [demo] Something went wrong. The message above says what.
echo [demo] Common causes: Python 3.11+ or Node.js 18+ is missing, or ports
echo [demo] 8848 / 4174 are already taken by another program.
echo.
pause
exit /b 1
