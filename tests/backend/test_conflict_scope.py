"""M9: the ingest conflict rebuild must be scoped and coalesced.

_ingest_resolved_download called _safe_rebuild_conflicts(active_only=None), which
rebuilds BOTH snapshots. Each _rebuild scans pak_assets JOIN mod_paks twice (once
for the aggregate, once for participants), so that was four full scans per mod --
and it ran synchronously for every mod in a burst.

A freshly ingested mod is not active yet (active_paks is written as '[]'), so the
_active snapshot cannot have changed: active_only=False is correct.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import core.api.server as server


@pytest.fixture(autouse=True)
def _reset_debounce():
    server._wait_for_conflict_rebuild(timeout=10)
    with server._CONFLICT_REBUILD_LOCK:
        server._CONFLICT_REBUILD_PENDING = False
        server._CONFLICT_REBUILD_ACTIVE_ONLY = None
        server._CONFLICT_REBUILD_THREAD = None
        server._CONFLICT_REBUILD_RUNS = 0
    yield
    server._wait_for_conflict_rebuild(timeout=10)


@pytest.fixture
def fast_debounce(monkeypatch):
    monkeypatch.setattr(server, "CONFLICT_REBUILD_DEBOUNCE_SECONDS", 0.15)


# ---------------------------------------------------------------------------
# _merge_active_only: scope widening
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "current,incoming,expected",
    [
        (False, False, False),   # both want "all" only
        (True, True, True),      # both want "active" only
        (False, True, None),     # different scopes -> must cover both
        (True, False, None),
        (None, False, None),     # None already means both
        (None, True, None),
        (False, None, None),
        (True, None, None),
        (None, None, None),
    ],
)
def test_merge_active_only(current, incoming, expected):
    assert server._merge_active_only(current, incoming) is expected


# ---------------------------------------------------------------------------
# Coalescing
# ---------------------------------------------------------------------------
def test_burst_of_requests_produces_one_rebuild(monkeypatch, fast_debounce):
    calls: list[object] = []

    def spy(conn, *, active_only, purpose, raise_on_error=False):
        calls.append(active_only)
        return {"asset_conflicts": 0}

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", spy)
    monkeypatch.setattr(server, "get_db", lambda: _DummyConn())

    for _ in range(5):
        assert server._schedule_conflict_rebuild(active_only=False, purpose="t") is True

    assert server._wait_for_conflict_rebuild(timeout=15)
    assert len(calls) == 1, f"expected 1 coalesced rebuild, got {len(calls)}"
    assert calls[0] is False


def test_mixed_scopes_widen_to_both(monkeypatch, fast_debounce):
    calls: list[object] = []

    def spy(conn, *, active_only, purpose, raise_on_error=False):
        calls.append(active_only)
        return {}

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", spy)
    monkeypatch.setattr(server, "get_db", lambda: _DummyConn())

    server._schedule_conflict_rebuild(active_only=False, purpose="a")
    server._schedule_conflict_rebuild(active_only=True, purpose="b")

    assert server._wait_for_conflict_rebuild(timeout=15)
    assert calls == [None], (
        f"mixed scopes must widen to None (both snapshots), got {calls}"
    )


def test_requests_after_completion_start_a_new_rebuild(monkeypatch, fast_debounce):
    calls: list[object] = []

    def spy(conn, *, active_only, purpose, raise_on_error=False):
        calls.append(active_only)
        return {}

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", spy)
    monkeypatch.setattr(server, "get_db", lambda: _DummyConn())

    server._schedule_conflict_rebuild(active_only=False, purpose="first")
    assert server._wait_for_conflict_rebuild(timeout=15)
    server._schedule_conflict_rebuild(active_only=False, purpose="second")
    assert server._wait_for_conflict_rebuild(timeout=15)

    assert len(calls) == 2, calls


def test_rebuild_failure_does_not_wedge_the_scheduler(monkeypatch, fast_debounce):
    """A failing rebuild must clear the pending flag, or no further rebuild
    would ever be scheduled."""

    def boom(conn, *, active_only, purpose, raise_on_error=False):
        raise RuntimeError("rebuild exploded")

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", boom)
    monkeypatch.setattr(server, "get_db", lambda: _DummyConn())

    server._schedule_conflict_rebuild(active_only=False, purpose="boom")
    assert server._wait_for_conflict_rebuild(timeout=15)

    with server._CONFLICT_REBUILD_LOCK:
        assert server._CONFLICT_REBUILD_PENDING is False
        assert server._CONFLICT_REBUILD_THREAD is None


def test_concurrent_schedulers_still_coalesce(monkeypatch, fast_debounce):
    calls: list[object] = []
    lock = threading.Lock()

    def spy(conn, *, active_only, purpose, raise_on_error=False):
        with lock:
            calls.append(active_only)
        return {}

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", spy)
    monkeypatch.setattr(server, "get_db", lambda: _DummyConn())

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait(timeout=10)
        server._schedule_conflict_rebuild(active_only=False, purpose="race")

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert server._wait_for_conflict_rebuild(timeout=15)
    assert len(calls) == 1, f"8 concurrent requests produced {len(calls)} rebuilds"


class _DummyConn:
    def close(self):
        pass


# ---------------------------------------------------------------------------
# End-to-end through _ingest_resolved_download
# ---------------------------------------------------------------------------
@pytest.fixture
def ingest_harness(monkeypatch, schema_db, tmp_path):
    rebuild_calls: list[object] = []

    def fake_extract_archive(archive_path, dest):
        target = Path(dest) / "thing.pak"
        target.write_bytes(b"\x00")

    monkeypatch.setattr(server, "extract_archive", fake_extract_archive)
    monkeypatch.setattr(
        server,
        "extract_pak_asset_map_from_folder",
        lambda folder, aes_key=None: {"thing.pak": ["/Game/A/x.uasset"]},
    )
    monkeypatch.setattr(
        server, "_sync_mod_metadata", lambda *a, **k: {"synced_mod_id": None}
    )

    def spy(conn, *, active_only, purpose, raise_on_error=False):
        rebuild_calls.append(active_only)
        return {}

    monkeypatch.setattr(server, "_safe_rebuild_conflicts", spy)
    monkeypatch.setattr(server, "CONFLICT_REBUILD_DEBOUNCE_SECONDS", 0.15)

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    return rebuild_calls, tmp_path


def test_five_rapid_ingests_trigger_at_most_two_rebuilds(ingest_harness, monkeypatch):
    """The spec's acceptance criterion: 5 ingests must not mean 5 rebuilds.

    The debounce clock is frozen for the ingest loop. Relying on real wall-clock
    time made this flaky: under load (the full suite takes ~90s) five ingests can
    take longer than the debounce window, so it expires mid-burst and extra
    rebuilds legitimately fire -- a property of the machine, not the code.
    """
    rebuild_calls, tmp_path = ingest_harness

    frozen = {"t": 10_000.0}
    monkeypatch.setattr(server, "_conflict_rebuild_clock", lambda: frozen["t"])

    for i in range(5):
        archive = tmp_path / f"Mod{i}-{1000 + i}-1-0.zip"
        archive.write_bytes(b"PK\x03\x04fake")
        result = server._ingest_resolved_download(
            archive, name=f"Mod {i}", mod_id=1000 + i, version="1.0"
        )
        assert result["ok"] is True

    # Release the window so the coalesced worker fires.
    frozen["t"] += server.CONFLICT_REBUILD_DEBOUNCE_SECONDS + 1
    assert server._wait_for_conflict_rebuild(timeout=20)

    assert len(rebuild_calls) == 1, (
        f"5 ingests inside one debounce window must coalesce into 1 rebuild, "
        f"got {len(rebuild_calls)}: {rebuild_calls}"
    )


def test_ingest_response_reports_pending_rebuild(ingest_harness):
    rebuild_calls, tmp_path = ingest_harness
    archive = tmp_path / "Single-7-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="Single", mod_id=7, version="1.0"
    )

    assert "conflicts_rebuild_pending" in result, result
    assert result["conflicts_rebuild_pending"] is True
    assert server._wait_for_conflict_rebuild(timeout=20)


def test_ingest_requests_all_snapshot_not_both(ingest_harness):
    """active_only must be False: a new mod is not active, so the _active
    snapshot cannot have changed. None would double the work."""
    rebuild_calls, tmp_path = ingest_harness
    archive = tmp_path / "Scope-8-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    server._ingest_resolved_download(archive, name="Scope", mod_id=8, version="1.0")
    assert server._wait_for_conflict_rebuild(timeout=20)

    assert rebuild_calls == [False], (
        f"ingest should request active_only=False, got {rebuild_calls}"
    )


def test_newly_ingested_mod_is_not_active(ingest_harness, schema_db):
    """The premise behind active_only=False."""
    rebuild_calls, tmp_path = ingest_harness
    archive = tmp_path / "Inactive-9-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="Inactive", mod_id=9, version="1.0"
    )
    row = schema_db.execute(
        "SELECT active_paks FROM local_downloads WHERE id = ?",
        (result["download_id"],),
    ).fetchone()
    assert row[0] in ("[]", None), f"a freshly ingested mod should be inactive: {row}"
    assert server._wait_for_conflict_rebuild(timeout=20)
