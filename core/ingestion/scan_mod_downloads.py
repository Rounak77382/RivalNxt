import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure project root on path when executed directly
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config import settings
from core.config.settings import AppSettings
from core.db import get_connection, init_schema, replace_local_downloads
from core.utils.mod_filename import parse_mod_filename_to_row
from core.utils.download_paths import normalize_download_path
from core.utils.archive import list_entries
from scripts.sync_nexus_to_db import sync_mods

DEFAULT_DOWNLOADS_ROOT_FALLBACK = Path(
    r"C:\Users\rouna\OneDrive\Documents\Marvel_Rivals_Mods\downloads"
)


def _resolve_default_downloads_root(active_settings: AppSettings) -> Path:
    configured = active_settings.marvel_rivals_local_downloads_root
    return configured if configured else DEFAULT_DOWNLOADS_ROOT_FALLBACK


def list_files_level_one_including_root(root_dir: str) -> List[tuple[str, str]]:
    """
    List files and directories at root level and recursively find mod folders.
    
    Returns:
        List of (name, relative_path) tuples for:
        - Files in root (archives, .pak files)
        - Files in immediate subdirectories (archives, .pak files)
        - Directories that directly contain .pak files (mod folders)
    """
    results: List[tuple[str, str]] = []
    root_path = Path(root_dir)
    
    # Scan root level for files
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    # Include all files (archives and .pak files)
                    results.append((entry.name, ""))
    except FileNotFoundError:
        return results
    
    # Find all directories that directly contain .pak files (recursively)
    # When pak files are nested (e.g. DownloadFolder/SubFolder/File.pak),
    # use the TOP-LEVEL folder (first subdirectory under root) as the entry,
    # not the inner subfolder. This ensures correct download naming.
    seen_top_dirs: set[str] = set()
    try:
        # Find all .pak files recursively
        for pak_file in root_path.rglob("*.pak"):
            if pak_file.is_file():
                try:
                    rel = pak_file.relative_to(root_path)
                except ValueError:
                    continue
                if not rel.parts:
                    continue
                if len(rel.parts) == 1:
                    # .pak files directly in root — handled by the file scan above
                    pass
                else:
                    # Use the first-level subdirectory as the download folder
                    top_dir = rel.parts[0]
                    if top_dir not in seen_top_dirs:
                        seen_top_dirs.add(top_dir)
                        results.append((top_dir, ""))
    except Exception:
        pass
    
    # Also scan for archive files in subdirectories (original behavior for compatibility)
    try:
        with os.scandir(root_dir) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    rel = os.path.relpath(entry.path, root_dir)
                    try:
                        with os.scandir(entry.path) as subentries:
                            for subentry in subentries:
                                if subentry.is_file(follow_symlinks=False):
                                    # Only add archives, not .pak files (those are handled above)
                                    if subentry.name.lower().endswith(('.zip', '.rar', '.7z')):
                                        results.append((subentry.name, rel))
                    except (PermissionError, FileNotFoundError):
                        continue
    except FileNotFoundError:
        pass
    
    return results


def _enumerate_archive_contents(full_path: Path) -> List[str]:
    """
    Enumerate .pak files from archives (.zip, .rar, .7z) or folders.
    
    Supports:
    - Archives: Lists .pak files inside the archive (preserving relative paths)
    - Folders: Lists .pak files in the folder (preserving relative paths from folder root)
    - Single .pak files: Returns the filename itself
    
    When files live in subfolders, the relative path is preserved using forward slashes
    (e.g. "Physics Options/Lots of Jiggle/LobbyPhysics_P.pak") so that same-named
    files in different subfolders remain distinct.
    """
    contents: List[str] = []
    lower_name = full_path.name.lower()
    
    # Handle archives
    if lower_name.endswith((".zip", ".rar", ".7z")):
        try:
            seen: set[str] = set()
            for entry in list_entries(str(full_path)):
                # Normalize to forward slashes and preserve relative path
                normalized = entry.replace("\\", "/")
                if normalized.lower().endswith(".pak") and normalized not in seen:
                    seen.add(normalized)
                    contents.append(normalized)
        except Exception:
            contents = []
    
    # Handle folders with .pak files
    elif full_path.is_dir():
        try:
            seen: set[str] = set()
            # Recursively find all .pak files in the folder
            for pak_file in full_path.rglob("*.pak"):
                if pak_file.is_file():
                    try:
                        rel = str(pak_file.relative_to(full_path)).replace("\\", "/")
                    except ValueError:
                        rel = pak_file.name
                    if rel not in seen:
                        seen.add(rel)
                        contents.append(rel)
        except Exception:
            contents = []
    
    # Handle single .pak files
    elif lower_name.endswith(".pak"):
        contents = [full_path.name]
    
    if contents:
        contents.sort()
    return contents


