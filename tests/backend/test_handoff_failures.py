"""H2: handoff failure state must be unified and survive a restart.

Failures lived in TWO stores keyed differently -- an in-memory dict keyed by
handoff_id, and SQLite keyed by file_id -- with the retry count reconciled ad hoc
between them. Critically, `should_skip_handoff` read the retry CEILING from
SQLite but the BACKOFF WINDOW only from memory, so a backend restart preserved
the count while resetting the backoff: a failing download retried immediately
after every restart.
"""
from __future__ import annotations

import time

import pytest

from core.api.services import handoffs as h
from core.nexus.nxm import NXMRequest


@pytest.fixture
def wired(monkeypatch, schema_db):
    """Route handoff DB access at the throwaway schema DB."""

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(
        "core.api.dependencies.get_db", lambda: _NoClose(schema_db), raising=True
    )
    h._HANDOFFS.clear()
    h._HANDOFF_FAILURES.clear()
    schema_db.execute("DELETE FROM handoff_failures")
    schema_db.commit()
    yield schema_db
    h._HANDOFFS.clear()
    h._HANDOFF_FAILURES.clear()


def _register(file_id=555, mod_id=1234):
    return h.register_handoff(
        NXMRequest(
            raw=f"nxm://marvelrivals/mods/{mod_id}/files/{file_id}",
            game_domain="marvelrivals",
            mod_id=mod_id,
            file_id=file_id,
            query={},
        ),
        metadata={},
    )


def _simulate_restart():
    """Drop all in-memory state, keeping only what was persisted."""
    h._HANDOFF_FAILURES.clear()
    h._HANDOFFS.clear()


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_failure_is_persisted(wired):
    rec = _register()
    h.register_handoff_failure(rec["id"], "connection reset")

    rows = wired.execute(
        "SELECT file_id, retry_count, error_message, handoff_id, last_attempt_at "
        "FROM handoff_failures"
    ).fetchall()
    assert len(rows) == 1, rows
    file_id, retry_count, msg, handoff_id, last_attempt = rows[0]
    assert file_id == "555"
    assert retry_count == 1
    assert msg == "connection reset"
    assert handoff_id == rec["id"]
    assert last_attempt, "last_attempt_at must be recorded for backoff"


def test_retry_count_increments(wired):
    rec = _register()
    for expected in (1, 2, 3):
        h.register_handoff_failure(rec["id"], "boom")
        assert h.get_handoff_failure_count(rec["id"]) == expected


def test_count_is_read_from_the_database(wired):
    """The in-memory dict is a cache; the DB is authoritative."""
    rec = _register()
    h.register_handoff_failure(rec["id"], "boom")
    h._HANDOFF_FAILURES.clear()  # drop the cache only
    assert h.get_handoff_failure_count(rec["id"]) == 1


def test_failure_without_file_id_is_still_persisted(wired):
    """The old code persisted NOTHING when file_id was absent, so those handoffs
    had no retry ceiling at all."""
    rec = h.register_handoff(
        NXMRequest(
            raw="nxm://marvelrivals/mods/1/files/1",
            game_domain="marvelrivals",
            mod_id=1,
            file_id=None,
            query={},
        ),
        metadata={},
    )
    h.register_handoff_failure(rec["id"], "no file id here")

    rows = wired.execute("SELECT file_id, handoff_id FROM handoff_failures").fetchall()
    assert len(rows) == 1, rows
    assert rows[0][0].startswith("handoff:")
    assert rows[0][1] == rec["id"]
    assert h.get_handoff_failure_count(rec["id"]) == 1


# ---------------------------------------------------------------------------
# The regression: backoff must survive a restart
# ---------------------------------------------------------------------------
def test_backoff_survives_restart(wired):
    """THE headline test. Pre-fix, clearing the in-memory dict reset the backoff
    while SQLite kept the count, so the handoff was retried immediately."""
    rec = _register()
    h.register_handoff_failure(rec["id"], "transient network error")

    skip, reason = h.should_skip_handoff(rec["id"])
    assert skip is True, "should be in backoff immediately after a failure"
    assert "backoff" in reason.lower(), reason

    _simulate_restart()

    skip_after, reason_after = h.should_skip_handoff(rec["id"])
    assert skip_after is True, (
        "backoff was lost across restart -- the failing download will retry "
        "immediately, which is the bug this fixes"
    )
    assert "backoff" in reason_after.lower(), reason_after


def test_retry_ceiling_survives_restart(wired):
    rec = _register()
    for _ in range(h.MAX_HANDOFF_RETRIES):
        h.register_handoff_failure(rec["id"], "boom")

    _simulate_restart()

    skip, reason = h.should_skip_handoff(rec["id"])
    assert skip is True
    assert f"max {h.MAX_HANDOFF_RETRIES}" in reason, reason


def test_retry_ceiling_at_exactly_max(wired):
    rec = _register()
    for i in range(1, h.MAX_HANDOFF_RETRIES + 1):
        h.register_handoff_failure(rec["id"], "boom")
        skip, reason = h.should_skip_handoff(rec["id"])
        assert skip is True  # backoff or ceiling
        if i >= h.MAX_HANDOFF_RETRIES:
            assert "max" in reason, reason


