"""M8: per-ingest tagging must be scoped to the mod's own paks.

The ingest path called scripts.build_asset_tags.main([]) and
scripts.build_pak_tags.main([]) in-process for every mod. Both opened their own
SQLite connection, re-ran init_schema + run_migrations, and rescanned the WHOLE
library -- build_pak_tags fetchall()'d every row of pak_assets into Python and
re-upserted tags for every pak in the database. One ingest was therefore
O(all assets in library), making a bulk import of N mods O(N x library).
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from core.tagging.service import (
    invalidate_entity_map_cache,
    rebuild_all_pak_tags,
    rebuild_pak_tags_for,
    tag_all_assets,
    tag_assets_for_paks,
    tag_paks,
)

EXISTING_PAKS = 500
EXISTING_ASSETS = 50_000
NEW_PAKS = 5


@pytest.fixture(autouse=True)
def _clear_entity_cache():
    invalidate_entity_map_cache()
    yield
    invalidate_entity_map_cache()


@pytest.fixture
def big_library(tmp_path: Path):
    """A library with 500 paks / 50k assets already tagged."""
    from core.db.db import init_schema, run_migrations

    db_path = tmp_path / "library.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = OFF;")  # test-only: fast setup
    init_schema(conn)
    run_migrations(conn)

    conn.executemany(
        "INSERT OR IGNORE INTO mod_paks(pak_name, mod_id, source_zip) VALUES(?, NULL, ?)",
        [(f"existing_{i}.pak", f"Existing{i}.zip") for i in range(EXISTING_PAKS)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
        [
            (
                f"existing_{i % EXISTING_PAKS}.pak",
                f"/Game/Marvel/Characters/1011/001/Meshes/sk_asset_{i}.uasset",
            )
            for i in range(EXISTING_ASSETS)
        ],
    )
    conn.commit()

    # Pre-tag everything so the incremental path has a populated baseline.
    tag_all_assets(conn)
    rebuild_all_pak_tags(conn)
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()

    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _add_new_mod(conn: sqlite3.Connection, pak_count: int = NEW_PAKS) -> list[str]:
    names = [f"newmod_{i}.pak" for i in range(pak_count)]
    # mods row first: mod_paks.mod_id has an FK to mods(mod_id).
    conn.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(4242, 'marvelrivals', 'New Mod')"
    )
    conn.executemany(
        "INSERT OR IGNORE INTO mod_paks(pak_name, mod_id, source_zip) VALUES(?, 4242, ?)",
        [(n, "NewMod.zip") for n in names],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
        [
            (names[i % pak_count], f"/Game/Marvel/Characters/1022/002/Meshes/sk_new_{i}.uasset")
            for i in range(40)
        ],
    )
    conn.commit()
    return names


# ---------------------------------------------------------------------------
# The library must be big enough for the test to mean anything
# ---------------------------------------------------------------------------
def test_baseline_library_size(big_library):
    conn = big_library
    assert conn.execute("SELECT COUNT(*) FROM pak_assets").fetchone()[0] == EXISTING_ASSETS
    assert conn.execute("SELECT COUNT(*) FROM mod_paks").fetchone()[0] == EXISTING_PAKS
    tagged = conn.execute("SELECT COUNT(*) FROM asset_tags").fetchone()[0]
    assert tagged > 0, "baseline assets were not tagged; the tagger produced nothing"


# ---------------------------------------------------------------------------
# Scoping: queries must carry an IN (...) clause
# ---------------------------------------------------------------------------
def test_tagging_queries_are_scoped(big_library, recorder):
    conn = big_library
    new_paks = _add_new_mod(conn)

    conn.set_trace_callback(recorder)
    tag_paks(conn, new_paks)
    conn.set_trace_callback(None)

    pak_asset_queries = [
        s for s in recorder.statements if "pak_assets" in s.lower() and "select" in s.lower()
    ]
    assert pak_asset_queries, "no pak_assets SELECT observed"
    for sql in pak_asset_queries:
        assert "in (" in sql.lower(), f"unscoped query against pak_assets: {sql}"


def test_no_unscoped_full_table_scan_of_pak_assets(big_library, recorder):
    """The specific regression: a SELECT over pak_assets with no WHERE."""
    conn = big_library
    new_paks = _add_new_mod(conn)

    conn.set_trace_callback(recorder)
    tag_paks(conn, new_paks)
    conn.set_trace_callback(None)

    for sql in recorder.statements:
        low = " ".join(sql.lower().split())
        if "from pak_assets" in low and low.startswith("select"):
            assert "where" in low, f"unbounded scan of pak_assets: {sql}"


def test_work_is_proportional_to_the_mod_not_the_library(big_library):
    """Rows touched must scale with the mod's paks, not the whole library."""
    conn = big_library
    new_paks = _add_new_mod(conn)

    stats = tag_paks(conn, new_paks)

    # Only the new mod's 40 assets are newly tagged...
    assert stats["assets_tagged"] <= 40, stats
    # ...and only its 5 paks get tag rollups rewritten, not all 505.
    assert stats["paks_tagged"] == NEW_PAKS, stats
    assert stats["paks_tagged"] < EXISTING_PAKS


