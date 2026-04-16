from __future__ import annotations

"""
Ingest assets for local downloads (zip/rar/7z) into per-pak tables, then (optionally) build tags.

- Resolves download file paths relative to MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT
- For each local_downloads row whose path ends with .zip/.rar/.7z and exists on disk:
  - Extracts the archive to a temp folder using core.utils.archive.extract_archive
  - Enumerates contained assets per pak using core.assets.zip_to_asset_paths.extract_pak_asset_map_from_folder
  - Upserts into:
      mod_paks(pak_name, mod_id, source_zip, local_download_id, io_store)
      pak_assets(pak_name, asset_path)
      pak_assets_json(pak_name, mod_id, assets_json)
  - Matches pak_name to declared names in local_downloads.contents (prefers exact match; alternates .pak<->.utoc; then stem)
  - io_store=True when pak_name ends with .utoc

Finally, optionally runs build_asset_tags and build_pak_tags to produce pak_tags_json from UE asset paths.
"""

import argparse
import logging
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure project root on sys.path for direct execution
import sys as _sys
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from core.db.db import (
    get_connection,
    init_schema,
    run_migrations,
    upsert_mod_pak,
    bulk_upsert_pak_assets,
    upsert_pak_assets_json,
)
from core.utils.archive import extract_archive as extract_with_7z
from core.utils.pak_files import collapse_pak_bundle
from core.assets.zip_to_asset_paths import extract_pak_asset_map_from_folder


def _load_env_from_dotenv(dotenv_path: Optional[Path] = None) -> None:
    """Best-effort loader for a simple .env file at project root.

    Supports lines like KEY=value or KEY="value"; ignores comments and blanks.
    Does not override variables already present in os.environ.
    """
    import os as _os
    p = dotenv_path or (_ROOT / ".env")
    try:
        if not p.exists():
            return
        for raw in p.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            # strip surrounding quotes if present
            if (val.startswith("\"") and val.endswith("\"")) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            if key and key not in _os.environ:
                _os.environ[key] = val
    except Exception:
        # Silent best-effort; logging will report missing vars if still unset
        return


def _downloads_root_from_env(override: Optional[str] = None) -> Path:
    root = override or os.environ.get("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT")
    if not root:
        # Try loading from project .env then re-read
        _load_env_from_dotenv()
        root = override or os.environ.get("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT")
    if not root:
        raise RuntimeError("MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT is not set")
    p = Path(root)
    if not p.exists():
        raise FileNotFoundError(f"Downloads root not found: {p}")
    return p


def _to_full_path(rel_or_name: str, root: Path) -> Path:
    # local_downloads.path is stored relative to downloads root. Fall back to name if needed.
    cand = root / rel_or_name
    if cand.exists():
        return cand
    # Try just name under root
    cand2 = root / Path(rel_or_name).name
    return cand2


