# PyO3-based Rust UE Tools integration (PyO3 is now mandatory)
from __future__ import annotations
import os
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional, Dict

# PyO3-based Rust UE Tools integration (mandatory)
try:
    import sys
    # Add the rust-ue-tools target directory to Python path
    rust_target_path = Path(__file__).parent.parent.parent / "src-tauri" / "src" / "rust-ue-tools" / "target" / "wheels"
    
    # Try to find the wheel file
    if rust_target_path.exists():
        for wheel_file in rust_target_path.glob("*.whl"):
            if str(rust_target_path) not in sys.path:
                sys.path.insert(0, str(rust_target_path))
            break
    
    # Try PyO3 import
    from rust_ue_tools import PyUnpacker, PyAssetPath
    
    _unpacker = PyUnpacker()
    RUST_LIBRARY_AVAILABLE = True
    print("PyO3 Rust UE Tools library loaded successfully")
    
    def extract_asset_paths_from_zip_py(zip_path: str, aes_key: Optional[str], keep_temp: bool) -> List[str]:
        """PyO3 function wrapper for extracting asset paths from ZIP"""
        try:
            result = _unpacker.extract_asset_paths_from_zip(zip_path, aes_key, keep_temp)
            return [str(asset) for asset in result]
        except Exception as e:
            print(f"PyO3 extraction failed: {e}")
            return []
            
    def extract_pak_asset_map_from_folder_py(folder_path: str, aes_key: Optional[str]) -> Dict[str, List[str]]:
        """PyO3 function wrapper for extracting pak asset map from folder"""
        try:
            result = _unpacker.extract_pak_asset_map_from_folder(folder_path, aes_key)
            
            # DEBUG: Print raw result from Rust
            print(f"[DEBUG] Rust returned {len(result)} pak entries")
            for pak_name, assets in result.items():
                print(f"[DEBUG]   {pak_name}: {len(assets)} assets")
                if len(assets) > 0:
                    print(f"[DEBUG]     Sample: {assets[:3]}")
            
            # Normalize paths to start with /Marvel/Content for consistency
            normalized_result = {}
            for pak_name, asset_paths in result.items():
                normalized_paths = []
                for asset_path in asset_paths:
                    # Filter out metadata paths like "patched_files" and "/patched_files"
                    if (asset_path == "patched_files" or asset_path.startswith("patched_files") or
                        asset_path == "/patched_files" or asset_path.startswith("/patched_files")):
                        print(f"[DEBUG] Filtered out: {asset_path}")
                        continue
                    
                    # Normalize path to start with /Marvel/Content
                    normalized_path = asset_path
                    if normalized_path.startswith("../../../"):
                        # Remove ../../../ prefix and ensure it starts with /
                        normalized_path = "/" + normalized_path[9:]
                    elif not normalized_path.startswith("/"):
                        # Add / prefix if missing
                        normalized_path = "/" + normalized_path
                    
                    normalized_paths.append(normalized_path)
                
                print(f"[DEBUG] After normalization: {pak_name} has {len(normalized_paths)} assets")
                normalized_result[pak_name] = normalized_paths
            
            return normalized_result
        except Exception as e:
            print(f"PyO3 folder extraction failed: {e}")
            import traceback
            traceback.print_exc()
            return {}
            
except ImportError as e:
    print(f"ERROR: PyO3 library not available: {e}")
    print("To fix this, build the PyO3 bindings:")
    print("   python build_rust_pyo3.py")
    raise ImportError("PyO3 Rust library is required but not available") from e

__all__ = [
    "extract_uasset_paths_from_zip",
    "extract_pak_asset_map_from_folder",
]

EXTENSIONS_TO_PRINT = {".uasset", ".umap", ".bnk", ".json", ".wem", ".fbx", ".obj", ".glb", ".gltf", ".ini", ".wav", ".mp3", ".ogg", ".uplugin", ".usf"}


def _pak_map_worker(folder: str, key: Optional[str], result_file: str) -> None:
    """Run in a child process so Rust panics don't kill the parent.

    Must be at module level so Windows multiprocessing (spawn) can pickle it.
    """
    import json as _json
    try:
        data = extract_pak_asset_map_from_folder_py(folder, key)
        Path(result_file).write_text(
            _json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        Path(result_file).write_text(
            _json.dumps({"__error__": str(exc)}, ensure_ascii=False),
            encoding="utf-8",
        )


def extract_pak_asset_map_from_folder(folder_path: str, repak_bin: Optional[str] = None, aes_key: Optional[str] = None) -> dict[str, List[str]]:
    """Return mapping of pak_name -> asset paths for content already extracted to a folder.

    This scans for classic .pak files and IoStore (.utoc + .ucas/.utac) files within the folder tree
    and enumerates contained assets, supporting both formats in a single pass.

    The Rust PyO3 library call is run in a subprocess so that if the Rust code
    panics on a corrupt file, it only kills the child process and not the entire
    backend.

    Args:
        folder_path: Path to the folder containing pak/utoc files
        repak_bin: Deprecated parameter, no longer used (PyO3 only)
        aes_key: Optional AES key for encrypted files

    Returns:
        Dict mapping pak names to lists of asset paths
    """
    import multiprocessing
    import json as _json

    result_path = Path(tempfile.mktemp(prefix="pak_map_", suffix=".json"))
    try:
        proc = multiprocessing.Process(
            target=_pak_map_worker, args=(folder_path, aes_key, str(result_path))
        )
        proc.start()
        proc.join(timeout=180)  # 3 minute timeout

        if proc.is_alive():
            print(f"[extract_pak_asset_map] Worker timed out after 180s — killing")
            proc.kill()
            proc.join(5)
            return {}

        if proc.exitcode != 0:
            print(f"[extract_pak_asset_map] Worker crashed with exit code {proc.exitcode} (likely Rust panic) — skipping")
            return {}

        if not result_path.exists():
            print("[extract_pak_asset_map] Worker produced no output — skipping")
            return {}

        raw = _json.loads(result_path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and "__error__" in raw:
            print(f"[extract_pak_asset_map] Worker error: {raw['__error__']}")
            return {}
        return raw
    except Exception as exc:
        print(f"[extract_pak_asset_map] Failed to run worker: {exc}")
        return {}
    finally:
        try:
            if result_path.exists():
                result_path.unlink()
        except Exception:
            pass

def extract_uasset_paths_from_zip(zip_path: str, repak_bin: Optional[str] = None, aes_key: Optional[str] = None, keep_temp: bool = False) -> List[str]:
    """Extract asset paths from a ZIP file using PyO3 Rust library.

    This function uses the native Rust UE Tools library via PyO3 to extract
    asset paths from ZIP files containing pak/utoc files without requiring
    external command-line tools.
    
    Args:
        zip_path: Path to the ZIP file
        repak_bin: Deprecated parameter, no longer used (PyO3 only)
        aes_key: Optional AES key for encrypted files
        keep_temp: Whether to keep temporary extraction files
    
    Returns:
        List of asset paths found in the ZIP file
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    # repak_bin parameter is ignored - PyO3 is mandatory
    try:
        # Use the Rust library to extract asset paths
        extracted_files = extract_asset_paths_from_zip_py(str(zip_path), aes_key, keep_temp)
        if extracted_files:
            print(f"Rust library found {len(extracted_files)} assets")
            return extracted_files
        else:
            print("No assets found in ZIP file")
            return []
    except Exception as e:
        print(f"Rust library extraction failed: {e}")
        return []
