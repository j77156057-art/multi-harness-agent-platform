@echo off
rem One-click launcher for the agent collaboration platform (Windows cmd).
rem ASCII-only on purpose: cmd parses .bat files with the active code page, so
rem non-ASCII text here would garble before any chcp could take effect.
rem
rem The human UI is the Agent Office at http://127.0.0.1:4174, a separate app
rem with its own repo; this script only starts the platform backend/workers.
rem
rem Modes:
rem   start.bat            backend only  (http://127.0.0.1:8848)
rem   start.bat all        backend + sweeper loop  (2 windows)
rem   start.bat workers    sweeper loop only  (app.scheduler --loop)
rem   start.bat help       usage
setlocal
cd /d "%~dp0"

set "ROOT=%~dp0"
set "PYBIN=%ROOT%.venv\Scripts\python.exe"
rem Python's console IO speaks Unicode natively; PYTHONUTF8=1 additionally keeps
rem redirected/piped output UTF-8. No PYTHONIOENCODING here on purpose (it would
rem force byte encoding onto console streams).
set PYTHONUTF8=1

if /i "%~1"=="help"   goto :usage
if /i "%~1"=="-h"     goto :usage
if /i "%~1"=="--help" goto :usage

if not exist "%PYBIN%" (
    echo [start] Python environment is missing; running the repository setup script...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%setup.ps1"
    if errorlevel 1 exit /b %errorlevel%
)

if /i "%~1"=="all"      goto :all
if /i "%~1"=="workers"  goto :workers
goto :backend

:backend
echo [start] backend on http://127.0.0.1:8848  (health / docs / spec/status)
echo [start] human UI: Agent Office at http://127.0.0.1:4174 (separate app)
echo [start] press Ctrl+C to stop.
pushd backend
"%PYBIN%" -m app.main
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:workers
echo [start] sweeper loop (app.scheduler --loop); press Ctrl+C to stop.
pushd backend
"%PYBIN%" -m app.scheduler --loop
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:all
echo [start] opening platform-backend / platform-workers
start "platform-backend" /d "%ROOT%backend" "%PYBIN%" -m app.main
start "platform-workers" /d "%ROOT%backend" "%PYBIN%" -m app.scheduler --loop
echo [start] backend http://127.0.0.1:8848 ^| human UI (Agent Office) http://127.0.0.1:4174
exit /b 0

:usage
echo Usage: start.bat [mode]
echo.
echo   (none)     start the backend on http://127.0.0.1:8848
echo   all        open backend + sweeper loop in 2 windows
echo   workers    run the sweeper loop only (app.scheduler --loop)
echo   help       show this text
echo.
echo The human UI is the Agent Office at http://127.0.0.1:4174; start it
echo separately from its own repo.
echo.
echo Optional env knobs (see README): AP_PORT AP_DATA_DIR AP_CHECK_SPEC
echo Other loops not opened here: python -m app.alerts ^| app.liveness ^| app.budget
exit /b 0