def build_download_row(
    full_path: Path,
    *,
    relative_to: Optional[Path] = None,
    forced_mod_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Build a download row for archives, folders, or single .pak files.
    
    Supports:
    - .zip, .rar, .7z archives containing .pak files
    - Folders containing .pak files (loose or nested)
    - Single .pak files
    """
    resolved = full_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    
    # Allow both files and directories now
    if resolved.is_dir():
        # For directories, use the folder name for parsing
        name, mod_id, version = parse_mod_filename_to_row(resolved.name)
    else:
        # For files (archives or .pak), use the filename
        name, mod_id, version = parse_mod_filename_to_row(resolved.name)
    
    if forced_mod_id is not None:
        mod_id = str(forced_mod_id)
    contents = _enumerate_archive_contents(resolved)
    try:
        created_at_iso = datetime.fromtimestamp(resolved.stat().st_mtime, timezone.utc).isoformat()
    except Exception:
        created_at_iso = None

    rel_path = resolved.name
    if relative_to is not None:
        try:
            rel_path = str(resolved.relative_to(relative_to.expanduser().resolve()))
        except ValueError:
            rel_path = resolved.name
    return {
        "name": name,
        "modID": mod_id,
        "version": version,
        "path": normalize_download_path(rel_path),
        "contents": contents,
        "active_paks": [],
        "absolute_path": str(resolved),
        "created_at": created_at_iso,
    }


def _fetch_existing_download_rows(conn) -> List[Dict[str, Any]]:
    cur = conn.cursor()
    rows: List[Dict[str, Any]] = []
    for name, mod_id, version, path, contents_json, active_paks_json in cur.execute(
        "SELECT name, mod_id, version, path, contents, active_paks FROM local_downloads ORDER BY id"
    ):
        try:
            contents = json.loads(contents_json) if contents_json else []
        except Exception:
            contents = []
        try:
            active_paks = json.loads(active_paks_json) if active_paks_json else []
        except Exception:
            active_paks = []
        rows.append(
            {
                "name": name or "",
                "modID": str(mod_id) if mod_id is not None else "",
                "version": version or "",
                "path": normalize_download_path(path or ""),
                "contents": contents,
                "active_paks": active_paks,
            }
        )
    return rows


def scan_and_ingest(root: str) -> int:
    root_path = Path(root).expanduser().resolve()
    files_with_paths = list_files_level_one_including_root(str(root_path))
    rows: List[Dict[str, Any]] = []
    for filename, relpath in sorted(files_with_paths, key=lambda t: t[0].casefold()):
        full_path = (root_path / relpath / filename) if relpath else (root_path / filename)
        try:
            row = build_download_row(full_path, relative_to=root_path)
        except (FileNotFoundError, ValueError):
            continue
        rows.append(row)
    conn = get_connection()
    init_schema(conn)
    inserted = replace_local_downloads(conn, rows)
    print(f"Ingested {inserted} local download rows directly into SQLite (root={root_path}).")
    for preview in rows[:10]:
        print(
            ",".join(
                [
                    preview.get("name", ""),
                    preview.get("modID", ""),
                    preview.get("version", ""),
                    (preview.get("contents") or [""])[0] if preview.get("contents") else "",
                ]
            )
        )
    return inserted


def ingest_single_file(
    file_path: str,
    *,
    root: Optional[str] = None,
    preserve_existing: bool = True,
    forced_mod_id: Optional[int] = None,
    sync_nexus: bool = False,
) -> int:
    resolved_file = Path(file_path).expanduser().resolve()
    if not resolved_file.exists():
        raise FileNotFoundError(resolved_file)
    root_path = Path(root).expanduser().resolve() if root else resolved_file.parent
    row = build_download_row(resolved_file, relative_to=root_path, forced_mod_id=forced_mod_id)
    conn = get_connection()
    init_schema(conn)
    rows: List[Dict[str, Any]] = []
    if preserve_existing:
        rows.extend(
            entry
            for entry in _fetch_existing_download_rows(conn)
            if entry.get("path") != row.get("path")
        )
    rows.append(row)
    inserted = replace_local_downloads(conn, rows)
    print(
        "Ingested {} local download rows directly into SQLite (file={}, preserve_existing={}).".format(
            inserted,
            resolved_file,
            preserve_existing,
        )
    )
    print(
        ",".join(
            [
                row.get("name", ""),
                row.get("modID", ""),
                row.get("version", ""),
                (row.get("contents") or [""])[0] if row.get("contents") else "",
            ]
        )
    )
    if sync_nexus:
        mod_id_val = row.get("modID")
        if mod_id_val:
            try:
                sync_mods([int(mod_id_val)])
            except Exception as exc:
                print(f"Failed to sync Nexus metadata for mod {mod_id_val}: {exc}")
        else:
            raise RuntimeError("Cannot sync with Nexus: mod id unresolved. Pass --mod-id explicitly.")
    return inserted


if __name__ == "__main__":
    active_settings = settings.reload_settings()
    default_root = _resolve_default_downloads_root(active_settings)
    parser = argparse.ArgumentParser(description="Ingest local mod downloads into SQLite.")
    parser.add_argument(
        "--root",
        dest="root",
        default=str(default_root),
        help="Root folder containing downloaded mods (default: %(default)s)",
    )
    parser.add_argument(
        "--file",
        dest="single_file",
        help="Optional single archive/pak to ingest. When provided, only this file is processed.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="When used with --file, replace existing rows instead of preserving them.",
    )
    parser.add_argument(
        "--mod-id",
        type=int,
        help="Override the parsed Nexus mod ID for the provided file.",
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="After ingestion, sync Nexus metadata for the affected mod(s).",
    )
    args = parser.parse_args()
    if args.single_file:
        ingest_single_file(
            args.single_file,
            root=args.root,
            preserve_existing=not args.replace,
            forced_mod_id=args.mod_id,
            sync_nexus=args.sync,
        )
    else:
        inserted = scan_and_ingest(args.root)
        if args.sync and inserted:
            # Collect distinct mod IDs from the freshly ingested rows
            conn = get_connection()
            try:
                mods_to_sync = []
                for (mod_id,) in conn.execute(
                    "SELECT DISTINCT mod_id FROM local_downloads WHERE mod_id IS NOT NULL ORDER BY mod_id"
                ):
                    mods_to_sync.append(int(mod_id))
                if mods_to_sync:
                    sync_mods(mods_to_sync)
            finally:
                conn.close()
