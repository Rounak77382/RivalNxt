"""Shared dependencies and environment checks for the API layer."""
from __future__ import annotations

import logging
import socket
import sqlite3
import threading
from typing import Iterable, Optional, Tuple

from core.db import get_connection, init_schema

logger = logging.getLogger("modmanager.api.dependencies")

_REQUIRED_DNS_HOSTS: Tuple[str, ...] = ("api.nexusmods.com",)
_SCHEMA_READY = False

# Per-thread connection cache. Uvicorn runs sync endpoints on a bounded
# threadpool, so this keeps one connection per worker thread instead of opening
# and tearing down a fresh one on every request.
_THREAD_STATE = threading.local()

# Pool generation. Bumped whenever the database file underneath us is replaced
# (bootstrap rebuild, backup restore). Cached connections carry the generation
# they were created at; a stale one is discarded on next use. This is what makes
# invalidation work across *all* worker threads -- close_thread_connection()
# alone only affects the calling thread, and the maintenance tasks run on their
# own thread.
_POOL_GENERATION = 0
_POOL_GENERATION_LOCK = threading.Lock()


def verify_required_dns_hosts(hosts: Iterable[str] = _REQUIRED_DNS_HOSTS) -> None:
    """Ensure critical Nexus hosts resolve before accepting requests."""
    failures = []
    for host in hosts:
        try:
            socket.getaddrinfo(host, None)
        except socket.gaierror as exc:  # pragma: no cover - network failure
            failures.append(f"{host} ({exc})")
    if failures:
        logger.warning(
            "DNS check failed for Nexus hosts: %s. Update your DNS resolver before using Mod Manager Download.",
            ", ".join(failures),
        )


def _ensure_schema_initialised() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    print("Initializing database schema...")
    conn = get_connection()
    try:
        init_schema(conn)
    finally:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive close
            pass
    _SCHEMA_READY = True


class _PooledConnection:
    """Thin proxy that keeps a cached connection alive across ``close()``.

    Call sites throughout core/api/server.py open a connection and close it in a
    ``finally`` block. Rather than rewrite every one of those, ``close()`` rolls
    back any open transaction and hands the connection back to the thread-local
    cache. ``force_close()`` really closes it.
    """

    __slots__ = ("_conn", "_closed", "_generation")

    def __init__(self, conn: sqlite3.Connection, generation: int) -> None:
        self._conn = conn
        self._closed = False
        self._generation = generation

    @property
    def generation(self) -> int:
        return self._generation

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __enter__(self):
        return self._conn.__enter__()

    def __exit__(self, *exc_info):
        return self._conn.__exit__(*exc_info)

    @property
    def raw(self) -> sqlite3.Connection:
        return self._conn

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        """Return the connection to the pool instead of closing it."""
        if self._closed:
            return
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except Exception:
            # If rollback fails the connection is not safe to reuse.
            self.force_close()

    def force_close(self) -> None:
        self._closed = True
        try:
            self._conn.close()
        except Exception:
            pass
        if getattr(_THREAD_STATE, "conn", None) is self:
            _THREAD_STATE.conn = None


def get_db():
    """Return a SQLite connection with schema guaranteed to exist.

    This used to run ``PRAGMA wal_checkpoint(RESTART)`` on every call -- i.e. on
    every API request. That forces the entire WAL to be written back into the
    main database file and restarted, and RESTART additionally blocks until
    existing readers finish. It defeated the purpose of WAL mode and turned the
    polled endpoints into a continuous source of disk I/O and lock contention.

    WAL readers already observe every committed write without a checkpoint. The
    only operations that genuinely need a clean database file are the
    maintenance tasks, and ``_task_bootstrap_rebuild`` already issues its own
    explicit ``wal_checkpoint(TRUNCATE)``.
    """
    _ensure_schema_initialised()

    with _POOL_GENERATION_LOCK:
        generation = _POOL_GENERATION

    cached: Optional[_PooledConnection] = getattr(_THREAD_STATE, "conn", None)
    if cached is not None and not cached.closed:
        if cached.generation != generation:
            # The database file was swapped out from under us on another thread.
            cached.force_close()
        else:
            try:
                cached.raw.execute("SELECT 1").fetchone()
                return cached
            except Exception:
                # Stale or broken handle.
                cached.force_close()

    pooled = _PooledConnection(get_connection(), generation)
    _THREAD_STATE.conn = pooled
    return pooled


def close_thread_connection() -> None:
    """Really close this thread's cached connection.

    Required before any operation that replaces the database file on disk
    (bootstrap rebuild, backup restore): a stale handle keeps the old inode
    alive and, on Windows, can block the replacement outright.
    """
    cached: Optional[_PooledConnection] = getattr(_THREAD_STATE, "conn", None)
    if cached is not None:
        cached.force_close()
    _THREAD_STATE.conn = None


def invalidate_connection_pool() -> None:
    """Retire every cached connection across all threads.

    Bumps the pool generation so other worker threads drop their handles on next
    use, and force-closes this thread's handle immediately.
    """
    global _POOL_GENERATION
    with _POOL_GENERATION_LOCK:
        _POOL_GENERATION += 1
    close_thread_connection()


def reset_schema_cache() -> None:
    """Force schema re-initialization on next get_db() call.

    Use this after operations that rebuild the database structure,
    such as migrations or full bootstrap rebuilds.
    """
    global _SCHEMA_READY
    _SCHEMA_READY = False
    invalidate_connection_pool()


__all__ = [
    "get_db",
    "verify_required_dns_hosts",
    "reset_schema_cache",
    "close_thread_connection",
    "invalidate_connection_pool",
]
