"""L4: duplicate detection must use an index seek, not a full table scan.

`_find_duplicate_download` matched with `WHERE LOWER(name) = LOWER(?)`.
Wrapping the column in LOWER() makes the predicate non-sargable, so
idx_local_downloads_name was unusable and every ingest paid a full scan of
local_downloads. Fixed by `WHERE name = ? COLLATE NOCASE` plus a matching
NOCASE index.
"""
from __future__ import annotations

import sqlite3

import pytest

NEW_QUERY_NO_MODID = """
    SELECT id, name, version, path
    FROM local_downloads
    WHERE name = ? COLLATE NOCASE
"""

NEW_QUERY_WITH_MODID = """
    SELECT id, name, version, path
    FROM local_downloads
    WHERE name = ? COLLATE NOCASE AND mod_id = ?
"""

OLD_QUERY = """
    SELECT id, name, version, path
    FROM local_downloads
    WHERE LOWER(name) = LOWER(?)
"""


def _plan(conn: sqlite3.Connection, sql: str, params: tuple) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return " | ".join(str(r[-1]) for r in rows)


def _seed(conn: sqlite3.Connection, count: int = 200) -> None:
    conn.executemany(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, ?, ?, ?, ?, '[]', '[]')
        """,
        [
            (f"C:/dl/Mod{i}.zip", i, f"Mod Name {i}", 1000 + i, "1.0")
            for i in range(count)
        ],
    )
    conn.commit()
    conn.execute("ANALYZE")
    conn.commit()


# ---------------------------------------------------------------------------
# Index existence
# ---------------------------------------------------------------------------
def test_nocase_index_exists(schema_db):
    names = {
        r[0]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='local_downloads'"
        ).fetchall()
    }
    assert "idx_local_downloads_name_nocase" in names, names


def test_file_md5_index_exists(schema_db):
    names = {
        r[0]
        for r in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='local_downloads'"
        ).fetchall()
    }
    assert "idx_local_downloads_file_md5" in names, names


def test_nocase_index_is_declared_nocase(schema_db):
    """A plain index on name cannot satisfy a COLLATE NOCASE predicate."""
    sql = schema_db.execute(
        "SELECT sql FROM sqlite_master WHERE name='idx_local_downloads_name_nocase'"
    ).fetchone()[0]
    assert "NOCASE" in sql.upper(), sql


# ---------------------------------------------------------------------------
# Query plan: SEARCH (seek) not SCAN
# ---------------------------------------------------------------------------
def test_new_query_uses_index_seek(schema_db):
    _seed(schema_db)
    plan = _plan(schema_db, NEW_QUERY_NO_MODID, ("Mod Name 7",))
    assert "SEARCH" in plan.upper(), plan
    assert "SCAN local_downloads" not in plan, plan
    assert "idx_local_downloads_name_nocase" in plan, plan


def test_new_query_with_mod_id_uses_index_seek(schema_db):
    _seed(schema_db)
    plan = _plan(schema_db, NEW_QUERY_WITH_MODID, ("Mod Name 7", 1007))
    assert "SEARCH" in plan.upper(), plan
    assert "SCAN local_downloads" not in plan, plan


def test_old_query_would_have_scanned(schema_db):
    """Documents the regression: LOWER(name) forces a full scan."""
    _seed(schema_db)
    plan = _plan(schema_db, OLD_QUERY, ("mod name 7",))
    assert "SCAN" in plan.upper(), (
        "expected the legacy LOWER() query to scan; if this now seeks, the "
        f"planner changed and this guard needs revisiting. plan={plan}"
    )


# ---------------------------------------------------------------------------
# Behaviour must be preserved: case-insensitive matching still works
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "stored,queried",
    [
        ("ModName", "modname"),
        ("ModName", "MODNAME"),
        ("Iron Man Suit", "iron man suit"),
        ("MiXeD CaSe", "mIxEd CaSe"),
    ],
)
def test_case_insensitive_match(schema_db, stored, queried):
    schema_db.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, 1, ?, 42, '1.0', '[]', '[]')
        """,
        (f"C:/dl/{stored}.zip", stored),
    )
    schema_db.commit()

    rows = schema_db.execute(NEW_QUERY_NO_MODID, (queried,)).fetchall()
    assert len(rows) == 1, f"{queried!r} did not match stored {stored!r}"
    assert rows[0][1] == stored


def test_non_matching_name_returns_nothing(schema_db):
    _seed(schema_db, 10)
    rows = schema_db.execute(NEW_QUERY_NO_MODID, ("Totally Different",)).fetchall()
    assert rows == []


def test_old_and_new_queries_agree_on_results(schema_db):
    """Behaviour parity: the optimisation must not change which rows match."""
    _seed(schema_db, 50)
    schema_db.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES('C:/dl/Weird.zip', 999, 'WeIrD CaSe Mod', 7, '2.0', '[]', '[]')
        """
    )
    schema_db.commit()

    for probe in ("weird case mod", "WEIRD CASE MOD", "Mod Name 3", "nope"):
        old = schema_db.execute(OLD_QUERY, (probe,)).fetchall()
        new = schema_db.execute(NEW_QUERY_NO_MODID, (probe,)).fetchall()
        assert sorted(old) == sorted(new), f"divergence for {probe!r}"


# ---------------------------------------------------------------------------
# End-to-end through the real function
# ---------------------------------------------------------------------------
def test_find_duplicate_download_case_insensitive(schema_db, tmp_path, monkeypatch):
    """Exercise the real _find_duplicate_download, including its
    physical-existence check."""
    import core.api.server as server

    archive = tmp_path / "TheMod.zip"
    archive.write_bytes(b"data")

    schema_db.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, 1, 'The Mod', 555, '1.2', '[]', '[]')
        """,
        (str(archive),),
    )
    schema_db.commit()

    monkeypatch.setattr(
        server, "resolve_absolute_download_path", lambda p: tmp_path / "TheMod.zip"
    )

    cur = schema_db.cursor()
    hit = server._find_duplicate_download(cur, "the mod", "1.2", 555)
    assert hit is not None, "case-insensitive duplicate not detected"
    assert hit[0] == 1

    miss = server._find_duplicate_download(cur, "unrelated mod", "1.2", 555)
    assert miss is None


def test_find_duplicate_allows_reingest_when_file_missing(
    schema_db, tmp_path, monkeypatch
):
    """Preserved behaviour: a DB row whose file is gone must not block re-ingest."""
    import core.api.server as server

    schema_db.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES('C:/gone/Missing.zip', 1, 'Missing Mod', 777, '1.0', '[]', '[]')
        """
    )
    schema_db.commit()

    monkeypatch.setattr(
        server,
        "resolve_absolute_download_path",
        lambda p: tmp_path / "definitely-not-here.zip",
    )

    cur = schema_db.cursor()
    assert server._find_duplicate_download(cur, "Missing Mod", "1.0", 777) is None
