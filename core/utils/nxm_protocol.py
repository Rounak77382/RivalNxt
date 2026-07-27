"""Cross-platform NXM protocol registration utilities."""
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Dict, Any, Optional

def get_archive_tool_info() -> Optional[Dict[str, Any]]:
    """Get archive tool information from the Tauri frontend.
    
    Returns a dictionary with archive tool configuration or None if unavailable.
    """
    if platform.system() != "Windows":
        return None
        
    tauri_exe = get_tauri_executable()
    if not tauri_exe:
        return None
        
    try:
        # Check for an environment variable set by the Tauri frontend
        # The Tauri frontend can set RAR_TOOL_PATH environment variable
        rar_tool_path = os.environ.get('RAR_TOOL_PATH')
        if rar_tool_path and Path(rar_tool_path).exists():
            return {
                "success": True,
                "rar_tool_path": rar_tool_path,
                "message": f"Found RAR tool at: {rar_tool_path}"
            }
            
        return None
    except Exception as e:
        print(f"Failed to get archive tool info: {e}")
        return None

def get_tauri_executable() -> Optional[Path]:
    """Get the path to the current Tauri executable.
    
    In production builds, this will be the .exe in AppData/Local or wherever installed.
    In development, we'll detect the dev executable path.
    
    CRITICAL: This is called by the Python backend running INSIDE the Tauri app,
    so we need to detect the parent Tauri process executable.
    """
    if platform.system() != "Windows":
        return None
    
    # Method 1: Check if TAURI_APP_PATH env var is set (we'll set this from Tauri)
    tauri_path = os.environ.get("TAURI_APP_PATH")
    if tauri_path:
        exe_path = Path(tauri_path)
        if exe_path.exists():
            return exe_path
    
    # Method 2: Find parent process (Tauri .exe that launched this Python backend)
    try:
        import psutil
        current_process = psutil.Process()
        parent = current_process.parent()
        
        if parent and parent.name().lower().endswith('.exe'):
            parent_exe = Path(parent.exe())
            # Verify it looks like our Tauri app
            if parent_exe.exists() and any(token in parent_exe.stem.lower() for token in ('rival', 'mod')):
                return parent_exe
    except (ImportError, Exception):
        pass  # psutil not available or error
    
    # Method 3: Look for common Tauri installation paths
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    if local_app_data.exists():
        # Look for the app in Programs directory
        possible_paths = [
            local_app_data / "Programs" / "RivalNxt" / "RivalNxt.exe",
            local_app_data / "Programs" / "project-modmanager-rivals" / "project-modmanager-rivals.exe",
            local_app_data / "Programs" / "rivals-mod-manager" / "rivals-mod-manager.exe",
        ]
        
        for path in possible_paths:
            if path.exists():
                return path
    
    # Method 4: Check if running from src-tauri/target (dev build)
    # The Python backend is in src-python/, Tauri exe is in src-tauri/target/debug/ or release/
    try:
        backend_dir = Path(__file__).resolve().parents[3]  # Go up from core/utils to repo root
        for build_type in ['debug', 'release']:
            dev_exe = backend_dir / 'src-tauri' / 'target' / build_type / 'rivalnxt.exe'
            if dev_exe.exists():
                return dev_exe
    except Exception:
        pass
    
    return None


def is_nxm_registered_windows() -> bool:
    """Check if nxm:// protocol is registered in Windows registry."""
    if platform.system() != "Windows":
        return False
    
    try:
        import winreg
        key_path = r"Software\Classes\nxm\shell\open\command"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, "")
            # Check if the value exists and is non-empty
            return bool(value and value.strip())
    except (OSError, ImportError):
        return False


def register_nxm_windows(tauri_exe_path: Path) -> Dict[str, Any]:
    """Register nxm:// protocol to launch Tauri app on Windows.
    
    Args:
        tauri_exe_path: Full path to the Tauri .exe file
        
    Returns:
        Dict with 'ok' status and optional 'error' message
    """
    if platform.system() != "Windows":
        return {"ok": False, "error": "Not on Windows"}
    
    if not tauri_exe_path.exists():
        return {"ok": False, "error": f"Tauri executable not found at {tauri_exe_path}"}
    
    try:
        import winreg
        
        # Normalize path for registry (backslashes, quoted)
        exe_str = str(tauri_exe_path.resolve()).replace("/", "\\")
        
        # Create nxm protocol key
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:nxm Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        
        # Set default icon
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\DefaultIcon") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe_str}",0')
        
        # Set command to launch Tauri app with nxm:// argument
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command") as key:
            # Pass %1 (the nxm:// URL) as argument to Tauri app
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{exe_str}" "%1"')
        
        return {"ok": True}
        
    except Exception as e:
        return {"ok": False, "error": f"Registration failed: {e}"}


def unregister_nxm_windows() -> Dict[str, Any]:
    """Unregister nxm:// protocol from Windows registry.
    
    Returns:
        Dict with 'ok' status and optional 'error' message
    """
    if platform.system() != "Windows":
        return {"ok": False, "error": "Not on Windows"}
    
    try:
        import winreg
        
        # Delete the nxm protocol key and all its subkeys
        # We need to delete subkeys first, then the main key
        try:
            # Delete the command subkey
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command")
        except FileNotFoundError:
            pass  # Already deleted or never existed
        
        try:
            # Delete the open subkey
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open")
        except FileNotFoundError:
            pass
        
        try:
            # Delete the shell subkey
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell")
        except FileNotFoundError:
            pass
        
        try:
            # Delete the DefaultIcon subkey
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\DefaultIcon")
        except FileNotFoundError:
            pass
        
        try:
            # Delete the main nxm key
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm")
        except FileNotFoundError:
            pass
        
        return {"ok": True}
        
    except Exception as e:
        return {"ok": False, "error": f"Unregistration failed: {e}"}


def check_nxm_status() -> Dict[str, Any]:
    """Check the current NXM protocol registration status.
    
    Returns:
        Dict with registration status and detected Tauri path
    """
    if platform.system() != "Windows":
        return {
            "registered": False,
            "tauri_path": None,
            "registered_path": None,
            "error": "Not on Windows"
        }
    
    registered = is_nxm_registered_windows()
    tauri_path = get_tauri_executable()
    registered_path = None
    
    # Try to extract the registered path from registry for comparison
    if registered:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\nxm\shell\open\command", 0, winreg.KEY_READ) as key:
                value, _ = winreg.QueryValueEx(key, "")
                # Extract path from command (remove quotes and %1)
                if value:
                    # Simple extraction - take everything between first and last quotes
                    import re
                    match = re.search(r'"([^"]*)"', value)
                    if match:
                        registered_path = match.group(1)
        except Exception:
            pass
    
    return {
        "registered": registered,
        "tauri_path": str(tauri_path) if tauri_path else None,
        "registered_path": registered_path,
        "status": "registered" if registered else "not registered"
    }
