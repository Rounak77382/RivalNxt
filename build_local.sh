#!/bin/bash
# ============================================================================
# Complete Build Script for RivalNxt
# This script builds all components from scratch for new users:
# - Installs npm dependencies
# - Builds Rust UE Tools with PyO3 bindings
# - Creates Python wrapper module
# - Builds Python backend with PyInstaller
# - Builds Tauri application (frontend + desktop app)
# ============================================================================

set -e  # Exit on error

echo ""
echo "============================================================================"
echo "                    RivalNxt Complete Build Script"
echo "============================================================================"
echo ""

# Check if we're in the correct directory
if [ ! -d "src-tauri" ]; then
    echo "ERROR: src-tauri directory not found!"
    echo "Please run this script from the project root directory."
    exit 1
fi

echo "Updating Graphify knowledge graph..."
graphify update .

# ============================================================================
echo "[1/6] Installing npm dependencies..."
echo "============================================================================"
echo "Checking for node_modules..."
if [ ! -d "node_modules" ]; then
    echo "Installing npm dependencies..."
    npm install
    echo "✓ npm dependencies installed successfully"
else
    echo "✓ node_modules already exists, skipping npm install"
fi

# ============================================================================
echo ""
echo "[2/6] Building Rust UE Tools with PyO3 bindings..."
echo "============================================================================"
cd src-tauri/src/rust-ue-tools

echo "📦 Building debug version with PyO3 bindings..."
cargo build --lib --features pyo3

echo "📦 Building release version with PyO3 bindings..."
cargo build --lib --release --features pyo3

echo "🔗 Checking Python bindings..."
# Check if the library was built correctly
if [ -f "target/debug/librust_ue_tools.so" ] || [ -f "target/debug/librust_ue_tools.dylib" ] || [ -f "target/debug/rust_ue_tools.dll" ]; then
    echo "✅ Debug library built successfully"
else
    echo "❌ Debug library not found"
    cd ../../..
    exit 1
fi

if [ -f "target/release/librust_ue_tools.so" ] || [ -f "target/release/librust_ue_tools.dylib" ] || [ -f "target/release/rust_ue_tools.dll" ]; then
    echo "✅ Release library built successfully"
else
    echo "❌ Release library not found"
    cd ../../..
    exit 1
fi

# ============================================================================
echo ""
echo "[3/6] Setting up Python bindings..."
echo "============================================================================"

# Create directory for Python libraries
mkdir -p ../../../python_libs

# Copy debug version (for development)
if [ -f "target/debug/rust_ue_tools.dll" ]; then
    cp target/debug/rust_ue_tools.dll ../../../python_libs/rust_ue_tools.dll
    echo "✅ Copied Windows debug library"
elif [ -f "target/debug/librust_ue_tools.so" ]; then
    cp target/debug/librust_ue_tools.so ../../../python_libs/rust_ue_tools.so
    echo "✅ Copied Linux debug library"
elif [ -f "target/debug/librust_ue_tools.dylib" ]; then
    cp target/debug/librust_ue_tools.dylib ../../../python_libs/rust_ue_tools.dylib
    echo "✅ Copied macOS debug library"
fi

# Copy release version (for production)
if [ -f "target/release/rust_ue_tools.dll" ]; then
    cp target/release/rust_ue_tools.dll ../../../python_libs/rust_ue_tools_release.dll
    echo "✅ Copied Windows release library"
elif [ -f "target/release/librust_ue_tools.so" ]; then
    cp target/release/librust_ue_tools.so ../../../python_libs/rust_ue_tools_release.so
    echo "✅ Copied Linux release library"
elif [ -f "target/release/librust_ue_tools.dylib" ]; then
    cp target/release/librust_ue_tools.dylib ../../../python_libs/rust_ue_tools_release.dylib
    echo "✅ Copied macOS release library"
fi

# Create a simple Python wrapper module
cat > ../../../python_libs/rust_ue_tools.py << 'EOF'
"""
Python wrapper for rust-ue-tools library
This module provides access to the Rust implementation of UE file operations
"""

import os
import sys
import platform
from pathlib import Path

# Add current directory to path so we can import the shared library
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

# Try to load the shared library
_lib = None
_lib_name = None

system = platform.system()
if system == "Windows":
    # Try debug version first, then release
    for lib_name in ["rust_ue_tools.dll", "rust_ue_tools_release.dll"]:
        try:
            _lib = __import__(lib_name.rsplit(".", 1)[0])
            _lib_name = lib_name
            break
        except (ImportError, OSError):
            continue
