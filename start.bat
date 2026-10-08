@echo off
rem One-click launcher for the agent collaboration platform (Windows cmd).
rem ASCII-only on purpose: cmd parses .bat files with the active code page, so
rem non-ASCII text here would garble before any chcp could take effect.
rem
rem Modes:
rem   start.bat            backend only  (http://127.0.0.1:8848)
rem   start.bat all        backend + sweeper loop + frontend  (3 windows)
rem   start.bat workers    sweeper loop only  (app.scheduler --loop)
rem   start.bat frontend   vite dev server only  (npm run dev)
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
    echo [start] no virtualenv at %ROOT%.venv - create it once:
    echo           python -m venv .venv
    echo           .venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
    exit /b 2
)

if /i "%~1"=="all"      goto :all
if /i "%~1"=="workers"  goto :workers
if /i "%~1"=="frontend" goto :frontend
goto :backend

:backend
echo [start] backend on http://127.0.0.1:8848  (health / docs / spec/status)
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

:frontend
if not exist "%ROOT%frontend\node_modules" (
    echo [start] frontend dependencies missing - run once:
    echo           cd frontend
    echo           npm install --registry=https://registry.npmmirror.com
    exit /b 2
)
echo [start] frontend on http://localhost:5173  (proxies /api to 127.0.0.1:8848)
pushd frontend
call npm run dev
set "RC=%ERRORLEVEL%"
popd
exit /b %RC%

:all
if not exist "%ROOT%frontend\node_modules" (
    echo [start] WARNING: frontend deps missing - that window will fail.
    echo         fix once: cd frontend ^&^& npm install --registry=https://registry.npmmirror.com
)
echo [start] opening platform-backend / platform-workers / platform-frontend
start "platform-backend" /d "%ROOT%backend" "%PYBIN%" -m app.main
start "platform-workers" /d "%ROOT%backend" "%PYBIN%" -m app.scheduler --loop
start "platform-frontend" /d "%ROOT%frontend" cmd /k "npm run dev"
echo [start] backend http://127.0.0.1:8848 ^| frontend http://localhost:5173
exit /b 0

:usage
echo Usage: start.bat [mode]
echo.
echo   (none)     start the backend on http://127.0.0.1:8848
echo   all        open backend + sweeper loop + frontend in 3 windows
echo   workers    run the sweeper loop only (app.scheduler --loop)
echo   frontend   run the vite dev server only (npm run dev)
echo   help       show this text
echo.
echo Optional env knobs (see README): AP_PORT AP_DATA_DIR AP_CHECK_SPEC
echo Other loops not opened here: python -m app.alerts ^| app.liveness ^| app.budget
exit /b 0
