@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM build_rust.bat - Step 2: Build Rust PyO3 wheel and extract for PyInstaller
REM Run when: src-tauri/src/rust-ue-tools/ Rust source changes
REM ============================================================================

if not exist "src-tauri" (
    echo ERROR: Run from project root.
    exit /b 1
)

echo.
echo ============================================================================
echo  [rust] Building PyO3 Module with Maturin...
echo ============================================================================

cd src-tauri\src\rust-ue-tools

if not exist "Cargo.toml" ( echo ERROR: Cargo.toml not found! & cd ..\..\.. & exit /b 1 )
if not exist "pyproject.toml" ( echo ERROR: pyproject.toml not found! & cd ..\..\.. & exit /b 1 )
if exist "target\wheels" rmdir /s /q target\wheels

maturin build --release --features pyo3
if %ERRORLEVEL% NEQ 0 ( echo ERROR: Maturin build failed! & cd ..\..\.. & exit /b 1 )

for /f "delims=" %%i in ('dir /b /s /o:-d target\wheels\*.whl 2^>nul') do (
    if not defined WHEEL_PATH set WHEEL_PATH=%%i
)
if not defined WHEEL_PATH ( echo ERROR: No wheel found in target\wheels! & cd ..\..\.. & exit /b 1 )
echo [OK] Wheel: !WHEEL_PATH!

echo.
echo  [rust] Installing wheel and extracting for PyInstaller...
pip install "!WHEEL_PATH!" --force-reinstall
if %ERRORLEVEL% NEQ 0 ( echo ERROR: pip install failed! & cd ..\..\.. & exit /b 1 )

python -c "import rust_ue_tools; print('rust_ue_tools OK')"
if %ERRORLEVEL% NEQ 0 ( echo ERROR: rust_ue_tools import failed! & cd ..\..\.. & exit /b 1 )

cd ..\..\..
if exist extracted_wheel rmdir /s /q extracted_wheel
mkdir extracted_wheel

for /f "delims=" %%i in ('dir /b /s /o:-d src-tauri\src\rust-ue-tools\target\wheels\*.whl 2^>nul') do (
    if not defined WHEEL_ABS set WHEEL_ABS=%%i
)
powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory('!WHEEL_ABS!', (Resolve-Path 'extracted_wheel').Path)"

set DLL_PATH=src-tauri\src\rust-ue-tools\repak-rivals\oo2core_9_win64.dll
if not exist "%DLL_PATH%" ( echo ERROR: Oodle DLL not found at %DLL_PATH%! & exit /b 1 )
copy "%DLL_PATH%" "extracted_wheel\rust_ue_tools\" >nul

echo [OK] Rust PyO3 wheel built and extracted to extracted_wheel\