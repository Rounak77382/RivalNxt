"""M6: the detected_at restore must not be one UPDATE per asset path.

rebuild_conflicts read every (asset_path, detected_at) pair into a Python dict
and then issued a separate UPDATE for each one after repopulating the table --
thousands of round trips inside the rebuild. The snapshot now lives in a TEMP
TABLE and is carried forward by a LEFT JOIN inside the INSERT, so the second
pass is gone entirely.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from core.db.db import init_schema, rebuild_conflicts, run_migrations

N_ASSETS = 10_000
N_MODS = 3


@pytest.fixture
def big_conflict_db(tmp_path: Path):
    """10k assets each provided by 3 mods -> 10k conflicts, 30k participants."""
    db_path = tmp_path / "perf.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 15000;")
    conn.execute("PRAGMA synchronous = OFF;")  # test-only: keep setup fast
    init_schema(conn)
    run_migrations(conn)

    assets = [f"/Game/Shared/asset_{i:06d}.uasset" for i in range(N_ASSETS)]
    for mod_id in range(1, N_MODS + 1):
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
            [(f"mod{mod_id}.pak", a) for a in assets],
        )
    conn.commit()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _legacy_detected_at_restore(conn, conflicts_tbl: str) -> float:
    """Run the OLD approach: snapshot into Python, then UPDATE per row."""
    cur = conn.cursor()
    start = time.perf_counter()
    prev: dict[str, str] = {}
    rows = cur.execute(
        f"SELECT asset_path, detected_at FROM {conflicts_tbl} WHERE detected_at IS NOT NULL"
    ).fetchall()
    for row in rows:
        prev[row[0]] = row[1]
    for asset_path, detected_at in prev.items():
        cur.execute(
            f"UPDATE {conflicts_tbl} SET detected_at = ? WHERE asset_path = ?",
            (detected_at, asset_path),
        )
    conn.commit()
    return time.perf_counter() - start


def test_seed_is_the_expected_size(big_conflict_db):
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=False)
    assert conn.execute("SELECT COUNT(*) FROM asset_conflicts").fetchone()[0] == N_ASSETS
    assert (
        conn.execute("SELECT COUNT(*) FROM asset_conflict_participants").fetchone()[0]
        == N_ASSETS * N_MODS
    )


def _new_detected_at_carry_forward(conn, conflicts_tbl: str) -> float:
    """Run the NEW approach's detected_at handling in isolation: temp-table
    snapshot + indexed LEFT JOIN carry-forward + drop."""
    cur = conn.cursor()
    start = time.perf_counter()
    cur.execute("DROP TABLE IF EXISTS temp._bench_prev;")
    cur.execute(
        f"""
        CREATE TEMP TABLE _bench_prev AS
        SELECT asset_path, detected_at FROM {conflicts_tbl}
        WHERE detected_at IS NOT NULL;
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS temp.idx__bench_prev ON _bench_prev(asset_path);"
    )
    # The carry-forward itself: one set-based statement, not N round trips.
    cur.execute(
        f"""
        UPDATE {conflicts_tbl} SET detected_at = COALESCE(
            (SELECT detected_at FROM _bench_prev p WHERE p.asset_path = {conflicts_tbl}.asset_path),
            detected_at
        );
        """
    )
    cur.execute("DROP TABLE IF EXISTS temp._bench_prev;")
    conn.commit()
    return time.perf_counter() - start


