@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM build_deps.bat - Step 1: Install npm dependencies
REM Run when: package.json or package-lock.json changed
REM ============================================================================

if not exist "src-tauri" (
    echo ERROR: Run from project root.
    exit /b 1
)

echo.
echo ============================================================================
echo  [deps] Installing npm dependencies...
echo ============================================================================

if not exist "node_modules" (
    call npm install
    if %ERRORLEVEL% NEQ 0 ( echo ERROR: npm install failed! ^& exit /b 1 )
    echo [OK] npm dependencies installed
) else (
    echo [OK] node_modules already exists, skipping
)

