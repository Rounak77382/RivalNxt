"""Incremental asset/PAK tagging.

Previously the ingest path called ``scripts.build_asset_tags.main([])`` and
``scripts.build_pak_tags.main([])`` in-process for every mod. Both of those:

* opened their **own** SQLite connection while the ingest connection was already
  open (write contention against a 5s busy timeout),
* re-ran ``init_schema`` + ``run_migrations`` on every invocation,
* and scanned the **entire** library. build_pak_tags in particular ran
  ``SELECT ... FROM pak_assets LEFT JOIN mod_paks LEFT JOIN asset_tags`` with no
  WHERE clause and ``.fetchall()``-ed the whole result into Python, then
  re-upserted tags for every pak in the database.

That made a single ingest O(all assets in the library), so bulk-importing N mods
was O(N x library). This module does the same work scoped to the paks that
actually changed, reusing the caller's connection.

The CLI scripts remain as thin wrappers so full rebuilds still work.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("modmanager.tagging")

# SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on older builds. Chunk
# IN (...) lists well under that.
_PARAM_CHUNK = 400

# The entity map is derived from the characters/skins tables, which change only
# when character data is re-extracted. Rebuilding it per ingest meant re-reading
# every character and skin row each time.
_ENTITY_MAP_CACHE: Optional[Dict[str, str]] = None


def invalidate_entity_map_cache() -> None:
    """Drop the cached character/skin entity map.

    Call after re-extracting character data (``_task_rebuild_character_data``).
    """
    global _ENTITY_MAP_CACHE
    _ENTITY_MAP_CACHE = None


def _get_tagger():
    """Import the tagging rules lazily (scripts package, no native deps)."""
    from scripts import tag_assets as tagger  # type: ignore

    return tagger


def _entity_map(conn: sqlite3.Connection, *, refresh: bool = False) -> Dict[str, str]:
    global _ENTITY_MAP_CACHE
    if _ENTITY_MAP_CACHE is not None and not refresh:
        return _ENTITY_MAP_CACHE

    mapping: Dict[str, str] = {}
    try:
        from core.db.db import get_all_characters

        for char in get_all_characters(conn):
            char_id = char["character_id"]
            mapping[char_id] = char["name"]
            for skin in char["skins"]:
                skin_name = skin["name"]
                # Skip fallback names generated during extraction.
                if isinstance(skin_name, str) and skin_name.startswith("variant "):
                    continue
                mapping[f"{char_id}{skin['variant']}"] = skin_name
    except Exception as exc:
        logger.warning("Failed to load entity map from DB: %s", exc)
        mapping = {}

    if not mapping:
        # JSON fallback ONLY. Deliberately not load_entity_map(), which tries the
        # database first via load_entity_map_from_db() and would open a second
        # connection while the caller's is already live.
        try:
            mapping = _get_tagger().load_entity_map_from_json(None)
        except Exception as exc:
            logger.warning("Failed to load fallback entity map: %s", exc)
            mapping = {}
        if not mapping:
            # Do not cache an empty map: a later run after character extraction
            # should pick up real data.
            return {}

    _ENTITY_MAP_CACHE = mapping
    return mapping


def _split_tag(tag: str) -> Tuple[Optional[str], Optional[str]]:
    """'entity,category' or bare 'category'."""
    if "," in tag:
        entity, category = tag.split(",", 1)
        entity = entity.strip() or None
        category = category.strip() or None
        return entity, category
    stripped = tag.strip()
    return None, (stripped or None)


def _chunks(items: Sequence[Any], size: int = _PARAM_CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _normalise_pak_names(pak_names: Iterable[str]) -> List[str]:
    seen: Dict[str, None] = {}
    for name in pak_names or ():
        if isinstance(name, str) and name.strip():
            seen.setdefault(name.strip(), None)
    return list(seen)


# ---------------------------------------------------------------------------
# asset_tags
# ---------------------------------------------------------------------------
def _tag_asset_paths(
    conn: sqlite3.Connection, asset_paths: Sequence[str]
) -> int:
    """Compute and upsert tags for the given asset paths. Returns rows written."""
    if not asset_paths:
        return 0

    tagger = _get_tagger()
    entity_map = _entity_map(conn)

    rows: List[Tuple[str, Optional[str], str, str]] = []
    for asset_path in asset_paths:
        try:
            tag = (tagger.tag_asset(asset_path, entity_map) or "").strip()
        except Exception as exc:
            logger.debug("tag_asset failed for %s: %s", asset_path, exc)
            continue
        if not tag:
            continue
        entity, category = _split_tag(tag)
        if not category:
            continue
        full_tag = f"{entity},{category}" if entity else category
        rows.append((asset_path, entity, category, full_tag))

    if not rows:
        return 0

    conn.executemany(
        """
        INSERT INTO asset_tags(asset_path, entity, category, tag)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(asset_path) DO UPDATE SET
            entity = excluded.entity,
            category = excluded.category,
            tag = excluded.tag
        """,
        rows,
    )
    return len(rows)


def tag_assets_for_paks(
    conn: sqlite3.Connection,
    pak_names: Iterable[str],
    *,
    retag_existing: bool = False,
) -> int:
    """Tag the assets belonging to ``pak_names`` only.

    ``retag_existing=False`` (the default) skips assets that already have a tag,
    matching the incremental behaviour of ``build_asset_tags`` without
    ``--rebuild`` -- but scoped by pak instead of anti-joining the whole table.
    """
    names = _normalise_pak_names(pak_names)
    if not names:
        return 0

    asset_paths: List[str] = []
    seen: Dict[str, None] = {}
    for chunk in _chunks(names):
        placeholders = ",".join("?" for _ in chunk)
        if retag_existing:
            sql = (
                f"SELECT DISTINCT pa.asset_path FROM pak_assets pa "
                f"WHERE pa.pak_name IN ({placeholders})"
            )
            params: List[Any] = list(chunk)
        else:
            sql = (
                f"SELECT DISTINCT pa.asset_path FROM pak_assets pa "
                f"LEFT JOIN asset_tags t ON t.asset_path = pa.asset_path "
                f"WHERE pa.pak_name IN ({placeholders}) AND t.asset_path IS NULL"
            )
            params = list(chunk)
        for (asset_path,) in conn.execute(sql, params).fetchall():
            if asset_path and asset_path not in seen:
                seen[asset_path] = None
                asset_paths.append(asset_path)

    written = _tag_asset_paths(conn, asset_paths)
    logger.debug(
        "tag_assets_for_paks: %d pak(s), %d asset(s) tagged", len(names), written
    )
    return written


def tag_all_assets(conn: sqlite3.Connection, *, rebuild: bool = False) -> int:
    """Full-library asset tagging (used by the CLI script / maintenance task)."""
    if rebuild:
        conn.execute("DELETE FROM asset_tags;")
        sql = "SELECT DISTINCT asset_path FROM pak_assets"
    else:
        sql = """
            SELECT DISTINCT pa.asset_path
            FROM pak_assets pa
            LEFT JOIN asset_tags t ON t.asset_path = pa.asset_path
            WHERE t.asset_path IS NULL
        """
    asset_paths = [row[0] for row in conn.execute(sql).fetchall() if row[0]]
    return _tag_asset_paths(conn, asset_paths)


# ---------------------------------------------------------------------------
# pak_tags_json
# ---------------------------------------------------------------------------
def _aggregate_pak_tags(
    rows: Iterable[Tuple[str, Optional[int], Optional[str], Optional[str]]]
) -> Dict[str, Dict[str, Any]]:
    agg: Dict[str, Dict[str, Any]] = {}
    for pak_name, mod_id, entity, category in rows:
        if not pak_name or (not category and not entity):
            continue
        rec = agg.setdefault(
            pak_name, {"mod_id": mod_id, "entities": set(), "categories": set()}
        )
        ent = (entity or "").strip()
        if ent:
            rec["entities"].add(ent)
        if category:
            rec["categories"].add(category)
        # Keep the first non-null mod_id if rows disagree.
        if rec["mod_id"] is None and mod_id is not None:
            rec["mod_id"] = mod_id
    return agg


def _upsert_pak_tags(
    conn: sqlite3.Connection, agg: Dict[str, Dict[str, Any]]
) -> int:
    if not agg:
        return 0
    batch: List[Tuple[str, Optional[int], str]] = []
    for pak_name, data in agg.items():
        tags: List[str] = sorted(data["entities"]) + sorted(data["categories"])
        batch.append((pak_name, data["mod_id"], json.dumps(tags, ensure_ascii=False)))

    conn.executemany(
        """
        INSERT INTO pak_tags_json(pak_name, mod_id, tags_json)
        VALUES(?, ?, ?)
        ON CONFLICT(pak_name) DO UPDATE SET
            mod_id = excluded.mod_id,
            tags_json = excluded.tags_json
        """,
        batch,
    )
    return len(batch)


_PAK_TAG_SELECT = """
    SELECT pa.pak_name, mp.mod_id, at.entity, at.category
    FROM pak_assets pa
    LEFT JOIN mod_paks mp ON mp.pak_name = pa.pak_name
    LEFT JOIN asset_tags at ON at.asset_path = pa.asset_path