def test_detected_at_handling_avoids_per_row_round_trips(big_conflict_db, recorder):
    """Compare like with like: the detected_at snapshot+restore step alone, old
    design vs new design, over the same 10k populated rows.

    The claim under test is algorithmic -- O(N) statements became O(1) -- so that
    is what is asserted. Wall clock is printed as a diagnostic but deliberately
    NOT asserted on: measured idle, the ratio here runs 2.9x-4.4x, but the new
    path's absolute cost is only ~30 ms, so a single scheduling hiccup on a loaded
    machine (or a shared CI runner) moves the ratio more than the optimisation
    does. A statement count cannot be perturbed by load.

    NOTE on scope. The total rebuild at this size is ~1s and is dominated by the
    pak_assets aggregation, not by this step -- so the improvement claimed applies
    to the detected_at handling that actually changed, not to end-to-end rebuild
    time. Claiming the latter would be measuring aggregation cost and attributing
    it here.
    """
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=False)  # 10k rows with detected_at set

    # Counts come from sqlite's own trace callback, not from a hand-maintained
    # counter in the helpers -- otherwise the assertions below would just be
    # restating a literal I wrote.
    conn.set_trace_callback(recorder)
    legacy_ms = _legacy_detected_at_restore(conn, "asset_conflicts")
    conn.set_trace_callback(None)
    legacy_stmts = len(recorder.statements)

    recorder.reset()
    conn.set_trace_callback(recorder)
    new_ms = _new_detected_at_carry_forward(conn, "asset_conflicts")
    conn.set_trace_callback(None)
    new_stmts = len(recorder.statements)

    print(
        f"\n  detected_at handling over {N_ASSETS} rows:"
        f"\n    OLD  snapshot into dict + {N_ASSETS} UPDATEs : "
        f"{legacy_ms*1000:8.1f} ms, {legacy_stmts:6d} statements"
        f"\n    NEW  temp table + set-based carry-forward   : "
        f"{new_ms*1000:8.1f} ms, {new_stmts:6d} statements"
        f"\n    round trips saved: {legacy_stmts - new_stmts}"
    )

    # The old design issued one UPDATE per populated row (plus the snapshot SELECT
    # and the transaction control statements the trace also reports).
    assert legacy_stmts >= N_ASSETS, f"{legacy_stmts} statements for {N_ASSETS} rows"
    # The new design is a fixed handful, independent of row count.
    assert new_stmts <= 12, f"{new_stmts} statements: {recorder.statements}"
    assert legacy_stmts / new_stmts > 100, (
        f"expected an order-of-magnitude round-trip reduction; "
        f"{legacy_stmts} -> {new_stmts}"
    )


def test_no_per_row_updates_are_issued(big_conflict_db, recorder):
    """Structural proof: the rebuild must issue no per-asset UPDATE statements."""
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=False)  # populate detected_at values

    conn.set_trace_callback(recorder)
    rebuild_conflicts(conn, active_only=False)
    conn.set_trace_callback(None)

    updates = [s for s in recorder.statements if s.strip().upper().startswith("UPDATE")]
    assert updates == [], f"{len(updates)} UPDATE statements issued, e.g. {updates[:3]}"

    # And the total statement count must be bounded, not proportional to rows.
    assert len(recorder.statements) < 60, (
        f"{len(recorder.statements)} statements for a {N_ASSETS}-row rebuild; "
        "should be a small constant"
    )


def test_detected_at_is_preserved_at_scale(big_conflict_db):
    """Correctness at scale: original timestamps must survive the rebuild."""
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=False)

    conn.execute("UPDATE asset_conflicts SET detected_at = '2019-06-06 06:06:06'")
    conn.commit()

    rebuild_conflicts(conn, active_only=False)

    distinct = conn.execute(
        "SELECT DISTINCT detected_at FROM asset_conflicts"
    ).fetchall()
    assert distinct == [("2019-06-06 06:06:06",)], distinct[:5]


def test_new_conflicts_get_current_detected_at(big_conflict_db):
    """A conflict appearing for the first time must get now(), not NULL."""
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=False)
    conn.execute("UPDATE asset_conflicts SET detected_at = '2019-06-06 06:06:06'")
    conn.commit()

    # Introduce a brand-new conflicting asset across two mods.
    conn.executemany(
        "INSERT OR REPLACE INTO pak_assets(pak_name, asset_path) VALUES(?, ?)",
        [("mod1.pak", "/Game/Brand/new.uasset"), ("mod2.pak", "/Game/Brand/new.uasset")],
    )
    conn.commit()
    rebuild_conflicts(conn, active_only=False)

    row = conn.execute(
        "SELECT detected_at FROM asset_conflicts WHERE asset_path = '/Game/Brand/new.uasset'"
    ).fetchone()
    assert row is not None, "new conflict was not recorded"
    assert row[0] is not None
    assert row[0] != "2019-06-06 06:06:06"

    old = conn.execute(
        "SELECT detected_at FROM asset_conflicts WHERE asset_path = '/Game/Shared/asset_000000.uasset'"
    ).fetchone()
    assert old[0] == "2019-06-06 06:06:06", "existing timestamp was clobbered"


def test_temp_table_does_not_leak(big_conflict_db):
    """_prev_detected must be dropped, or a second rebuild in the same
    connection would hit "table already exists"."""
    conn = big_conflict_db
    rebuild_conflicts(conn, active_only=None)
    rebuild_conflicts(conn, active_only=None)  # must not raise

    leaked = conn.execute(
        "SELECT name FROM temp.sqlite_master WHERE name LIKE '_prev_detected%'"
    ).fetchall()
    assert leaked == [], leaked
