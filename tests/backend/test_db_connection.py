"""M1: get_db() must not checkpoint the WAL, and must pool per thread.

get_db() ran `PRAGMA wal_checkpoint(RESTART)` on every call -- every API
request. RESTART writes the entire WAL back into the main database and blocks
until existing readers finish, so it defeated WAL mode and made the polled
endpoints a continuous source of I/O and lock contention.
"""
from __future__ import annotations

import sqlite3
import threading

import pytest

import core.api.dependencies as deps


@pytest.fixture
def isolated_pool(monkeypatch, tmp_path):
    """Point get_db() at a throwaway DB and reset pool state around each test."""
    db_path = tmp_path / "pool.db"

    from core.db.db import get_connection as real_get_connection

    def _get_connection(path=None):
        return real_get_connection(str(db_path))

    monkeypatch.setattr(deps, "get_connection", _get_connection)
    monkeypatch.setattr(deps, "init_schema", lambda conn: None)
    monkeypatch.setattr(deps, "_SCHEMA_READY", True, raising=False)

    deps.close_thread_connection()
    yield db_path
    deps.close_thread_connection()


# ---------------------------------------------------------------------------
# The headline assertion
# ---------------------------------------------------------------------------
def test_get_db_issues_no_wal_checkpoint(isolated_pool, recorder):
    """Simulate a request: no wal_checkpoint statement may be issued."""
    statements: list[str] = []
    original_connect = sqlite3.connect

    def tracing_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    import core.db.db as db_mod

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(db_mod.sqlite3, "connect", tracing_connect)
        conn = deps.get_db()
        try:
            conn.execute("SELECT 1").fetchone()
        finally:
            conn.close()

    checkpoints = [s for s in statements if "wal_checkpoint" in s.lower()]
    assert checkpoints == [], f"get_db still checkpoints the WAL: {checkpoints}"


def test_no_checkpoint_across_many_requests(isolated_pool):
    """The polled endpoints call get_db repeatedly; none may checkpoint."""
    statements: list[str] = []
    conn = deps.get_db()
    conn.raw.set_trace_callback(statements.append)
    try:
        for _ in range(50):
            c = deps.get_db()
            c.execute("SELECT 1").fetchone()
            c.close()
    finally:
        conn.raw.set_trace_callback(None)
    assert not any("wal_checkpoint" in s.lower() for s in statements), statements


# ---------------------------------------------------------------------------
# Pooling
# ---------------------------------------------------------------------------
def test_same_connection_reused_within_thread(isolated_pool):
    a = deps.get_db()
    b = deps.get_db()
    assert a is b, "get_db should return the cached per-thread connection"
    assert a.raw is b.raw


def test_close_returns_connection_to_pool(isolated_pool):
    """Existing call sites close in a finally block; that must not destroy the
    pooled handle."""
    a = deps.get_db()
    a.close()
    b = deps.get_db()
    assert b.raw is a.raw, "close() should return the connection, not destroy it"
    assert b.execute("SELECT 1").fetchone() == (1,)


def test_close_rolls_back_open_transaction(isolated_pool):
    """A leaked transaction must not poison the next user of the connection."""
    conn = deps.get_db()
    conn.execute("CREATE TABLE IF NOT EXISTS t(x INTEGER)")
    conn.commit()
    conn.execute("BEGIN")
    conn.execute("INSERT INTO t(x) VALUES (1)")
    assert conn.raw.in_transaction
    conn.close()

    nxt = deps.get_db()
    assert not nxt.raw.in_transaction
    assert nxt.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 0