"""


def rebuild_pak_tags_for(conn: sqlite3.Connection, pak_names: Iterable[str]) -> int:
    """Rebuild pak_tags_json for ``pak_names`` only."""
    names = _normalise_pak_names(pak_names)
    if not names:
        return 0

    rows: List[Tuple[str, Optional[int], Optional[str], Optional[str]]] = []
    for chunk in _chunks(names):
        placeholders = ",".join("?" for _ in chunk)
        rows.extend(
            conn.execute(
                _PAK_TAG_SELECT + f" WHERE pa.pak_name IN ({placeholders})",
                list(chunk),
            ).fetchall()
        )

    return _upsert_pak_tags(conn, _aggregate_pak_tags(rows))


def rebuild_all_pak_tags(conn: sqlite3.Connection, *, rebuild: bool = False) -> int:
    """Full-library pak tag rebuild (used by the CLI script / maintenance task)."""
    if rebuild:
        conn.execute("DELETE FROM pak_tags_json;")
    rows = conn.execute(_PAK_TAG_SELECT).fetchall()
    return _upsert_pak_tags(conn, _aggregate_pak_tags(rows))


# ---------------------------------------------------------------------------
# Combined entry point used by the ingest path
# ---------------------------------------------------------------------------
def tag_paks(
    conn: sqlite3.Connection,
    pak_names: Iterable[str],
    *,
    retag_existing: bool = False,
    commit: bool = True,
) -> Dict[str, int]:
    """Tag assets for ``pak_names`` and refresh those paks' tag rollups.

    Reuses the caller's connection: the previous implementation opened two extra
    connections per ingest while the ingest transaction was live.
    """
    names = _normalise_pak_names(pak_names)
    if not names:
        return {"assets_tagged": 0, "paks_tagged": 0}

    assets_tagged = tag_assets_for_paks(conn, names, retag_existing=retag_existing)
    paks_tagged = rebuild_pak_tags_for(conn, names)
    if commit:
        conn.commit()
    return {"assets_tagged": assets_tagged, "paks_tagged": paks_tagged}
