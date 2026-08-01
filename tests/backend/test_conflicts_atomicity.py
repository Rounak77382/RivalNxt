"""M5: rebuild_conflicts must be atomic.

The sequence was:
    DELETE FROM asset_conflicts;
    cur.executescript(agg_sql)      # INSERT ... SELECT
    cur.executescript(part_sql)     # INSERT ... SELECT

Python's sqlite3.executescript() issues an implicit COMMIT before executing, so
the DELETE landed as its own committed transaction, separate from the INSERT that
repopulates the table. Any concurrent reader on another connection -- and
/api/conflicts, /api/conflicts/active and /api/mods/{id}/conflicts each open their
own -- could observe an EMPTY conflicts table mid-rebuild.

This is reachable in production: set_active_paks rebuilds the active snapshot
while the UI polls conflicts.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from core.db.db import init_schema, rebuild_conflicts, run_migrations


@pytest.fixture
def conflict_db(tmp_path: Path):
    """On-disk DB seeded with three mods that all provide the same assets."""
    db_path = tmp_path / "conflicts.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    init_schema(conn)
    run_migrations(conn)

    shared_assets = [f"/Game/Shared/asset_{i}.uasset" for i in range(40)]
    for mod_id in (1, 2, 3):
        conn.execute(
            "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(?, 'marvelrivals', ?)",
            (mod_id, f"Mod {mod_id}"),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
            VALUES(?, ?, ?, ?, '1.0', ?, ?)
            """,
            (
                f"C:/dl/Mod{mod_id}.zip",
                mod_id,
                f"Mod {mod_id}",
                mod_id,
                f'["mod{mod_id}.pak"]',
                f'["mod{mod_id}.pak"]',
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO mod_paks(pak_name, mod_id, source_zip, local_download_id)
            VALUES(?, ?, ?, ?)
            """,
            (f"mod{mod_id}.pak", mod_id, f"Mod{mod_id}.zip", mod_id),
        )
        conn.executemany(
            "INSERT OR REPLACE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
            [(f"mod{mod_id}.pak", a) for a in shared_assets],
        )
    conn.commit()

    # Prime both snapshots so there is something for a reader to see.
    rebuild_conflicts(conn, active_only=None)

    try:
        yield conn, db_path
    finally:
        try:
            conn.close()
        except Exception:
            pass


def test_seed_actually_produces_conflicts(conflict_db):
    conn, _ = conflict_db
    n_all = conn.execute("SELECT COUNT(*) FROM asset_conflicts").fetchone()[0]
    n_active = conn.execute("SELECT COUNT(*) FROM asset_conflicts_active").fetchone()[0]
    assert n_all == 40, n_all
    assert n_active == 40, n_active


# ---------------------------------------------------------------------------
# The headline test
# ---------------------------------------------------------------------------
def test_reader_never_sees_empty_conflicts_during_rebuild(conflict_db):
    """A concurrent reader must never observe zero conflicts while conflicts
    exist. FAILS on the pre-fix code."""
    conn, db_path = conflict_db

    stop = threading.Event()
    zero_sightings: list[str] = []
    reader_errors: list[BaseException] = []
    rebuild_errors: list[BaseException] = []
    reads_done = [0]

    def rebuilder():
        # Own connection: sqlite3 objects cannot be shared across threads.
        wconn = sqlite3.connect(str(db_path))
        wconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            for _ in range(30):
                if stop.is_set():
                    break
                rebuild_conflicts(wconn, active_only=None)
        except BaseException as exc:
            rebuild_errors.append(exc)
        finally:
            wconn.close()
            stop.set()

    def reader():
        rconn = sqlite3.connect(str(db_path))
        rconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            while not stop.is_set() and reads_done[0] < 1000:
                for table in ("asset_conflicts", "asset_conflicts_active"):
                    count = rconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    reads_done[0] += 1
                    if count == 0:
                        zero_sightings.append(table)
        except BaseException as exc:
            reader_errors.append(exc)
        finally:
            rconn.close()

    t_read = threading.Thread(target=reader)
    t_build = threading.Thread(target=rebuilder)
    t_read.start()
    t_build.start()
    t_build.join(timeout=90)
    stop.set()
    t_read.join(timeout=30)

    assert not rebuild_errors, rebuild_errors
    assert not reader_errors, reader_errors
    assert reads_done[0] > 50, f"reader barely ran ({reads_done[0]} reads)"
    assert zero_sightings == [], (
        f"reader saw an EMPTY conflicts table {len(zero_sightings)} time(s) "
        f"mid-rebuild (tables: {set(zero_sightings)}) -- the rebuild is not atomic"
    )


def test_reader_always_sees_the_full_row_count(conflict_db):
    """Stronger form: not just non-zero, but never a partially populated table."""
    conn, db_path = conflict_db

    stop = threading.Event()
    bad_counts: list[int] = []
    reads = [0]

    def rebuilder():
        wconn = sqlite3.connect(str(db_path))
        wconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            for _ in range(25):
                rebuild_conflicts(wconn, active_only=None)
        finally:
            wconn.close()
            stop.set()

    def reader():
        rconn = sqlite3.connect(str(db_path))
        rconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            while not stop.is_set() and reads[0] < 800:
                count = rconn.execute("SELECT COUNT(*) FROM asset_conflicts").fetchone()[0]
                reads[0] += 1
                if count != 40:
                    bad_counts.append(count)
        finally:
            rconn.close()

    t_read = threading.Thread(target=reader)
    t_build = threading.Thread(target=rebuilder)
    t_read.start()
    t_build.start()
    t_build.join(timeout=90)
    stop.set()
    t_read.join(timeout=30)

    assert reads[0] > 50, f"reader barely ran ({reads[0]} reads)"
    assert bad_counts == [], (
        f"reader saw partial row counts {sorted(set(bad_counts))} (expected always 40)"
    )


def test_participants_stay_consistent_with_conflicts(conflict_db):
    """A reader must never see conflicts without their participant rows."""
    conn, db_path = conflict_db

    stop = threading.Event()
    orphans: list[tuple[int, int]] = []
    reads = [0]

    def rebuilder():
        wconn = sqlite3.connect(str(db_path))
        wconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            for _ in range(25):
                rebuild_conflicts(wconn, active_only=None)
        finally:
            wconn.close()
            stop.set()

    def reader():
        rconn = sqlite3.connect(str(db_path))
        rconn.execute("PRAGMA busy_timeout = 15000;")
        try:
            while not stop.is_set() and reads[0] < 800:
                row = rconn.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM asset_conflicts),
                           (SELECT COUNT(*) FROM asset_conflict_participants)
                    """
                ).fetchone()
                reads[0] += 1
                conflicts, participants = row
                # 40 conflicting assets x 3 providers = 120 participants.
                if conflicts and not participants:
                    orphans.append((conflicts, participants))
        finally:
            rconn.close()

    t_read = threading.Thread(target=reader)
    t_build = threading.Thread(target=rebuilder)
    t_read.start()
    t_build.start()
    t_build.join(timeout=90)
    stop.set()
    t_read.join(timeout=30)

    assert reads[0] > 50, f"reader barely ran ({reads[0]} reads)"
    assert orphans == [], f"saw conflicts with no participant rows: {orphans[:5]}"