def test_distinct_connections_per_thread(isolated_pool):
    """SQLite connections are not thread-safe; each thread needs its own.

    NOTE: hold strong references to the connection objects. Comparing id() of
    objects that have already been released is unsound -- CPython recycles the
    address, so a later thread's connection can land on a freed one's id and the
    assertion flakes.
    """
    raw_conns: list[sqlite3.Connection] = []
    lock = threading.Lock()
    errors: list[BaseException] = []

    def worker():
        try:
            conn = deps.get_db()
            conn.execute("SELECT 1").fetchone()
            with lock:
                raw_conns.append(conn.raw)  # strong ref: id() stays valid
        except BaseException as exc:  # pragma: no cover - surfaced below
            with lock:
                errors.append(exc)
        finally:
            deps.close_thread_connection()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(raw_conns) == 4
    # All four objects are still alive here, so identity comparison is sound.
    assert len({id(c) for c in raw_conns}) == 4, "connections leaked across threads"


def test_concurrent_opens_do_not_deadlock_on_journal_mode(isolated_pool):
    """Regression: get_connection() used to issue `PRAGMA journal_mode = WAL`
    unconditionally on every open. That takes a brief exclusive lock, so
    simultaneous openers raced and one raised OperationalError
    ("database is locked"). journal_mode is persistent, so it is now only set
    when the database is not already in WAL.
    """
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(8)

    def worker():
        try:
            start.wait(timeout=10)  # maximise contention
            conn = deps.get_db()
            conn.execute("SELECT 1").fetchone()
        except BaseException as exc:
            with lock:
                errors.append(exc)
        finally:
            deps.close_thread_connection()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent opens failed: {errors}"


def test_journal_mode_is_wal_after_open(isolated_pool):
    conn = deps.get_db()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_force_close_drops_the_cached_connection(isolated_pool):
    a = deps.get_db()
    a.force_close()
    b = deps.get_db()
    assert b.raw is not a.raw


def test_broken_connection_is_replaced(isolated_pool):
    """A handle closed out from under the pool must be transparently replaced."""
    a = deps.get_db()
    a.raw.close()  # break it without going through the proxy
    b = deps.get_db()
    assert b.raw is not a.raw
    assert b.execute("SELECT 1").fetchone() == (1,)


def test_pool_generation_invalidates_other_threads(isolated_pool):
    """Bootstrap/restore replace the DB file on a worker thread; every thread's
    cached handle must be retired, not just the caller's."""
    holder: dict[str, object] = {}
    ready = threading.Event()
    proceed = threading.Event()
    done = threading.Event()

    def worker():
        holder["first"] = deps.get_db()
        ready.set()
        proceed.wait(timeout=5)
        holder["second"] = deps.get_db()
        done.set()
        deps.close_thread_connection()

    t = threading.Thread(target=worker)
    t.start()
    assert ready.wait(timeout=5)

    # Simulate a DB-file swap happening on a different thread.
    deps.invalidate_connection_pool()

    proceed.set()
    assert done.wait(timeout=5)
    t.join()

    assert holder["first"] is not holder["second"], (
        "worker thread kept a stale connection after pool invalidation"
    )


def test_reset_schema_cache_invalidates_pool(isolated_pool):
    a = deps.get_db()
    deps.reset_schema_cache()
    b = deps.get_db()
    assert b.raw is not a.raw


# ---------------------------------------------------------------------------
# PRAGMA tuning
# ---------------------------------------------------------------------------
def test_connection_pragmas(isolated_pool):
    conn = deps.get_db()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # Negative cache_size is a KiB budget; SQLite reports it back as given.
        assert conn.execute("PRAGMA cache_size").fetchone()[0] == -20000
    finally:
        conn.close()


def test_wal_readers_see_committed_writes_without_checkpoint(isolated_pool):
    """The premise behind removing the checkpoint: a second connection observes
    committed data with no checkpoint involved."""
    writer = deps.get_db()
    writer.execute("CREATE TABLE IF NOT EXISTS vis(x INTEGER)")
    writer.commit()
    writer.execute("INSERT INTO vis(x) VALUES (42)")
    writer.commit()

    reader = sqlite3.connect(str(isolated_pool))
    try:
        assert reader.execute("SELECT x FROM vis").fetchone()[0] == 42
    finally:
        reader.close()