def test_scoped_tagging_is_much_faster_than_full_rebuild(big_library):
    conn = big_library
    new_paks = _add_new_mod(conn)

    start = time.perf_counter()
    tag_paks(conn, new_paks)
    scoped = time.perf_counter() - start

    start = time.perf_counter()
    tag_all_assets(conn)
    rebuild_all_pak_tags(conn)
    conn.commit()
    full = time.perf_counter() - start

    print(
        f"\n  library: {EXISTING_PAKS} paks / {EXISTING_ASSETS} assets"
        f"\n  scoped tagging for a {NEW_PAKS}-pak mod : {scoped*1000:8.1f} ms"
        f"\n  old full-library rebuild             : {full*1000:8.1f} ms"
        f"\n  speedup: {full/scoped:.1f}x"
    )
    assert full / scoped >= 5.0, (
        f"expected a large win; scoped {scoped*1000:.1f} ms vs full {full*1000:.1f} ms"
    )


def test_ingest_does_not_open_extra_connections(big_library, monkeypatch):
    """The old path opened two extra SQLite connections per ingest via
    get_connection() inside the CLI scripts."""
    import core.db.db as db_mod

    opened: list[str] = []
    real_connect = sqlite3.connect

    def counting_connect(*args, **kwargs):
        opened.append(str(args[0]) if args else "?")
        return real_connect(*args, **kwargs)

    conn = big_library
    new_paks = _add_new_mod(conn)

    monkeypatch.setattr(db_mod.sqlite3, "connect", counting_connect)
    tag_paks(conn, new_paks)
    monkeypatch.undo()

    assert opened == [], f"tagging opened {len(opened)} extra connection(s): {opened}"


# ---------------------------------------------------------------------------
# Equivalence with a full rebuild -- the correctness guarantee
# ---------------------------------------------------------------------------
def test_scoped_result_matches_full_rebuild(big_library):
    """Incremental tagging must produce exactly what a full rebuild would."""
    conn = big_library
    new_paks = _add_new_mod(conn)

    tag_paks(conn, new_paks)
    scoped_asset_tags = dict(
        conn.execute("SELECT asset_path, tag FROM asset_tags").fetchall()
    )
    scoped_pak_tags = dict(
        conn.execute("SELECT pak_name, tags_json FROM pak_tags_json").fetchall()
    )

    # Wipe and rebuild everything from scratch.
    tag_all_assets(conn, rebuild=True)
    rebuild_all_pak_tags(conn, rebuild=True)
    conn.commit()
    full_asset_tags = dict(
        conn.execute("SELECT asset_path, tag FROM asset_tags").fetchall()
    )
    full_pak_tags = dict(
        conn.execute("SELECT pak_name, tags_json FROM pak_tags_json").fetchall()
    )

    assert scoped_asset_tags == full_asset_tags, "asset_tags diverge from full rebuild"
    assert scoped_pak_tags == full_pak_tags, "pak_tags_json diverge from full rebuild"


def test_new_mod_tags_are_actually_written(big_library):
    conn = big_library
    new_paks = _add_new_mod(conn)
    tag_paks(conn, new_paks)

    rows = conn.execute(
        "SELECT pak_name, tags_json FROM pak_tags_json WHERE pak_name LIKE 'newmod_%'"
    ).fetchall()
    assert len(rows) == NEW_PAKS, rows
    for _, tags_json in rows:
        assert tags_json and tags_json != "[]", tags_json


def test_mod_id_is_recorded_on_pak_tags(big_library):
    conn = big_library
    new_paks = _add_new_mod(conn)
    tag_paks(conn, new_paks)
    rows = conn.execute(
        "SELECT DISTINCT mod_id FROM pak_tags_json WHERE pak_name LIKE 'newmod_%'"
    ).fetchall()
    assert rows == [(4242,)], rows


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("names", [[], None, ["", "   "]])
def test_empty_pak_list_is_a_noop(big_library, names):
    conn = big_library
    assert tag_paks(conn, names) == {"assets_tagged": 0, "paks_tagged": 0}


def test_unknown_pak_name_is_harmless(big_library):
    conn = big_library
    assert tag_paks(conn, ["does_not_exist.pak"]) == {
        "assets_tagged": 0,
        "paks_tagged": 0,
    }


def test_retag_existing_forces_recompute(big_library):
    conn = big_library
    new_paks = _add_new_mod(conn)
    tag_paks(conn, new_paks)

    # Default skips already-tagged assets.
    assert tag_assets_for_paks(conn, new_paks) == 0
    # Explicit retag recomputes them.
    assert tag_assets_for_paks(conn, new_paks, retag_existing=True) > 0


def test_chunking_handles_more_paks_than_the_param_limit(big_library):
    """SQLite caps bound parameters; the IN (...) list must be chunked."""
    conn = big_library
    many = [f"existing_{i}.pak" for i in range(EXISTING_PAKS)]
    assert len(many) > 400
    count = rebuild_pak_tags_for(conn, many)  # must not raise "too many variables"
    assert count > 0


def test_duplicate_pak_names_are_deduplicated(big_library):
    conn = big_library
    new_paks = _add_new_mod(conn)
    stats = tag_paks(conn, new_paks + new_paks + new_paks)
    assert stats["paks_tagged"] == NEW_PAKS, stats
