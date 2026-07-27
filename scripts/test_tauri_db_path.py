"""
Test script to verify Tauri database path configuration.
Simulates what happens when Tauri launches the backend.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).resolve().parents[1]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

def test_tauri_environment():
    """Simulate Tauri environment and test database path."""
    print("=" * 70)
    print("TAURI DATABASE PATH TEST")
    print("=" * 70)
    
    # Simulate Tauri setting the environment variable
    # This matches what main.rs does: app_handle.path().app_data_dir()
    tauri_data_dir = (
        Path(os.environ.get("APPDATA", "~/.config")).expanduser()
        / "com.rivalnxt.modmanager"
    )
    
    print("\n1. Simulating Tauri environment:")
    print(f"   Setting MODMANAGER_DATA_DIR = {tauri_data_dir}")
    
    os.environ["MODMANAGER_DATA_DIR"] = str(tauri_data_dir)
    
    # Now import and configure settings (this is what run_server.py does)
    from core.config.settings import SETTINGS, configure
    
    print("\n2. Before configure():")
    print(f"   SETTINGS.data_dir = {SETTINGS.data_dir}")
    
    # Configure with Tauri path
    data_dir_override = os.environ.get("MODMANAGER_DATA_DIR")
    configure(data_dir=data_dir_override)
    
    print("\n3. After configure():")
    print(f"   SETTINGS.data_dir = {SETTINGS.data_dir}")
    
    # Check if paths match
    expected_path = tauri_data_dir
    actual_path = SETTINGS.data_dir
    
    print("\n4. Path verification:")
    print(f"   Expected: {expected_path}")
    print(f"   Actual:   {actual_path}")
    print(f"   Match: {'✓ YES' if str(expected_path) == str(actual_path) else '✗ NO'}")
    
    # Test database path
    from core.db.db import _data_root, DB_FILENAME
    
    db_path = _data_root() / DB_FILENAME
    print("\n5. Database path:")
    print(f"   {db_path}")
    print(f"   Exists: {db_path.exists()}")
    
    # Test directory creation
    print("\n6. Directory creation test:")
    try:
        SETTINGS.data_dir.mkdir(parents=True, exist_ok=True)
        print("   ✓ SUCCESS - Directory created/verified")
        print(f"   Writable: {os.access(SETTINGS.data_dir, os.W_OK)}")
    except Exception as e:
        print(f"   ✗ FAILED - {e}")
    
    # Test schema initialization
    print("\n7. Database schema test:")
    try:
        from core.db.db import get_connection, init_schema
        conn = get_connection()
        init_schema(conn)
        
        # Check for key tables
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tables = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        
        print("   ✓ Schema initialized successfully")
        print(f"   Tables created: {len(tables)}")
        
        key_tables = ["local_downloads", "mods", "pak_assets", "asset_conflicts"]
        missing = [t for t in key_tables if t not in tables]
        
        if missing:
            print(f"   ⚠ Missing tables: {missing}")
        else:
            print("   ✓ All key tables present")
            
    except Exception as e:
        print(f"   ✗ Schema initialization failed: {e}")
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)
    
    return str(expected_path) == str(actual_path)


if __name__ == "__main__":
    success = test_tauri_environment()
    sys.exit(0 if success else 1)
