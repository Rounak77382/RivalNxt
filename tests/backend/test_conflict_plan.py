"""M7: the conflict-detection aggregate must use a covering index.

pak_assets had PRIMARY KEY(pak_name, asset_path) and a single-column index on
asset_path. The rebuild's base CTE groups by asset_path and counts DISTINCT
pak_name, so the single-column index forced a table lookup per row:
    SCAN pa USING INDEX idx_pak_assets_asset_path
With (asset_path, pak_name) both columns come from the index:
    SCAN pa USING COVERING INDEX idx_pak_assets_asset_pak
"""
from __future__ import annotations

import sqlite3


# The base CTE from rebuild_conflicts, reproduced so the plan can be inspected.
CONFLICT_BASE_SQL = """
SELECT pa.asset_path,
       COUNT(DISTINCT CASE
           WHEN mp.mod_id IS NOT NULL THEN CAST(mp.mod_id AS TEXT)
           WHEN mp.local_download_id IS NOT NULL THEN 'local:' || CAST(mp.local_download_id AS TEXT)
           WHEN mp.source_zip IS NOT NULL THEN 'zip:' || LOWER(mp.source_zip)
           ELSE 'pak:' || LOWER(pa.pak_name)
       END) AS mod_count,
       COUNT(DISTINCT pa.pak_name) AS pak_count
FROM pak_assets pa
JOIN mod_paks mp ON mp.pak_name = pa.pak_name
GROUP BY pa.asset_path
HAVING mod_count > 1
"""


def _plan(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(str(r[-1]) for r in rows)


def _seed(conn: sqlite3.Connection, paks: int = 60, assets: int = 30_000) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO mod_paks(pak_name, mod_id, source_zip) VALUES(?, NULL, ?)",
        [(f"p{i}.pak", f"z{i}.zip") for i in range(paks)],
    )
    conn.executemany(
        "INSERT OR IGNORE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
        [(f"p{i % paks}.pak", f"/Game/A/a{i % (assets // 10)}.uasset") for i in range(assets)],
    )
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()


# ---------------------------------------------------------------------------
# Index presence
# ---------------------------------------------------------------------------
def test_covering_index_exists(schema_db):
    names = {
        r[0]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pak_assets'"
        ).fetchall()
    }
    assert "idx_pak_assets_asset_pak" in names, names


def test_redundant_single_column_index_is_gone(schema_db):
    names = {
        r[0]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pak_assets'"
        ).fetchall()
    }
    assert "idx_pak_assets_asset_path" not in names, (
        "the single-column index is redundant once (asset_path, pak_name) exists "
        "and only adds write amplification"
    )


def test_index_column_order(schema_db):
    """asset_path MUST lead, or plain WHERE asset_path = ? lookups regress."""
    sql = schema_db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'idx_pak_assets_asset_pak'"
    ).fetchone()[0]
    normalized = sql.replace(" ", "").lower()
    assert "(asset_path,pak_name)" in normalized, sql


# ---------------------------------------------------------------------------
# Query plans
# ---------------------------------------------------------------------------
def test_conflict_aggregate_uses_covering_index(schema_db):
    _seed(schema_db)
    plan = _plan(schema_db, CONFLICT_BASE_SQL)
    assert "idx_pak_assets_asset_pak" in plan, plan
    assert "COVERING INDEX" in plan.upper(), (
        f"expected a covering index scan (no table lookups); plan={plan}"
    )


def test_conflict_aggregate_does_not_scan_the_table(schema_db):
    """Every scan step touching pak_assets must go through an index.

    A grouped aggregate legitimately scans (there is no seek for "all rows"), so
    the regression to catch is a scan with no USING INDEX clause -- i.e. reading
    heap pages directly.
    """
    _seed(schema_db)
    rows = [
        str(r[-1])
        for r in schema_db.execute("EXPLAIN QUERY PLAN " + CONFLICT_BASE_SQL).fetchall()
    ]
    pak_asset_steps = [
        step
        for step in rows
        if step.startswith("SCAN pa") or step.startswith("SCAN pak_assets")
    ]
    assert pak_asset_steps, f"no pak_assets step found in plan: {rows}"
    for step in pak_asset_steps:
        assert "USING" in step and "INDEX" in step, (
            f"pak_assets is being read without an index: {step!r} (full plan {rows})"
        )


def test_asset_path_lookup_still_uses_an_index(schema_db):
    """Dropping idx_pak_assets_asset_path must not regress point lookups --
    asset_path is the leading column of the replacement."""
    _seed(schema_db)
    plan = _plan(
        schema_db,
        "SELECT pak_name FROM pak_assets WHERE asset_path = ?",
        ("/Game/A/a5.uasset",),
    )
    assert "SEARCH" in plan.upper(), plan
    assert "idx_pak_assets_asset_pak" in plan, plan
    assert "SCAN" not in plan.upper(), plan


def test_conflicts_detail_query_still_indexed(schema_db):
    """The real query from core/db/conflicts.py get_asset_conflict_detail."""
    _seed(schema_db)
    plan = _plan(
        schema_db,
        """
        SELECT pa.pak_name, mp.mod_id, mp.source_zip
        FROM pak_assets pa
        JOIN mod_paks mp ON mp.pak_name = pa.pak_name
        WHERE pa.asset_path = ? AND mp.mod_id IS NOT NULL
        """,
        ("/Game/A/a5.uasset",),
    )
    assert "SEARCH" in plan.upper(), plan
    assert "idx_pak_assets_asset_pak" in plan, plan


def test_pak_name_lookup_still_uses_primary_key(schema_db):
    """Queries by pak_name rely on the (pak_name, asset_path) PK, untouched."""
    _seed(schema_db)
    plan = _plan(
        schema_db, "SELECT asset_path FROM pak_assets WHERE pak_name = ?", ("p1.pak",)
    )
    assert "SEARCH" in plan.upper(), plan


# ---------------------------------------------------------------------------
# The real rebuild still works and is correct
# ---------------------------------------------------------------------------
def test_rebuild_still_produces_correct_conflicts(schema_db):
    from core.db.db import rebuild_conflicts

    schema_db.executemany(
        "INSERT OR IGNORE INTO mod_paks(pak_name, mod_id, source_zip) VALUES(?, NULL, ?)",
        [("a.pak", "A.zip"), ("b.pak", "B.zip")],
    )
    schema_db.executemany(
        "INSERT OR IGNORE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
        [
            ("a.pak", "/Game/shared.uasset"),
            ("b.pak", "/Game/shared.uasset"),
            ("a.pak", "/Game/only_a.uasset"),
        ],
    )
    schema_db.commit()

    rebuild_conflicts(schema_db, active_only=False)

    rows = schema_db.execute(
        "SELECT asset_path, distinct_mods FROM asset_conflicts ORDER BY asset_path"
    ).fetchall()
    assert rows == [("/Game/shared.uasset", 2)], rows


def test_analyze_statistics_are_present(schema_db):
    """The migration runs ANALYZE; without sqlite_stat1 the planner guesses."""
    _seed(schema_db)
    tables = {
        r[0]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'sqlite_stat1'"
        ).fetchall()
    }
    assert "sqlite_stat1" in tables
