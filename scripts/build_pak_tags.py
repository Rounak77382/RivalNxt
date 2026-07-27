from __future__ import annotations
import argparse
import json
import logging
import sqlite3
from typing import Dict, List, Tuple
from pathlib import Path
import sys

# Reuse tagger from scripts.tag_assets (support both module and script execution)
try:
    from . import tag_assets as tagger  # type: ignore
except Exception:
    # When executed as a plain script, add project root to sys.path and import via absolute package name
    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts import tag_assets as tagger  # type: ignore

from core.db.db import get_connection, init_schema, run_migrations


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Build or refresh pak_tags_json from pak_assets + asset_tags.")
    p.add_argument('--db', dest='db_path', default=None, help='Path to mods.db (optional)')
    p.add_argument('--rebuild', action='store_true', help='Rebuild all paks (truncate table first)')
    p.add_argument('--limit', type=int, default=None, help='Optional limit for testing')
    p.add_argument('--sample', type=int, default=0, help='Print N sample rows after upsert')
    p.add_argument('--log-level', dest='log_level', default='INFO', choices=['CRITICAL','ERROR','WARNING','INFO','DEBUG'])
    return p.parse_args(argv)


def ensure_schema(conn: sqlite3.Connection) -> None:
    init_schema(conn)
    run_migrations(conn)


def fetch_pak_asset_tags(conn: sqlite3.Connection, limit: int | None) -> List[Tuple[str, int | None, str | None, str | None]]:
    cur = conn.cursor()
    q = """
        SELECT pa.pak_name,
               mp.mod_id,
               at.entity,
               at.category
        FROM pak_assets pa
        LEFT JOIN mod_paks mp ON mp.pak_name = pa.pak_name
        LEFT JOIN asset_tags at ON at.asset_path = pa.asset_path
    """
    if limit:
        q += " LIMIT ?"
        return cur.execute(q, (limit,)).fetchall()
    return cur.execute(q).fetchall()


def build_pak_tags(rows: List[Tuple[str, int | None, str | None, str | None]]) -> Dict[str, Dict]:
    # Aggregate to {pak_name: {mod_id, by_entity: {entity: set(categories)}, unknown_cats: set()}}
    agg: Dict[str, Dict] = {}
    for pak_name, mod_id, entity, category in rows:
        if not pak_name or (not category and not entity):
            continue
        rec = agg.setdefault(pak_name, {"mod_id": mod_id, "by_entity": {}, "unknown_cats": set()})
        ent = (entity or '').strip()
        if ent:
            s = rec["by_entity"].setdefault(ent, set())
            if category:
                s.add(category)
        if category:
            rec["unknown_cats"].add(category)
        # Keep first non-null mod_id if multiple rows disagree
        if rec["mod_id"] is None and mod_id is not None:
            rec["mod_id"] = mod_id
    return agg


def upsert_pak_tags(conn: sqlite3.Connection, agg: Dict[str, Dict]) -> int:
    cur = conn.cursor()
    batch: List[Tuple[str, int | None, str]] = []
    for pak_name, data in agg.items():
        # Collect entities (character names) and categories as separate tags
        ents = sorted(list(data["by_entity"].keys()))
        cats_set = set()
        for cats in data["by_entity"].values():
            cats_set.update(cats)
        cats_set.update(data["unknown_cats"])
        cats = sorted(list(cats_set))
        # Store entities and categories as separate array elements, not comma-joined
        tags: List[str] = []
        if ents:
            tags.extend(ents)
        if cats:
            tags.extend(cats)
        tags_json = json.dumps(tags, ensure_ascii=False)
        batch.append((pak_name, data["mod_id"], tags_json))
    cur.executemany(
        """
        INSERT INTO pak_tags_json(pak_name, mod_id, tags_json)
        VALUES(?, ?, ?)
        ON CONFLICT(pak_name) DO UPDATE SET
            mod_id = excluded.mod_id,
            tags_json = excluded.tags_json
        ;
        """,
        batch,
    )
    conn.commit()
    return len(batch)


def main(argv=None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format='%(asctime)s %(levelname)s [pak_tags] %(message)s')
    log = logging.getLogger('build_pak_tags')
    conn = get_connection(args.db_path)
    ensure_schema(conn)

    # Thin wrapper over core.tagging.service. The old inline implementation
    # fetchall()'d every row of pak_assets into Python on every call -- including
    # from the ingest path, once per mod.
    from core.tagging.service import rebuild_all_pak_tags

    if args.rebuild:
        log.info("Truncating pak_tags_json ...")

    count = rebuild_all_pak_tags(conn, rebuild=bool(args.rebuild))
    conn.commit()
    log.info("Upserted tags for %d paks.", count)
    if args.sample and args.sample > 0:
        cur = conn.cursor()
        rows = cur.execute("SELECT pak_name, mod_id, tags_json FROM pak_tags_json ORDER BY pak_name LIMIT ?", (args.sample,)).fetchall()
        for r in rows:
            log.debug("sample: %s", r)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