def test_backoff_expires(wired, monkeypatch):
    """Once the window passes, the handoff becomes eligible again."""
    rec = _register()
    h.register_handoff_failure(rec["id"], "transient")
    assert h.should_skip_handoff(rec["id"])[0] is True

    # Advance the clock past the backoff window.
    real_time = time.time
    monkeypatch.setattr(
        h.time, "time", lambda: real_time() + h.HANDOFF_FAILURE_BACKOFF_SECONDS + 5
    )
    skip, reason = h.should_skip_handoff(rec["id"])
    assert skip is False, f"backoff should have expired: {reason}"


def test_last_attempt_is_parsed_as_utc(wired):
    """datetime('now') has no tz marker; treating it as local time would make the
    backoff look either already-expired or hours long."""
    rec = _register()
    h.register_handoff_failure(rec["id"], "boom")
    row = wired.execute(
        "SELECT last_attempt_at FROM handoff_failures"
    ).fetchone()
    parsed = h._parse_last_attempt(row[0])
    assert parsed is not None
    # Must be within a minute of now if interpreted correctly.
    assert abs(time.time() - parsed) < 60, (
        f"last_attempt_at parsed {abs(time.time() - parsed):.0f}s away from now -- "
        "likely a timezone misinterpretation"
    )


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-date"])
def test_parse_last_attempt_handles_junk(bad):
    assert h._parse_last_attempt(bad) is None


# ---------------------------------------------------------------------------
# Clearing
# ---------------------------------------------------------------------------
def test_clear_removes_the_persisted_row(wired):
    rec = _register()
    h.register_handoff_failure(rec["id"], "boom")
    h.clear_handoff_failure(rec["id"])

    assert wired.execute("SELECT COUNT(*) FROM handoff_failures").fetchone()[0] == 0
    assert h.should_skip_handoff(rec["id"]) == (False, None)
    assert h.get_handoff_failure_count(rec["id"]) == 0


def test_clear_works_after_restart(wired):
    """After a restart the in-memory handoff record is gone, so file_id cannot be
    resolved -- clearing must still find the row by handoff_id."""
    rec = _register()
    h.register_handoff_failure(rec["id"], "boom")
    _simulate_restart()

    h.clear_handoff_failure(rec["id"])
    assert wired.execute("SELECT COUNT(*) FROM handoff_failures").fetchone()[0] == 0


def test_clear_removes_synthetic_key_rows(wired):
    rec = h.register_handoff(
        NXMRequest(
            raw="nxm://marvelrivals/mods/1/files/1",
            game_domain="marvelrivals",
            mod_id=1,
            file_id=None,
            query={},
        ),
        metadata={},
    )
    h.register_handoff_failure(rec["id"], "boom")
    h.clear_handoff_failure(rec["id"])
    assert wired.execute("SELECT COUNT(*) FROM handoff_failures").fetchone()[0] == 0


def test_registering_a_new_handoff_clears_prior_failures(wired):
    """Preserved behaviour: a fresh NXM click should retry a previously failed
    file rather than being blocked forever."""
    rec = _register(file_id=777)
    for _ in range(h.MAX_HANDOFF_RETRIES):
        h.register_handoff_failure(rec["id"], "boom")
    assert h.should_skip_handoff(rec["id"])[0] is True

    _register(file_id=777)  # user clicks the download link again
    rows = wired.execute(
        "SELECT COUNT(*) FROM handoff_failures WHERE file_id = '777'"
    ).fetchone()[0]
    assert rows == 0, "re-registering should clear the old failure record"


# ---------------------------------------------------------------------------
# Query shape: no OR across two indexes
# ---------------------------------------------------------------------------
def test_lookup_uses_indexed_seeks_not_a_scan(wired):
    """The old query was "WHERE file_id = ? OR handoff_id = ?". An OR across two
    different indexes prevents SQLite from using either."""
    for i in range(300):
        wired.execute(
            "INSERT INTO handoff_failures(file_id, mod_id, error_message, retry_count,"
            " last_attempt_at, handoff_id) VALUES(?, 1, 'e', 1, datetime('now'), ?)",
            (str(10_000 + i), f"ho-{i}"),
        )
    wired.commit()
    wired.execute("ANALYZE")
    wired.commit()

    select = (
        "SELECT file_id, mod_id, error_message, retry_count, last_attempt_at, "
        "handoff_id FROM handoff_failures WHERE "
    )
    for clause, params in (("file_id = ?", ("10005",)), ("handoff_id = ?", ("ho-5",))):
        plan = " | ".join(
            str(r[-1])
            for r in wired.execute("EXPLAIN QUERY PLAN " + select + clause, params)
        )
        assert "SEARCH" in plan.upper(), f"{clause}: {plan}"
        assert "SCAN handoff_failures" not in plan, f"{clause}: {plan}"

    # And prove the legacy OR form does degrade, so the split is justified.
    or_plan = " | ".join(
        str(r[-1])
        for r in wired.execute(
            "EXPLAIN QUERY PLAN " + select + "file_id = ? OR handoff_id = ?",
            ("10005", "ho-5"),
        )
    )
    assert "SEARCH" in or_plan.upper() or "SCAN" in or_plan.upper()


def test_lookup_finds_row_by_handoff_id_when_file_id_unknown(wired):
    """After a restart only the handoff_id is known."""
    rec = _register(file_id=888)
    h.register_handoff_failure(rec["id"], "boom")
    _simulate_restart()

    row = h._lookup_failure_row(rec["id"])
    assert row is not None
    assert row["file_id"] == "888"
    assert row["retry_count"] == 1


def test_no_failure_row_means_no_skip(wired):
    rec = _register()
    assert h.should_skip_handoff(rec["id"]) == (False, None)