elif system == "Darwin":  # macOS
    for lib_name in ["rust_ue_tools.dylib", "rust_ue_tools_release.dylib"]:
        try:
            _lib = __import__(lib_name.rsplit(".", 1)[0])
            _lib_name = lib_name
            break
        except (ImportError, OSError):
            continue
else:  # Linux and others
    for lib_name in ["rust_ue_tools.so", "rust_ue_tools_release.so"]:
        try:
            _lib = __import__(lib_name.rsplit(".", 1)[0])
            _lib_name = lib_name
            break
        except (ImportError, OSError):
            continue

if _lib is None:
    print(f"Warning: Could not load rust-ue-tools library")
    print("Falling back to external tools (repak.exe, retoc_cli.exe)")
else:
    print(f"✅ Loaded rust-ue-tools from {_lib_name}")

# Import the functions from the Rust library
try:
    extract_asset_paths_from_zip_py = getattr(_lib, 'extract_asset_paths_from_zip_py')
    extract_pak_asset_map_from_folder_py = getattr(_lib, 'extract_pak_asset_map_from_folder_py')
    free_c_string = getattr(_lib, 'free_c_string')
except AttributeError as e:
    print(f"Warning: Could not import required functions: {e}")
    extract_asset_paths_from_zip_py = None
    extract_pak_asset_map_from_folder_py = None
    free_c_string = None

__all__ = [
    'extract_asset_paths_from_zip_py',
    'extract_pak_asset_map_from_folder_py',
    'free_c_string'
]
EOF

echo "✅ Created Python wrapper module"

# Return to project root
cd ../../..

# ============================================================================
echo ""
echo "[4/6] Building Python backend with PyInstaller..."
echo "============================================================================"
echo "Cleaning previous builds..."
rm -rf dist build

echo "Building backend executable using spec file..."
python -m PyInstaller --noconfirm --clean rivalnxt_backend_merged.spec

if [ ! -f "dist/rivalnxt_backend" ]; then
    echo "ERROR: Backend executable not found in dist directory!"
    exit 1
fi
echo "✓ Python backend built successfully"

# ============================================================================
echo ""
echo "[5/6] Copying backend to Tauri sidecars..."
echo "============================================================================"
mkdir -p src-tauri/sidecars

# Determine the platform-specific sidecar name
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    SIDECAR_NAME="rivalnxt_backend-x86_64-unknown-linux-gnu"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    SIDECAR_NAME="rivalnxt_backend-aarch64-apple-darwin"
else
    SIDECAR_NAME="rivalnxt_backend-x86_64-unknown-linux-gnu"
fi

cp dist/rivalnxt_backend "src-tauri/sidecars/$SIDECAR_NAME"
chmod +x "src-tauri/sidecars/$SIDECAR_NAME"
echo "✓ Backend copied to sidecars directory as $SIDECAR_NAME"

# ============================================================================
echo ""
echo "[6/6] Building Tauri application..."
echo "============================================================================"
echo "This will build the frontend and Tauri application..."
npm run tauri:build
echo "✓ Tauri application built successfully"

# ============================================================================
echo ""
echo "============================================================================"
echo "                         Build Complete!"
echo "============================================================================"
echo ""
echo "Generated files:"
echo "  - Python Backend:  dist/rivalnxt_backend"
echo "  - Tauri App:       src-tauri/target/release/rivalnxt"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "  - AppImage:        src-tauri/target/release/bundle/appimage/"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  - DMG:             src-tauri/target/release/bundle/dmg/"
fi
echo ""
echo "============================================================================"

# Display file sizes
echo ""
echo "File sizes:"
if [ -f "dist/rivalnxt_backend" ]; then
    ls -lh dist/rivalnxt_backend | awk '{print "  rivalnxt_backend: " $5}'
fi
if [ -f "src-tauri/target/release/rivalnxt" ]; then
    ls -lh src-tauri/target/release/rivalnxt | awk '{print "  rivalnxt: " $5}'
fi

echo ""
echo "Build completed successfully at $(date)"
echo ""
echo "📋 What was built:"
echo "  1. Rust UE Tools library with PyO3 bindings"
echo "  2. Python wrapper module for Rust library"
echo "  3. Python backend executable (PyInstaller)"
echo "  4. Tauri desktop application"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    echo "  5. AppImage for Linux"
elif [[ "$OSTYPE" == "darwin"* ]]; then
    echo "  5. DMG installer for macOS"
fi
echo ""