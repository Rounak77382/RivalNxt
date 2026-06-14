@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM Complete Build Script for RivalNxt
REM This script builds all components from scratch for new users:
REM - Installs npm dependencies
REM - Builds Rust UE Tools with PyO3 bindings
REM - Creates Python wrapper module
REM - Builds Python backend with PyInstaller
REM - Builds Tauri application (frontend + desktop app)
REM ============================================================================

echo.
echo ============================================================================
echo                    RivalNxt Complete Build Script
echo ============================================================================
echo.

REM Check if we're in the correct directory
if not exist "src-tauri" (
    echo ERROR: src-tauri directory not found!
    echo Please run this script from the project root directory.
    exit /b 1
)

echo Updating Graphify knowledge graph...
graphify update .

REM Get version from package.json
echo.
echo Detecting version...
for /f "delims=" %%v in ('node -p "require('./package.json').version"') do set APP_VERSION=%%v
echo Detected version: !APP_VERSION!

REM ============================================================================
echo [1/6] Installing npm dependencies...
echo ============================================================================
echo Checking for node_modules...
if not exist "node_modules" (
    echo Installing npm dependencies...
    call npm install
    if %ERRORLEVEL% NEQ 0 (
        echo ERROR: npm install failed!
        exit /b 1
    )
    echo ✓ npm dependencies installed successfully
) else (
    echo ✓ node_modules already exists, skipping npm install
)

REM ============================================================================
echo.
echo [2/6] Building PyO3 Module with Maturin...
echo ============================================================================
cd src-tauri\src\rust-ue-tools

echo 📦 Building Python wheel with Maturin...
echo Current directory: %cd%

REM Check if required files exist
if not exist "Cargo.toml" (
    echo ❌ Cargo.toml not found in current directory!
    cd ..\..\..
    exit /b 1
)

if not exist "pyproject.toml" (
    echo ❌ pyproject.toml not found in current directory!
    cd ..\..\..
    exit /b 1
)

echo ✅ Cargo.toml and pyproject.toml found

REM Check workspace members
if exist "repak-rivals" (
    echo ✅ repak-rivals submodule found
) else (
    echo ❌ repak-rivals submodule not found! Git submodules may not be initialized.
    echo Run: git submodule update --init --recursive
    cd ..\..\..
    exit /b 1
)

REM Build using Maturin
echo Building wheel with --release --features pyo3...
maturin build --release --features pyo3
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Maturin build failed
    cd ..\..\..
    exit /b 1
)

echo Finding built wheel...
for /f "delims=" %%i in ('dir /b /s target\wheels\*.whl 2^>nul ^| findstr /r ".*"') do set WHEEL_PATH=%%i

if not defined WHEEL_PATH (
    echo ❌ No wheel file found in target\wheels!
    cd ..\..\..
    exit /b 1
)

echo ✅ Found wheel: %WHEEL_PATH%

REM ============================================================================
echo.
echo [3/6] Installing and extracting wheel for PyInstaller...
echo ============================================================================

echo Installing wheel...
pip install "%WHEEL_PATH%" --force-reinstall
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Failed to install wheel
    cd ..\..\..
    exit /b 1
)

echo Verifying installation...
python -c "import rust_ue_tools; print('rust_ue_tools imported successfully!')"
if %ERRORLEVEL% NEQ 0 (
    echo ❌ Failed to import rust_ue_tools module!
    cd ..\..\..
    exit /b 1
)

echo Extracting wheel for PyInstaller bundling...
cd ..\..\..
if exist extracted_wheel rmdir /s /q extracted_wheel
mkdir extracted_wheel

REM Extract wheel using PowerShell
powershell -Command "Add-Type -AssemblyName System.IO.Compression.FileSystem; [System.IO.Compression.ZipFile]::ExtractToDirectory('%WHEEL_PATH%', 'extracted_wheel')"

REM Manually copy Oodle DLL to the extracted package
echo Copying Oodle DLL...
set DLL_PATH=src-tauri\src\rust-ue-tools\repak-rivals\oo2core_9_win64.dll
if not exist "%DLL_PATH%" (
    echo ❌ Oodle DLL not found at: %DLL_PATH%
    exit /b 1
)
copy "%DLL_PATH%" "extracted_wheel\rust_ue_tools\" >nul

echo ✅ Wheel extracted successfully to extracted_wheel\
dir extracted_wheel
dir extracted_wheel\rust_ue_tools.

REM ============================================================================
echo.
echo [4/6] Building Python backend with PyInstaller...
echo ============================================================================
echo Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build

echo Building backend executable using spec file...
python -m PyInstaller --noconfirm --clean rivalnxt_backend_merged.spec
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Python backend build failed!
    exit /b 1
)

if not exist dist\rivalnxt_backend.exe (
    echo ERROR: Backend executable not found in dist directory!
    exit /b 1
)
echo ✓ Python backend built successfully

REM ============================================================================
echo.
echo [5/6] Copying backend to Tauri sidecars...
echo ============================================================================
if not exist src-tauri\sidecars mkdir src-tauri\sidecars
copy /Y dist\rivalnxt_backend.exe src-tauri\sidecars\rivalnxt_backend-x86_64-pc-windows-msvc.exe >nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Failed to copy backend executable!
    exit /b 1
)
echo ✓ Backend copied to sidecars directory

REM ============================================================================
echo.
echo [6/6] Building Tauri application...
echo ============================================================================
echo This will build the frontend and Tauri application...
call npm run tauri:build
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Tauri build failed!
    exit /b 1
)
echo ✓ Tauri application built successfully

REM ============================================================================
echo.
echo ============================================================================
echo                         Build Complete!
echo ============================================================================
echo.
echo Generated files:
echo   - Python Backend:  dist\rivalnxt_backend.exe
echo   - Tauri App:       src-tauri\target\release\rivalnxt.exe
echo   - NSIS Installer:  src-tauri\target\release\bundle\nsis\RivalNxt_!APP_VERSION!_x64-setup.exe
echo.
echo ============================================================================

REM Display file sizes
echo.
echo File sizes:
if exist dist\rivalnxt_backend.exe (
    for %%A in (dist\rivalnxt_backend.exe) do (
        set /a size_mb=%%~zA/1048576
        echo   rivalnxt_backend.exe: !size_mb! MB
    )
)
if exist src-tauri\target\release\rivalnxt.exe (
    for %%A in (src-tauri\target\release\rivalnxt.exe) do (
        set /a size_mb=%%~zA/1048576
        echo   rivalnxt.exe: !size_mb! MB
    )
)
if exist src-tauri\target\release\bundle\nsis\RivalNxt_!APP_VERSION!_x64-setup.exe (
    for %%A in (src-tauri\target\release\bundle\nsis\RivalNxt_!APP_VERSION!_x64-setup.exe) do (
        set /a size_mb=%%~zA/1048576
        echo   RivalNxt_!APP_VERSION!_x64-setup.exe: !size_mb! MB
    )
)

echo.
echo Build completed successfully at %date% %time%
echo.
echo 📋 What was built:
echo   1. Rust UE Tools library with PyO3 bindings
echo   2. Python wrapper module for Rust library
echo   3. Python backend executable (PyInstaller)
echo   4. Tauri desktop application
echo   5. NSIS installer for Windows
echo.