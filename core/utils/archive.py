from __future__ import annotations


import os
import shutil
import tempfile
import unicodedata
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

# Third-party archive libraries
import py7zr
import rarfile

from core.config.settings import SETTINGS

# Configure rarfile to use WinRAR if available
def _configure_rarfile() -> None:
    """Configure rarfile to use the appropriate RAR tool."""
    try:
        # First check if we have a custom RAR tool path in settings
        if hasattr(SETTINGS, 'rar_tool_path') and SETTINGS.rar_tool_path:
            rarfile.UNRAR_TOOL = SETTINGS.rar_tool_path
            print(f"Using custom RAR tool: {SETTINGS.rar_tool_path}")
            return
        
        # Check environment variable
        env_rar_tool = os.environ.get('RAR_TOOL_PATH')
        if env_rar_tool and Path(env_rar_tool).exists():
            rarfile.UNRAR_TOOL = env_rar_tool
            print(f"Using RAR tool from environment: {env_rar_tool}")
        
        # Try to get archive tool info from Tauri frontend
        try:
            from core.utils.nxm_protocol import get_archive_tool_info
            archive_info = get_archive_tool_info()
            if archive_info and archive_info.get('success'):
                rar_tool_path = archive_info.get('rar_tool_path')
                if rar_tool_path and Path(rar_tool_path).exists():
                    rarfile.UNRAR_TOOL = rar_tool_path
                    print(f"Using RAR tool from Tauri: {rar_tool_path}")
                    return
        except ImportError:
            pass  # nxm_protocol not available
            
        # Try common WinRAR locations
        winrar_paths = [
            r"C:\Program Files\WinRAR\rar.exe",
            r"C:\Program Files (x86)\WinRAR\rar.exe",
            r"C:\WinRAR\rar.exe",
        ]
        
        for path in winrar_paths:
            if Path(path).exists():
                rarfile.UNRAR_TOOL = path
                print(f"Using WinRAR at: {path}")
                return
        
        # Check if rar.exe is in PATH
        rar_exe = shutil.which('rar.exe') or shutil.which('rar')
        if rar_exe:
            rarfile.UNRAR_TOOL = rar_exe
            print(f"Using RAR tool from PATH: {rar_exe}")
            return
            
        print("Warning: No RAR tool found, RAR extraction may not work")
        
    except Exception as e:
        print(f"Warning: Failed to configure rarfile: {e}")


# Configure rarfile on module import
_configure_rarfile()


def _archive_type(archive_path: str) -> str:
    lower = archive_path.lower()
    if lower.endswith(".zip"):
        return "zip"
    if lower.endswith(".7z"):
        return "7z"
    if lower.endswith(".rar"):
        return "rar"
    return "unknown"


def list_entries(archive_path: str) -> List[str]:
    typ = _archive_type(archive_path)
    if typ == "zip":
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                return [zi.filename for zi in zf.infolist() if not zi.is_dir()]
        except Exception as e:
            raise RuntimeError(f"zip list failed: {e}")
    elif typ == "7z":
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as zf:
                # Filter out directory entries to match zip/rar behavior
                return [
                    fileinfo.filename
                    for fileinfo in zf.list()
                    if not getattr(fileinfo, 'is_directory', False)
                ]
        except Exception as e:
            raise RuntimeError(f"7z list failed: {e}")
    elif typ == "rar":
        try:
            with rarfile.RarFile(archive_path, "r") as rf:
                return [f.filename for f in rf.infolist() if not f.is_dir()]
        except Exception as e:
            # Reconfigure rarfile and retry
            _configure_rarfile()
            try:
                with rarfile.RarFile(archive_path, "r") as rf:
                    return [f.filename for f in rf.infolist() if not f.is_dir()]
            except Exception as e2:
                raise RuntimeError(f"rar list failed after reconfiguration: {e2}")
    else:
        raise RuntimeError(f"Unsupported archive type for listing: {archive_path}")

def extract_archive(archive_path: str, dest_dir: str) -> List[str]:
    """Extract entire archive into dest_dir and return list of extracted file paths (relative to dest_dir)."""
    Path(dest_dir).mkdir(parents=True, exist_ok=True)
    typ = _archive_type(archive_path)
    extracted: List[str] = []
    if typ == "zip":
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(dest_dir)
                for zi in zf.infolist():
                    if not zi.is_dir():
                        extracted.append(zi.filename)
            return extracted
        except Exception as e:
            raise RuntimeError(f"zip extract failed: {e}")
    elif typ == "7z":
        try:
            with py7zr.SevenZipFile(archive_path, mode="r") as zf:
                zf.extractall(path=dest_dir)
                for name in zf.getnames():
                    extracted.append(name)
            return extracted
        except Exception as e:
            raise RuntimeError(f"7z extract failed: {e}")

    elif typ == "rar":
        try:
            with rarfile.RarFile(archive_path, "r") as rf:
                rf.extractall(dest_dir)
                for f in rf.infolist():
                    if not f.is_dir():
                        extracted.append(f.filename)
            return extracted
        except Exception as e:
            # Reconfigure rarfile and retry
            _configure_rarfile()
            try:
                with rarfile.RarFile(archive_path, "r") as rf:
                    rf.extractall(dest_dir)
                    for f in rf.infolist():
                        if not f.is_dir():
                            extracted.append(f.filename)
                return extracted
            except Exception as e2:
                raise RuntimeError(f"rar extract failed after reconfiguration: {e2}")
        except Exception as e:
            raise RuntimeError(f"rar extract failed: {e}")
    else:
        raise RuntimeError(f"Unsupported archive type for extraction: {archive_path}")


