"""L6 (backend half): GET /api/nxm/handoffs must not write.

list_handoffs() used to DELETE stale "duplicate download" rows from
handoff_failures inline, opening a *second* DB connection to do it. The
frontend polled that endpoint once per second, so an idle app issued
continuous writes. The cleanup now lives in _purge_benign_duplicate_failures(),
invoked from _purge_expired_failures() on the write paths.
"""
from __future__ import annotations

import pytest

from core.api.services import handoffs as handoffs_mod


@pytest.fixture
def wired_db(monkeypatch, schema_db):
    """Route handoff DB access at the throwaway schema DB without letting the
    service close the shared fixture connection."""

    class _NoCloseConn:
        def __init__(self, inner):
            self._inner = inner
            self.close_calls = 0

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            self.close_calls += 1

    proxies: list[_NoCloseConn] = []

    def _get_db():
        proxy = _NoCloseConn(schema_db)
        proxies.append(proxy)
        return proxy

    monkeypatch.setattr("core.api.dependencies.get_db", _get_db, raising=True)
    handoffs_mod._HANDOFFS.clear()
    handoffs_mod._HANDOFF_FAILURES.clear()
    yield schema_db, proxies
    handoffs_mod._HANDOFFS.clear()
    handoffs_mod._HANDOFF_FAILURES.clear()


def _insert_failure(conn, file_id: str, message: str, retry_count: int = 1) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO handoff_failures
            (file_id, mod_id, error_message, retry_count, last_attempt_at, handoff_id)
        VALUES (?, 1, ?, ?, datetime('now'), ?)
        """,
        (file_id, message, retry_count, f"ho-{file_id}"),
    )
    conn.commit()


def test_list_handoffs_issues_no_writes(wired_db, recorder):
    """The core assertion: no DELETE/INSERT/UPDATE from the read path."""
    conn, _ = wired_db
    _insert_failure(conn, "111", "duplicate download detected (id=5)")
    _insert_failure(conn, "222", "connection reset by peer")

    conn.set_trace_callback(recorder)
    handoffs_mod.list_handoffs()
    conn.set_trace_callback(None)

    for verb in ("DELETE", "INSERT", "UPDATE", "COMMIT"):
        assert recorder.count(verb) == 0, (
            f"list_handoffs issued a {verb}: {recorder.matching(verb)}"
        )


def test_list_handoffs_opens_one_connection(wired_db):
    """Previously up to two connections per call (read + cleanup)."""
    conn, proxies = wired_db
    _insert_failure(conn, "111", "duplicate download detected (id=5)")

    proxies.clear()
    handoffs_mod.list_handoffs()
    assert len(proxies) == 1, f"expected 1 connection, got {len(proxies)}"


def test_duplicate_failures_are_still_hidden(wired_db):
    """Benign duplicate rows must not surface as failed handoffs."""
    conn, _ = wired_db
    _insert_failure(conn, "111", "duplicate download detected (id=5)")
    _insert_failure(conn, "222", "already exists")
    _insert_failure(conn, "333", "connection reset by peer")

    result = handoffs_mod.list_handoffs()
    file_ids = {str(r["request"].get("file_id")) for r in result}
    assert "333" in file_ids, result
    assert "111" not in file_ids
    assert "222" not in file_ids


def test_real_failures_are_surfaced_with_progress(wired_db):
    conn, _ = wired_db
    _insert_failure(conn, "444", "SSL handshake failed", retry_count=2)

    result = handoffs_mod.list_handoffs()
    assert len(result) == 1
    rec = result[0]
    assert rec["progress"]["stage"] == "failed"
    assert rec["progress"]["error"] == "SSL handshake failed"
    assert rec["progress"]["retry_count"] == 2
    assert rec["progress"]["permanently_failed"] is False


def test_permanently_failed_flag_at_retry_ceiling(wired_db):
    conn, _ = wired_db
    _insert_failure(
        conn, "555", "boom", retry_count=handoffs_mod.MAX_HANDOFF_RETRIES
    )
    result = handoffs_mod.list_handoffs()
    assert result[0]["progress"]["permanently_failed"] is True


def test_purge_helper_removes_duplicate_rows(wired_db):
    """The relocated cleanup must still actually work."""
    conn, _ = wired_db
    _insert_failure(conn, "111", "duplicate download detected (id=5)")
    _insert_failure(conn, "222", "already exists")
    _insert_failure(conn, "333", "connection reset by peer")

    removed = handoffs_mod._purge_benign_duplicate_failures()
    assert removed == 2, removed

    remaining = {
        r[0] for r in conn.execute("SELECT file_id FROM handoff_failures").fetchall()
    }
    assert remaining == {"333"}


def test_purge_expired_failures_triggers_duplicate_cleanup(wired_db):
    """_purge_expired_failures is the write-path hook that now owns cleanup."""
    conn, _ = wired_db
    _insert_failure(conn, "111", "duplicate download detected (id=5)")

    handoffs_mod._purge_expired_failures()

    rows = conn.execute("SELECT file_id FROM handoff_failures").fetchall()
    assert rows == [], rows


def test_in_memory_handoffs_take_precedence(wired_db):
    """A live in-memory handoff must not be duplicated by a synthetic DB row."""
    from core.nexus.nxm import NXMRequest

    conn, _ = wired_db
    _insert_failure(conn, "777", "some earlier error")

    handoffs_mod.register_handoff(
        NXMRequest(
            raw="nxm://marvelrivals/mods/1/files/777",
            game_domain="marvelrivals",
            mod_id=1,
            file_id=777,
            query={},
        ),
        metadata={},
    )

    result = handoffs_mod.list_handoffs()
    matching = [r for r in result if str(r["request"].get("file_id")) == "777"]
    assert len(matching) == 1, matching
