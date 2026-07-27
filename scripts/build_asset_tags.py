from __future__ import annotations
import argparse
import logging
import sqlite3
from typing import Optional, Tuple
from pathlib import Path
import sys

# Support both `python -m scripts.build_x` and `python scripts/build_x.py` by
# putting the project root on sys.path before importing core.*.
#
# NOTE: this was previously a `try: from . import tag_assets` / `except: sys.path
# bootstrap` pair. Once the tagger import became unnecessary, ruff --fix reduced
# the try body to `pass`, which never raises -- silently disabling the bootstrap
# and breaking plain-script execution. Made unconditional so it cannot rot.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.db.db import get_connection, init_schema, run_migrations


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build or refresh asset_tags from pak_assets.")
    p.add_argument('--db', dest='db_path', default=None, help='Path to mods.db (optional)')
    p.add_argument('--map', dest='map_path', default=None, help='Optional character_ids.json mapping path')
    p.add_argument('--limit', type=int, default=None, help='Optional limit for testing')
    p.add_argument('--rebuild', action='store_true', help='Truncate and rebuild all tags')
    p.add_argument('--log-level', dest='log_level', default='INFO', choices=['CRITICAL','ERROR','WARNING','INFO','DEBUG'])
    return p.parse_args(argv)


def ensure_schema(conn: sqlite3.Connection) -> None:
    # Make sure base schema and migrations are applied (to get asset_tags table)
    init_schema(conn)
    run_migrations(conn)


def split_tag(tag: str) -> Tuple[Optional[str], str]:
    # tag is either 'category' or 'entity,category'
    if ',' in tag:
        ent, cat = tag.split(',', 1)
        return ent.strip() or None, cat.strip()
    return None, tag.strip()


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format='%(asctime)s %(levelname)s [asset_tags] %(message)s')
    log = logging.getLogger('build_asset_tags')
    conn = get_connection(args.db_path)
    ensure_schema(conn)

    # Thin wrapper over core.tagging.service, which owns the tagging logic so the
    # ingest path can run it scoped to a single mod's paks instead of rescanning
    # the whole library.
    from core.tagging.service import tag_all_assets

    if args.rebuild:
        log.info("Truncating asset_tags ...")

    written = tag_all_assets(conn, rebuild=bool(args.rebuild))
    conn.commit()
    log.info("Tagged %d asset path(s).", written)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
