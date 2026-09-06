@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM build_frontend.bat - Step 4: Build Tauri app (React frontend + Rust shell)
REM Run when: src/ (React/TS) or src-tauri/src/main.rs / Cargo.toml changes
REM Requires: sidecar already copied (run build_backend.bat first if Python changed)
REM ============================================================================

if not exist "src-tauri" (
    echo ERROR: Run from project root.
    exit /b 1
)

echo.
echo ============================================================================
echo  [frontend] Building Tauri application (React + Rust shell)...
echo ============================================================================

if not exist "src-tauri\sidecars\rivalnxt_backend-x86_64-pc-windows-msvc.exe" (
    echo WARNING: Backend sidecar not found. Run build_backend.bat first!
    echo          Frontend will build but the app won't have a backend.
)

for /f "delims=" %%v in ('node -p "require('./package.json').version"') do set APP_VERSION=%%v
echo Building version: !APP_VERSION!

call npm run tauri:build
if %ERRORLEVEL% NEQ 0 ( echo ERROR: Tauri build failed! & exit /b 1 )

echo.
echo ============================================================================
echo  [frontend] Build complete!
echo ============================================================================
echo.
echo Output files:
if exist src-tauri\target\release\rivalnxt.exe (
    for %%A in (src-tauri\target\release\rivalnxt.exe) do (
        set /a size_mb=%%~zA/1048576
        echo   rivalnxt.exe: !size_mb! MB
    )
)
if exist "src-tauri\target\release\bundle\nsis\RivalNxt_!APP_VERSION!_x64-setup.exe" (
    for %%A in ("src-tauri\target\release\bundle\nsis\RivalNxt_!APP_VERSION!_x64-setup.exe") do (
        set /a size_mb=%%~zA/1048576
        echo   RivalNxt_!APP_VERSION!_x64-setup.exe: !size_mb! MB
    )
)
echo.
echo Build completed at %date% %time%