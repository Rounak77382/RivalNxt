from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import shutil

# Ensure repo root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.db import get_connection, init_schema, rebuild_conflicts, update_local_download_active_paks
from core.ingestion.scan_active_mods import main as scan_active_main
from core.utils.archive import build_entry_lookup, list_entries, extract_member, resolve_entry


def _load_env_mods_folder() -> Path:
    """Resolve the ~mods folder from MARVEL_RIVALS_ROOT (env or .env)."""
    root = os.environ.get("MARVEL_RIVALS_ROOT")
    if not root:
        env_path = ROOT / ".env"
        try:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    k = k.strip(); v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
                root = os.environ.get("MARVEL_RIVALS_ROOT")
        except Exception:
            pass
    if not root:
        raise SystemExit("MARVEL_RIVALS_ROOT is not set. Configure it in environment or .env")
    from core.config.settings import get_mods_dir
    mods_dir = get_mods_dir(root)
    mods_dir.mkdir(parents=True, exist_ok=True)
    return mods_dir


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Activate pak files for a local download (or by name), mirror into ~mods, rescan, and refresh conflicts.")
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--download-id", type=int, help="local_downloads.id")
    target.add_argument("--name", type=str, help="local_downloads.name")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="Activate all .pak files in this download's contents")
    mode.add_argument("--paks", nargs="+", help="Specific pak filenames to activate, e.g. Foo_P.pak Bar_P.pak")
    p.add_argument("--db", dest="db_path", default=None, help="Optional path to DB file (defaults to mods.db)")
    return p.parse_args(argv)


def _resolve_source_path(root_hint: str | None, rel_or_abs: str) -> str:
    # If absolute, return as-is; else, join with MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT (or legacy MARVEL_RIVALS_MODS_ROOT) or downloads/ under repo
    p = Path(rel_or_abs)
    if p.is_absolute():
        return str(p)
    mods_root_env = root_hint or os.environ.get("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT") or os.environ.get("MARVEL_RIVALS_MODS_ROOT")
    if not mods_root_env:
        env_path = ROOT / ".env"
        try:
            if env_path.exists():
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() in ("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT", "MARVEL_RIVALS_MODS_ROOT"):
                        mods_root_env = v.strip().strip('"').strip("'")
                        break
        except Exception:
            pass
    if not mods_root_env:
        mods_root_env = str(ROOT / "downloads")
    return str((Path(mods_root_env) / rel_or_abs).resolve())


def main(argv=None) -> int:
    args = parse_args(argv)
    conn = get_connection(args.db_path)
    try:
        init_schema(conn)
        cur = conn.cursor()
        row = None
        if args.download_id is not None:
            row = cur.execute("SELECT id, name, path, contents FROM local_downloads WHERE id=?", (args.download_id,)).fetchone()
        else:
            row = cur.execute("SELECT id, name, path, contents FROM local_downloads WHERE name=? ORDER BY id DESC LIMIT 1", (args.name,)).fetchone()
        if not row:
            print("local_download not found")
            return 2
        dl_id, name, rel_path, contents_json = row
        try:
            contents = json.loads(contents_json) if contents_json else []
            if not isinstance(contents, list):
                contents = []
        except Exception:
            contents = []
        # Determine desired paks
        if args.all:
            desired = [os.path.basename(c) for c in contents if isinstance(c, str) and c.lower().endswith('.pak')]
        else:
            desired = [os.path.basename(x) for x in (args.paks or [])]
        if not desired:
            print("Nothing to activate")
            return 0
        # Resolve source path
        full_path = _resolve_source_path(None, rel_path or "")
        lower = full_path.lower()
        mods_dir = _load_env_mods_folder()
        copied: list[str] = []
        if lower.endswith(('.zip', '.rar', '.7z')):
            entries = list_entries(full_path)
            lookup = build_entry_lookup(entries)
            for pak in desired:
                dest = mods_dir / pak
                if dest.exists():
                    continue
                member = resolve_entry(lookup, pak)
                if not member:
                    print(f"Warning: {pak} not found in archive; skipping")
                    continue
                extract_member(full_path, member, str(dest))
                copied.append(pak)
        elif lower.endswith('.pak'):
            base = os.path.basename(full_path)
            if base in desired:
                dest = mods_dir / base
                if not dest.exists():
                    shutil.copy2(full_path, dest)
                    copied.append(base)
        else:
            print("Unsupported source type; use .zip/.rar/.7z/.pak")
            return 3
        # Update DB active_paks = union(previous, copied) or desired if selecting subset
        prev_row = cur.execute("SELECT active_paks FROM local_downloads WHERE id=?", (dl_id,)).fetchone()
        prev = []
        if prev_row and prev_row[0]:
            try:
                prev = json.loads(prev_row[0])
            except Exception:
                prev = []
        new_active = list({*prev, *copied}) if copied else prev
        update_local_download_active_paks(conn, dl_id, new_active)
        conn.commit()
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # Rescan active and rebuild conflicts
    scan_active_main([])
    conn = get_connection(args.db_path)
    try:
        init_schema(conn)
        rebuild_conflicts(conn, active_only=1)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    print(f"Activated: {copied if copied else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
