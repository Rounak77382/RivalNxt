from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# Ensure project root on path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.db import DB_FILENAME, get_connection
from core.utils.download_paths import known_download_roots


def backup_db(db_path: Path) -> Path:
    bak = db_path.with_suffix(db_path.suffix + ".bak")
    try:
        shutil.copy2(db_path, bak)
        print(f"Backed up DB: {db_path} -> {bak}")
    except Exception as e:
        print(f"Failed to back up DB: {e}")
        raise
    return bak


def resolve_candidates(path_value: str) -> List[Path]:
    candidates: List[Path] = []
    # Try direct absolute/relative resolution
    p = Path(path_value)
    candidates.append(p)
    # If not found, try known download roots
    try:
        for root in known_download_roots():
            candidates.append(Path(root) / path_value)
    except Exception:
        # ignore and continue
        pass
    return candidates


def get_mtime_iso_for_path(path_value: str) -> Optional[str]:
    for candidate in resolve_candidates(path_value):
        try:
            if candidate.exists():
                m = candidate.stat().st_mtime
                return datetime.fromtimestamp(m, timezone.utc).isoformat()
        except Exception:
            continue
    return None


def main() -> int:
    conn = get_connection()
    try:
        # Locate DB file for backup
        try:
            db_path = Path(conn.execute("PRAGMA database_list").fetchall()[0][2])
        except Exception:
            # fallback: use SETTINGS.data_dir / DB_FILENAME by importing settings
            from core.config.settings import SETTINGS

            db_path = Path(SETTINGS.data_dir) / DB_FILENAME
        if not db_path.exists():
            print(f"Database file not found at {db_path}")
            return 2

        # Backup DB
        backup_db(db_path)

        cur = conn.cursor()
        rows = cur.execute("SELECT id, path, created_at FROM local_downloads;").fetchall()
        total = len(rows)
        updated = 0
        not_found = 0
        skipped_same = 0
        sample_before = []
        sample_after = []
        for idx, (row_id, path_value, prev_created) in enumerate(rows):
            if idx < 5:
                sample_before.append((row_id, path_value, prev_created))
            mtime_iso = get_mtime_iso_for_path(path_value or "")
            if mtime_iso:
                if (not prev_created) or (str(prev_created).strip() != mtime_iso):
                    try:
                        cur.execute("UPDATE local_downloads SET created_at = ? WHERE id = ?;", (mtime_iso, row_id))
                        updated += 1
                        if len(sample_after) < 5:
                            sample_after.append((row_id, path_value, mtime_iso))
                    except Exception as e:
                        print(f"Failed to update id={row_id}: {e}")
                        continue
                else:
                    skipped_same += 1
            else:
                not_found += 1
        conn.commit()
        print(f"Total rows: {total}")
        print(f"Updated rows: {updated}")
        print(f"Unchanged (same mtime): {skipped_same}")
        print(f"Not found on disk: {not_found}")
        print("Sample before (first 5):")
        for r in sample_before:
            print(r)
        print("Sample after (first updated 5):")
        for r in sample_after:
            print(r)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
