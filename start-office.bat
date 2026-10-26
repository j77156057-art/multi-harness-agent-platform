@echo off
setlocal
cd /d "%~dp0office"

if not exist "node_modules\vite\bin\vite.js" (
    echo [office] dependencies are missing; installing with npm ci...
    call npm ci
    if errorlevel 1 exit /b %errorlevel%
)

if not defined OFFICE_FORMAL_UPSTREAM set "OFFICE_FORMAL_UPSTREAM=http://127.0.0.1:8848"
echo [office] Agent Office on http://127.0.0.1:4174
echo [office] upstream: %OFFICE_FORMAL_UPSTREAM%
call npm run dev
