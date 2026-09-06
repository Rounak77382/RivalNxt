@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM build_local.bat - Full build: runs all steps in order
REM For partial rebuilds use the individual scripts:
REM   build_deps.bat    - npm install  (when package.json changes)
REM   build_rust.bat    - Maturin/PyO3 (when src-tauri/src/rust-ue-tools/ changes)
REM   build_backend.bat - PyInstaller  (when core/ or src-python/ Python changes)
REM   build_frontend.bat- Tauri+React  (when src/ or src-tauri/src/main.rs changes)
REM ============================================================================

if not exist "src-tauri" (
    echo ERROR: Run from project root.
    exit /b 1
)

echo.
echo ============================================================================
echo                    RivalNxt Complete Build Script
echo ============================================================================

echo Updating Graphify knowledge graph...
graphify update .

echo.
echo [1/4] Dependencies...
call build_deps.bat
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo.
echo [2/4] Rust PyO3 wheel...
call build_rust.bat
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo.
echo [3/4] Python backend...
call build_backend.bat
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo.
echo [4/4] Tauri frontend...
call build_frontend.bat
if %ERRORLEVEL% NEQ 0 exit /b %ERRORLEVEL%

echo.
echo ============================================================================
echo                         Full Build Complete!
echo ============================================================================
echo.
echo Tip: For faster rebuilds use the individual scripts above.