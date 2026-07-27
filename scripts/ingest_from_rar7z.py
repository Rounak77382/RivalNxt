from __future__ import annotations
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

# Ensure project root on sys.path for direct execution
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.db import (
    bulk_upsert_pak_assets,
    get_connection,
    init_schema,
    next_local_download_id,
    resolve_created_at,
    upsert_mod_pak,
    upsert_pak_assets_json,
)
from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder
from core.utils.archive import extract_archive as extract_with_7z
from core.utils.pak_files import collapse_pak_bundle


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Ingest assets from .rar/.7z archives by extracting to temp folder and scanning.")
    p.add_argument("archive", help="Path to .rar or .7z file")
    p.add_argument("--mod-id", dest="mod_id", type=int, default=None, help="Nexus mod ID if known")
    p.add_argument("--name", dest="name", default=None, help="Display name to insert into local_downloads when missing")
    p.add_argument("--db", dest="db_path", default=None)
    p.add_argument("--aes-key", dest="aes_key", default=os.environ.get("AES_KEY_HEX"))
    p.add_argument("--created-at", dest="created_at", default=None, help="Optional timestamp (ISO-8601 or epoch) to use for created_at")
    return p.parse_args(argv)


def extract_archive(archive_path: str, dest_dir: str) -> None:
    # Delegate to the shared archive extractor for consistency with the rest of the app."
    lower = archive_path.lower()
    if not (lower.endswith(".rar") or lower.endswith(".7z")):
        raise ValueError("Unsupported archive type; expected .rar or .7z")
    extract_with_7z(archive_path, dest_dir)


def main(argv=None) -> int:
    args = parse_args(argv)
    arc = Path(args.archive)
    if not arc.exists():
        print(f"Archive not found: {arc}")
        return 2
    conn = get_connection(args.db_path)
    init_schema(conn)

    # Create a local_downloads row for correlation if one doesn't exist
    path_key = arc.name
    cur = conn.cursor()
    row = cur.execute(
        "SELECT id, contents FROM local_downloads WHERE path = ? OR name = ? LIMIT 1",
        (path_key, arc.name),
    ).fetchone()
    if row:
        local_download_id = row[0]
        try:
            contents = json.loads(row[1]) if row[1] else []
        except Exception:
            contents = []
        contents = collapse_pak_bundle(contents)
        cur.execute(
            "UPDATE local_downloads SET contents = ? WHERE id = ?",
            (json.dumps(contents, ensure_ascii=False), local_download_id),
        )
        conn.commit()
    else:
        local_download_id = next_local_download_id(conn)
        created_at_iso = resolve_created_at(path=arc, hints=[args.created_at])

        cur.execute(
            """
            INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path_key,
                local_download_id,
                args.name or arc.stem,
                args.mod_id,
                "",
                json.dumps([], ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                created_at_iso,
            ),
        )
        conn.commit()
        contents = []

    # Extract to temp and scan for paks
    tmpdir = tempfile.mkdtemp(prefix="ingest_rar7z_")
    try:
        extract_archive(str(arc), tmpdir)
        pak_map = extract_pak_asset_map_from_folder(tmpdir, aes_key=args.aes_key)

        # Best-effort mapping to contents declared names (exact/stem)
        normalized_contents = {c: c for c in contents}
        stem_map = {os.path.splitext(c)[0].lower(): c for c in contents}

        # Respect FK: only set mod_id if exists in mods
        resolved_mod_id: Optional[int] = None
        if args.mod_id is not None:
            if conn.execute("SELECT 1 FROM mods WHERE mod_id=?", (args.mod_id,)).fetchone():
                resolved_mod_id = args.mod_id

        source_zip_rel = path_key
        total_paks = 0
        total_assets = 0
        for pak_name, asset_list in pak_map.items():
            total_paks += 1
            total_assets += len(asset_list)
            declared_name = normalized_contents.get(pak_name)
            if not declared_name:
                stem = os.path.splitext(pak_name)[0].lower()
                declared_name = stem_map.get(stem, pak_name)
            upsert_mod_pak(
                conn,
                pak_name=declared_name,
                mod_id=resolved_mod_id,
                source_zip=source_zip_rel,
                local_download_id=local_download_id,
                io_store=True if pak_name.lower().endswith('.utoc') else False,
            )
            bulk_upsert_pak_assets(conn, declared_name, asset_list, replace=True)
            upsert_pak_assets_json(conn, declared_name, asset_list, mod_id=resolved_mod_id)

        print(f"Ingested {total_paks} pak(s) with {total_assets} assets from {arc.name}.")
        return 0
    finally:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