# ---------------------------------------------------------------------------
# Correctness must be unchanged
# ---------------------------------------------------------------------------
def test_rebuild_results_are_stable_across_runs(conflict_db):
    conn, _ = conflict_db
    first = rebuild_conflicts(conn, active_only=None)
    second = rebuild_conflicts(conn, active_only=None)
    assert first == second


def test_active_only_variants(conflict_db):
    conn, _ = conflict_db
    only_all = rebuild_conflicts(conn, active_only=False)
    assert "asset_conflicts" in only_all
    assert "asset_conflicts_active" not in only_all

    only_active = rebuild_conflicts(conn, active_only=True)
    assert "asset_conflicts_active" in only_active
    assert "asset_conflicts" not in only_active

    both = rebuild_conflicts(conn, active_only=None)
    assert set(both) == {"asset_conflicts", "asset_conflicts_active"}


def test_participants_are_populated(conflict_db):
    conn, _ = conflict_db
    rebuild_conflicts(conn, active_only=None)
    n = conn.execute("SELECT COUNT(*) FROM asset_conflict_participants").fetchone()[0]
    assert n == 120, n  # 40 assets x 3 providers


def test_detected_at_is_preserved_across_rebuilds(conflict_db):
    """Returning conflicts must keep their original first-detected timestamp."""
    conn, _ = conflict_db
    conn.execute(
        "UPDATE asset_conflicts SET detected_at = '2020-01-01 00:00:00' "
        "WHERE asset_path = '/Game/Shared/asset_0.uasset'"
    )
    conn.commit()

    rebuild_conflicts(conn, active_only=False)

    row = conn.execute(
        "SELECT detected_at FROM asset_conflicts WHERE asset_path = ?",
        ("/Game/Shared/asset_0.uasset",),
    ).fetchone()
    assert row[0] == "2020-01-01 00:00:00", row


def test_non_conflicting_assets_are_excluded(conflict_db):
    """Only assets provided by more than one mod are conflicts."""
    conn, _ = conflict_db
    conn.execute(
        "INSERT OR REPLACE INTO pak_assets(pak_name, asset_path) VALUES('mod1.pak', '/Game/Unique/only.uasset')"
    )
    conn.commit()
    rebuild_conflicts(conn, active_only=False)

    row = conn.execute(
        "SELECT COUNT(*) FROM asset_conflicts WHERE asset_path = '/Game/Unique/only.uasset'"
    ).fetchone()
    assert row[0] == 0


def test_removing_a_provider_clears_the_conflict(conflict_db):
    conn, _ = conflict_db
    conn.execute("DELETE FROM pak_assets WHERE pak_name IN ('mod2.pak', 'mod3.pak')")
    conn.commit()
    rebuild_conflicts(conn, active_only=False)
    assert conn.execute("SELECT COUNT(*) FROM asset_conflicts").fetchone()[0] == 0
