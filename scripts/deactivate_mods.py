from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.db import get_connection, init_schema, rebuild_conflicts, update_local_download_active_paks
from core.ingestion.scan_active_mods import main as scan_active_main


def _mods_folder() -> Path:
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
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    if k and k not in os.environ:
                        os.environ[k] = v
                root = os.environ.get("MARVEL_RIVALS_ROOT")
        except Exception:
            pass
    if not root:
        raise SystemExit("MARVEL_RIVALS_ROOT is not set. Configure it in environment or .env")
    from core.config.settings import get_mods_dir
    p = get_mods_dir(root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Deactivate paks for a local download (by id or name), rescan, and refresh conflicts.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--download-id", type=int, help="local_downloads.id")
    g.add_argument("--name", type=str, help="local_downloads.name")
    p.add_argument("--db", dest="db_path", default=None, help="Optional path to DB file (defaults to mods.db)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    conn = get_connection(args.db_path)
    try:
        init_schema(conn)
        cur = conn.cursor()
        if args.download_id is not None:
            row = cur.execute("SELECT id, contents FROM local_downloads WHERE id=?", (args.download_id,)).fetchone()
        else:
            row = cur.execute("SELECT id, contents FROM local_downloads WHERE name=? ORDER BY id DESC LIMIT 1", (args.name,)).fetchone()
        if not row:
            print("local_download not found")
            return 2
        dl_id, contents_json = row
        try:
            contents = json.loads(contents_json) if contents_json else []
            if not isinstance(contents, list):
                contents = []
        except Exception:
            contents = []
        pak_names = [os.path.basename(c) for c in contents if isinstance(c, str) and c.lower().endswith('.pak')]
        mods_dir = _mods_folder()
        removed: list[str] = []
        for pak in pak_names:
            fp = mods_dir / pak
            try:
                if fp.exists():
                    fp.unlink()
                    removed.append(pak)
            except Exception:
                pass
        update_local_download_active_paks(conn, dl_id, [])
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
    print(f"Deactivated and removed: {removed if removed else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
