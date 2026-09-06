@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM build_backend.bat - Step 3: Build Python backend exe and copy to sidecar
REM Run when: core/, src-python/, or any .py file changes
REM Requires: extracted_wheel\ to exist (run build_rust.bat first if Rust changed)
REM ============================================================================

if not exist "src-tauri" (
    echo ERROR: Run from project root.
    exit /b 1
)

echo.
echo ============================================================================
echo  [backend] Building Python backend with PyInstaller...
echo ============================================================================

if not exist "extracted_wheel" (
    echo WARNING: extracted_wheel\ not found. Run build_rust.bat first if you changed Rust code.
    echo Continuing anyway - PyInstaller may fail if rust_ue_tools is not installed...
)

echo Cleaning previous dist/build...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

python -m PyInstaller --noconfirm --clean rivalnxt_backend_merged.spec
if %ERRORLEVEL% NEQ 0 ( echo ERROR: PyInstaller build failed! & exit /b 1 )

if not exist dist\rivalnxt_backend.exe ( echo ERROR: Backend exe not found in dist\! & exit /b 1 )
echo [OK] Python backend built: dist\rivalnxt_backend.exe

echo.
echo ============================================================================
echo  [backend] Copying sidecar to src-tauri\sidecars\...
echo ============================================================================

if not exist src-tauri\sidecars mkdir src-tauri\sidecars
copy /Y dist\rivalnxt_backend.exe src-tauri\sidecars\rivalnxt_backend-x86_64-pc-windows-msvc.exe >nul
if %ERRORLEVEL% NEQ 0 ( echo ERROR: Copy to sidecars failed! & exit /b 1 )

echo [OK] Sidecar updated: src-tauri\sidecars\rivalnxt_backend-x86_64-pc-windows-msvc.exe

for %%A in (dist\rivalnxt_backend.exe) do (
    set /a size_mb=%%~zA/1048576
    echo     Size: !size_mb! MB
)