def _alias_variants(value: str) -> Set[str]:
    variants: Set[str] = {value}
    transforms = (
        lambda v: v.encode("cp437", errors="ignore").decode("cp1252", errors="ignore"),
        lambda v: v.encode("cp1252", errors="ignore").decode("cp437", errors="ignore"),
    )
    for transform in transforms:
        try:
            alias = transform(value)
        except Exception:
            continue
        if alias and alias not in variants:
            variants.add(alias)
    return variants


def _normalize_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    lowered = stripped.replace("\\", "/").lower()
    try:
        lowered = lowered.encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return lowered


def _candidate_lookup_keys(value: str) -> Set[str]:
    keys: Set[str] = set()
    for variant in _alias_variants(value):
        if variant:
            keys.add(variant.lower())
            norm = _normalize_key(variant)
            if norm:
                keys.add(norm)
    norm_original = _normalize_key(value)
    if norm_original:
        keys.add(norm_original)
    base_lower = value.lower()
    if base_lower:
        keys.add(base_lower)
    keys.discard("")
    return keys


def build_entry_lookup(entries: Iterable[str]) -> Dict[str, str]:
    lookup: Dict[str, str] = {}
    for entry in entries:
        # 1. Always map exact relative path
        exact = entry.replace("\\", "/").lower()
        lookup[exact] = entry
        
        # 2. Fallbacks based on basename
        base = os.path.basename(entry)
        if not base:
            continue
        for key in _candidate_lookup_keys(base):
            lookup.setdefault(key, entry)
    return lookup


def resolve_entry(lookup: Dict[str, str], desired: str) -> Optional[str]:
    # 1. Try exact match first
    exact = desired.replace("\\", "/").lower()
    if exact in lookup:
        return lookup[exact]

    # 2. Fallback to basename lookup (for companions or when full path is not available)
    base = os.path.basename(desired)
    if not base:
        return None
    for key in _candidate_lookup_keys(base):
        entry = lookup.get(key)
        if entry:
            return entry
    return None


def _cleanup_empty_parents(child: Path, stop: Path) -> None:
    """Remove empty directories from child up to (but not including) stop."""
    current = child
    while current != stop and current.parent != current:
        try:
            if current.is_dir() and not any(current.iterdir()):
                current.rmdir()
            else:
                break
        except Exception:
            break
        current = current.parent


def extract_member(archive_path: str, member_path: str, dest_path: str) -> None:
    """Extract a single member to dest_path. Works for zip, 7z, and rar using pure Python libs."""
    dstdir = Path(dest_path).parent
    dstdir.mkdir(parents=True, exist_ok=True)
    typ = _archive_type(archive_path)
    target_base = os.path.basename(member_path)
    if typ == "zip":
        with zipfile.ZipFile(archive_path, "r") as zf:
            member = None
            target_lower = member_path.lower()
            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                if zi.filename.lower() == target_lower or os.path.basename(zi.filename).lower() == os.path.basename(target_lower):
                    member = zi.filename
                    break
            if not member:
                raise RuntimeError("member not found in zip")
            with zf.open(member, 'r') as src, open(dest_path, 'wb') as dst:
                shutil.copyfileobj(src, dst)
            return
    elif typ == "7z":
        with py7zr.SevenZipFile(archive_path, mode="r") as zf:
            names = zf.getnames()
            member = None
            for name in names:
                if name.lower() == member_path.lower() or os.path.basename(name).lower() == target_base.lower():
                    member = name
                    break
            if not member:
                raise RuntimeError("member not found in 7z")
            zf.extract(targets=[member], path=dstdir)
            src_path = dstdir / member
            if not src_path.exists():
                raise RuntimeError("extracted member not found in 7z")
            shutil.move(str(src_path), dest_path)
            # Clean up empty intermediate directories created by archive extraction
            _cleanup_empty_parents(src_path.parent, dstdir)
            return
    elif typ == "rar":
        with rarfile.RarFile(archive_path, "r") as rf:
            member = None
            try:
                for f in rf.infolist():
                    if f.filename.lower() == member_path.lower() or os.path.basename(f.filename).lower() == target_base.lower():
                        member = f.filename
                        break
                if not member:
                    raise RuntimeError("member not found in rar")
                rf.extract(member, dstdir)
                src_path = dstdir / member
                if not src_path.exists():
                    raise RuntimeError("extracted member not found in rar")
                shutil.move(str(src_path), dest_path)
                # Clean up empty intermediate directories created by archive extraction
                _cleanup_empty_parents(src_path.parent, dstdir)
                return
            except Exception as e:
                # Reconfigure rarfile and retry
                _configure_rarfile()
                try:
                    with rarfile.RarFile(archive_path, "r") as rf:
                        for f in rf.infolist():
                            if f.filename.lower() == member_path.lower() or os.path.basename(f.filename).lower() == target_base.lower():
                                member = f.filename
                                break
                        if not member:
                            raise RuntimeError("member not found in rar after reconfiguration")
                        rf.extract(member, dstdir)
                        src_path = dstdir / member
                        if not src_path.exists():
                            raise RuntimeError("extracted member not found in rar after reconfiguration")
                        shutil.move(str(src_path), dest_path)
                        # Clean up empty intermediate directories created by archive extraction
                        _cleanup_empty_parents(src_path.parent, dstdir)
                        return
                except Exception as e2:
                    raise RuntimeError(f"rar member extraction failed after reconfiguration: {e2}")
    else:
        raise RuntimeError(f"Unsupported archive type for member extraction: {archive_path}")
