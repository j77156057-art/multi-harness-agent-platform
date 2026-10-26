@echo off
rem Administrator-harness self-loop. Keep this window open next to the member
rem workers. The worker only acts through the administrator token; it does not
rem use a browser.
rem
rem Override any of these before running:
rem   AP_ADMIN_HARNESS_ID  default codex-window-admin
rem   AP_ADMIN_GROUP       default office-formal
rem   AP_ADMIN_TOKEN_FILE  default <data>\codex-window-admin.key
rem   AP_DATA_DIR          default the checkout's data directory
setlocal
set "ROOT=%~dp0"
rem The active platform code may live in the Codex worktree. Prefer it when it
rem actually contains the admin worker; the main checkout may be an older branch.
set "BACKEND=%ROOT%backend"
set "VENV=%ROOT%.venv"
if exist "%ROOT%.worktrees\codex\backend\app\admin\worker.py" (
    set "BACKEND=%ROOT%.worktrees\codex\backend"
    set "VENV=%ROOT%.worktrees\codex\.venv"
)
rem If this script itself lives in a worktree, the live state/tokens belong to
rem the main checkout two levels up, not to the worktree's stale data dir.
set "DATA_DIR=%ROOT%data"
echo %ROOT% | findstr /I /C:".worktrees" >nul
if not errorlevel 1 if exist "%ROOT%..\..\data\state.db" set "DATA_DIR=%ROOT%..\..\data"
if not defined AP_DATA_DIR if exist "%DATA_DIR%\state.db" set "AP_DATA_DIR=%DATA_DIR%"
cd /d "%BACKEND%"
set "PYBIN=%VENV%\Scripts\python.exe"
set PYTHONUTF8=1
rem codex is installed here but is not always on PATH; the administrator channel
rem resolves to the codex CLI, so make the documented location visible.
if exist "%LOCALAPPDATA%\OpenAI\Codex\bin" set "PATH=%LOCALAPPDATA%\OpenAI\Codex\bin;%PATH%"

if not exist "%PYBIN%" (
    echo [admin] no virtualenv at %VENV%
    echo         create it once: python -m venv .venv
    exit /b 2
)
if not defined AP_ADMIN_HARNESS_ID set "AP_ADMIN_HARNESS_ID=codex-window-admin"
if not defined AP_ADMIN_GROUP set "AP_ADMIN_GROUP=office-formal"
if not defined AP_AUTH_MODE set "AP_AUTH_MODE=enforce"
if not defined AP_ADMIN_SUBJECTS set "AP_ADMIN_SUBJECTS=%AP_ADMIN_HARNESS_ID%"
if not defined AP_ADMIN_TOKEN_FILE set "AP_ADMIN_TOKEN_FILE=%DATA_DIR%\codex-window-admin.key"
if not exist "%AP_ADMIN_TOKEN_FILE%" (
    echo [admin] token file not found: %AP_ADMIN_TOKEN_FILE%
    echo         set AP_ADMIN_TOKEN_FILE to an administrator token file
    exit /b 2
)

echo [admin] harness=%AP_ADMIN_HARNESS_ID% group=%AP_ADMIN_GROUP% data=%AP_DATA_DIR%
"%PYBIN%" -m app.admin.worker --admin-harness-id "%AP_ADMIN_HARNESS_ID%" --group "%AP_ADMIN_GROUP%" --token-file "%AP_ADMIN_TOKEN_FILE%" %*
