"""Export mod_changelogs rows into per-mod JSON files.

Creates one file per mod in <out_dir> (default: build/changelogs) with the
structure:

{
  "mod_id": 1234,
  "changelogs": [
    {"version": "1.0", "changelog": "...", "uploaded_at": "..."},
    ...
  ]
}

Usage:
  python -m scripts.export_changelogs [--out-dir path] [--mod 123 456]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

from core.db import get_connection


def export_all(out_dir: Path, specific_mods: List[int] | None = None) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    cur = conn.cursor()

    if specific_mods:
        mod_ids = list(dict.fromkeys(int(m) for m in specific_mods))
    else:
        mod_ids = [r[0] for r in cur.execute("SELECT DISTINCT mod_id FROM mod_changelogs ORDER BY mod_id;").fetchall()]

    written: List[Path] = []
    for mod_id in mod_ids:
        rows = cur.execute(
            "SELECT version, changelog, uploaded_at FROM mod_changelogs WHERE mod_id = ? ORDER BY uploaded_at DESC, version DESC;",
            (mod_id,),
        ).fetchall()
        changelogs = [
            {"version": r[0], "changelog": r[1] or "", "uploaded_at": r[2]} for r in rows
        ]
        payload = {"mod_id": mod_id, "changelogs": changelogs}
        out_file = out_dir / f"changelogs_{mod_id}.json"
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        written.append(out_file)
        print(f"Wrote {out_file} ({len(changelogs)} entries)")

    return written


def main() -> None:
    p = argparse.ArgumentParser(description="Export mod_changelogs into per-mod JSON files")
    p.add_argument("--out-dir", default="build/changelogs", help="Output directory")
    p.add_argument("--mod", action="append", help="Specific mod_id to export (repeatable)")
    args = p.parse_args()

    out_dir = Path(args.out_dir)
    written = export_all(out_dir, args.mod)
    print(f"Exported {len(written)} files to {out_dir}")


if __name__ == "__main__":
    main()