def _map_declared_name(pak_from_scan: str, declared_contents: List[str]) -> str:
    """Return pak name aligned to local_downloads.contents when possible.

    - Prefer exact match on filename (case-insensitive)
    - Try alternate extension .pak <-> .utoc
    - Fallback to stem-based match
    Otherwise return the pak_from_scan as-is
    """
    if not declared_contents:
        return pak_from_scan
    by_lower = {c.lower(): c for c in declared_contents}
    name_l = pak_from_scan.lower()
    if name_l in by_lower:
        return by_lower[name_l]
    # Alternate extension
    if name_l.endswith(".pak"):
        alt = name_l[:-4] + ".utoc"
        if alt in by_lower:
            return by_lower[alt]
    if name_l.endswith(".utoc"):
        alt = name_l[:-5] + ".pak"
        if alt in by_lower:
            return by_lower[alt]
    # Stem-based
    stem = os.path.splitext(pak_from_scan)[0].lower()
    for c in declared_contents:
        if os.path.splitext(c)[0].lower() == stem:
            return c
    return pak_from_scan


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Ingest UE assets from local download archives into per-pak tables")
    p.add_argument("--db", dest="db_path", default=None, help="Path to mods.db (optional)")
    p.add_argument("--only", dest="only_names", action="append", default=None, help="Only process local_downloads.name equal to this (can repeat)")
    p.add_argument("--rebuild-tags", action="store_true", help="After ingest, rebuild asset_tags and pak_tags_json")
    p.add_argument("--extract", action="store_true", help="Extract archives and (re)build pak_assets from contents. If omitted, no extraction occurs.")
    p.add_argument("--downloads-root", dest="downloads_root", default=None, help="Override MARVEL_RIVALS_LOCAL_DOWNLOADS_ROOT for this run")
    p.add_argument("--aes-key", dest="aes_key", default=os.environ.get("AES_KEY_HEX"))
    p.add_argument(
        "--log-level",
        dest="log_level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"],
        help="Logging level (default: INFO)",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    # Configure logging early
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    log = logging.getLogger("ingest_download_assets")
    # Ensure .env is honored for all environment-based defaults
    _load_env_from_dotenv()
    # If AES key defaulted to None before .env load, try again now
    if not args.aes_key:
        args.aes_key = os.environ.get("AES_KEY_HEX")
    try:
        root = _downloads_root_from_env(args.downloads_root)
    except Exception as e:
        logging.error("Downloads root resolution failed: %s", e)
        return 2
    log.info("Downloads root: %s", root)
    conn = get_connection(args.db_path)
    init_schema(conn)
    run_migrations(conn)

    processed = 0
    paks_written = 0
    assets_written = 0
    if args.extract:
        cur = conn.cursor()
        filt_sql = ""
        params: Tuple = ()
        if args.only_names:
            placeholders = ",".join(["?"] * len(args.only_names))
            filt_sql = f" WHERE name IN ({placeholders})"
            params = tuple(args.only_names)
        rows = cur.execute(
            f"SELECT id, name, mod_id, path, contents FROM local_downloads{filt_sql}", params
        ).fetchall()
        if not rows:
            log.warning("No local downloads to process.")
            rows = []

        if rows:
            log.info("Found %d download row(s) to process%s", len(rows),
                     f" (filtered by {len(args.only_names)} name(s))" if args.only_names else "")
        failed_downloads: List[str] = []
        for download_id, name, mod_id, relpath, contents_json in rows:
            try:
                log.debug("Row id=%s name='%s' relpath='%s' mod_id=%s", download_id, name, relpath, mod_id)
                contents: List[str] = []
                try:
                    contents = json.loads(contents_json) if contents_json else []
                except Exception:
                    contents = []
                contents = collapse_pak_bundle(contents)
                try:
                    cur.execute(
                        "UPDATE local_downloads SET contents = ? WHERE id = ?",
                        (json.dumps(contents, ensure_ascii=False), download_id),
                    )
                    conn.commit()
                except Exception:
                    log.debug("[%s] Failed to persist collapsed contents", name, exc_info=True)
                full = _to_full_path(relpath or name, root)
                low = str(full).lower()
                if not full.exists():
                    log.debug("Skip: file does not exist: %s", full)
                    continue
                
                # Determine if this is an archive or a folder
                is_archive = low.endswith((".zip", ".rar", ".7z"))
                is_folder = full.is_dir()
                
                if not is_archive and not is_folder:
                    log.debug("Skip: not an archive or folder: %s", full)
                    continue

                tmpdir = None
                pak_source_dir = None
                
                if is_archive:
                    # Extract archive to temp directory with timeout protection
                    tmpdir = tempfile.mkdtemp(prefix="ingest_dl_")
                    log.info("[%s] Extracting archive -> %s", name, full)
                    try:
                        import threading
                        extract_error: List[Optional[Exception]] = [None]
                        
                        def _do_extract() -> None:
                            try:
                                extract_with_7z(str(full), tmpdir)
                            except Exception as exc:
                                extract_error[0] = exc
                        
                        t = threading.Thread(target=_do_extract, daemon=True)
                        t.start()
                        t.join(timeout=120)  # 2 minute timeout per archive
                        
                        if t.is_alive():
                            log.error("[%s] Archive extraction timed out after 120s — skipping", name)
                            shutil.rmtree(tmpdir, ignore_errors=True)
                            continue
                        if extract_error[0] is not None:
                            raise extract_error[0]
                        pak_source_dir = tmpdir
                    except Exception as e:
                        log.error("[%s] Failed to extract archive: %s", name, e)
                        if tmpdir:
                            shutil.rmtree(tmpdir, ignore_errors=True)
                        continue
                elif is_folder:
                    # Use folder directly (already extracted)
                    log.info("[%s] Processing folder (already extracted) -> %s", name, full)
                    pak_source_dir = str(full)
                
                try:
                    pak_map = extract_pak_asset_map_from_folder(pak_source_dir, aes_key=args.aes_key)
                    if not pak_map:
                        log.warning("[%s] No paks/assets found after extraction", name)
                        continue
                    processed += 1
                    log.info("[%s] Found %d pak(s) in archive", name, len(pak_map))
                    # Ensure mod_id respects FK; None when no row in mods
                    resolved_mod_id: Optional[int] = None
                    if mod_id is not None:
                        if conn.execute("SELECT 1 FROM mods WHERE mod_id=?", (mod_id,)).fetchone():
                            resolved_mod_id = mod_id
                    # Merge paks (e.g. .pak + .utoc) into a single entry keyed by the .pak name
                    merged_pak_map: Dict[str, List[str]] = {}
                    merged_io_store: Dict[str, bool] = {}
                    
                    for raw_pak_name, assets in pak_map.items():
                        declared = _map_declared_name(raw_pak_name, contents)
                        
                        # Normalize extension: .utoc/.ucas -> .pak
                        lower_declared = declared.lower()
                        if lower_declared.endswith(".utoc"):
                            normalized_name = declared[:-5] + ".pak"
                        elif lower_declared.endswith(".ucas"):
                            normalized_name = declared[:-5] + ".pak"
                        else:
                            normalized_name = declared
                            
                        # Track if this bundle involves IoStore (if any part is .utoc)
                        is_utoc = raw_pak_name.lower().endswith(".utoc")
                        if normalized_name not in merged_io_store:
                            merged_io_store[normalized_name] = False
                        if is_utoc:
                            merged_io_store[normalized_name] = True
                            
                        if normalized_name not in merged_pak_map:
                            merged_pak_map[normalized_name] = []
                        merged_pak_map[normalized_name].extend(assets)

                    for pak_name, assets in merged_pak_map.items():
                        # Deduplicate assets
                        assets = sorted(list(set(assets)))
                        io_store = merged_io_store.get(pak_name, False)
                        
                        log.debug("[%s] Upserting pak %s with %d asset(s) (io_store=%s)",
                                  name, pak_name, len(assets), io_store)
                        upsert_mod_pak(
                            conn,
                            pak_name=pak_name,
                            mod_id=resolved_mod_id,
                            source_zip=str(Path(relpath or name).as_posix()),
                            local_download_id=download_id,
                            io_store=io_store,
                        )
                        paks_written += 1
                        assets_written += bulk_upsert_pak_assets(conn, pak_name, assets, replace=True)
                        upsert_pak_assets_json(conn, pak_name, assets, mod_id=resolved_mod_id)
                finally:
                    # Only clean up temp directory if we created one (for archives)
                    if tmpdir:
                        try:
                            shutil.rmtree(tmpdir, ignore_errors=True)
                        except Exception:
                            pass
            except Exception:
                log.exception("[%s] Failed to process download (id=%s) — skipping", name, download_id)
                failed_downloads.append(name or f"id:{download_id}")
                continue

        if failed_downloads:
            log.warning("Skipped %d problematic download(s): %s", len(failed_downloads), ", ".join(failed_downloads))
        log.info("Processed %d archive(s); wrote %d pak(s) and %d pak_assets.", processed, paks_written, assets_written)
    else:
        log.info("Extraction disabled (--extract not set). Skipping archive processing and going straight to tag rebuild (if requested).")

    if args.rebuild_tags:
        # Lazy import to avoid circular deps
        log.info("Rebuilding tags (asset_tags then pak_tags_json)...")
        try:
            from scripts import build_asset_tags as bat  # type: ignore
            from scripts import build_pak_tags as bpt  # type: ignore
            # build missing asset_tags first, then aggregate to pak_tags_json
            bat.main(["--db", args.db_path, "--log-level", args.log_level] if args.db_path else ["--log-level", args.log_level])
            bpt.main(["--db", args.db_path, "--log-level", args.log_level] if args.db_path else ["--log-level", args.log_level])
            log.info("Tag rebuild complete.")
        except Exception as e:
            log.exception("Tag rebuild failed: %s", e)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
