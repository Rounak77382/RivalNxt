"""H3: collection bulk operations and import failure tracking.

Enabling a 40-mod collection meant 40 separate
PATCH /api/collections/{cid}/mod-files/{fid}/state calls from the frontend, each
triggering its own conflict rebuild. Collection imports also had no failure
tracking: a transient Nexus outage returned a 502 with no record, so retries were
uncounted and unbacked-off.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

import core.api.server as server


@pytest.fixture
def wired(monkeypatch, schema_db):
    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    monkeypatch.setattr(
        "core.api.dependencies.get_db", lambda: _NoClose(schema_db), raising=True
    )
    schema_db.execute("DELETE FROM handoff_failures")
    schema_db.commit()
    return schema_db


def _seed_collection(conn, *, n_mods: int = 10, installed: int = 10) -> int:
    conn.execute(
        """
        INSERT INTO collections(slug, revision_num, name, author, total_mods)
        VALUES('cool-collection', 1, 'Cool Collection', 'someone', ?)
        """,
        (n_mods,),
    )
    cid = conn.execute("SELECT id FROM collections WHERE slug='cool-collection'").fetchone()[0]

    for i in range(n_mods):
        mod_id = 1000 + i
        file_id = 5000 + i
        conn.execute(
            "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(?, 'marvelrivals', ?)",
            (mod_id, f"Mod {i}"),
        )
        conn.execute(
            """
            INSERT INTO collection_mod_files
                (collection_id, file_id, mod_id, version, file_name, mod_name, download_state)
            VALUES(?, ?, ?, '1.0', ?, ?, 'downloaded')
            """,
            (cid, file_id, mod_id, f"Mod{i}.zip", f"Mod {i}"),
        )
        if i < installed:
            conn.execute(
                """
                INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
                VALUES(?, ?, ?, ?, '1.0', ?, '[]')
                """,
                (
                    f"C:/dl/Mod{i}.zip",
                    i + 1,
                    f"Mod {i}",
                    mod_id,
                    json.dumps([f"mod{i}.pak"]),
                ),
            )
    conn.commit()
    return cid


# ---------------------------------------------------------------------------
# Bulk activate: exactly ONE conflict rebuild
# ---------------------------------------------------------------------------
def test_activate_rebuilds_conflicts_exactly_once(wired, monkeypatch):
    """The headline assertion: 10 mods, 1 rebuild."""
    cid = _seed_collection(wired, n_mods=10, installed=10)

    rebuild_calls: list[object] = []
    monkeypatch.setattr(
        server,
        "_safe_rebuild_conflicts",
        lambda conn, *, active_only, purpose, raise_on_error=False: rebuild_calls.append(
            purpose
        ),
    )
    set_calls: list[tuple] = []
    monkeypatch.setattr(
        server,
        "set_active_paks",
        lambda download_id, payload: set_calls.append((download_id, payload)),
    )

    result = server.activate_collection(cid)

    assert result["ok"] is True
    assert len(set_calls) == 10, f"expected 10 per-mod applies, got {len(set_calls)}"
    assert len(rebuild_calls) == 1, (
        f"expected exactly 1 conflict rebuild for the batch, got {len(rebuild_calls)}"
    )
    assert rebuild_calls[0] == "collection_activate"


def test_activate_defers_per_mod_rebuilds(wired, monkeypatch):
    """Each set_active_paks call must be told NOT to rebuild."""
    cid = _seed_collection(wired, n_mods=5, installed=5)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    payloads: list[dict] = []
    monkeypatch.setattr(
        server,
        "set_active_paks",
        lambda download_id, payload: payloads.append(payload),
    )

    server.activate_collection(cid)

    assert payloads, "no mods were applied"
    for p in payloads:
        assert p.get("rebuild_conflicts") is False, p


def test_activate_passes_the_mods_own_paks(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=3, installed=3)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    calls: list[tuple] = []
    monkeypatch.setattr(
        server, "set_active_paks", lambda d, p: calls.append((d, p["active_paks"]))
    )

    server.activate_collection(cid)

    all_paks = {pak for _, paks in calls for pak in paks}
    assert all_paks == {"mod0.pak", "mod1.pak", "mod2.pak"}, all_paks


def test_deactivate_clears_active_paks(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=4, installed=4)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    calls: list[tuple] = []
    monkeypatch.setattr(
        server, "set_active_paks", lambda d, p: calls.append((d, p["active_paks"]))
    )

    result = server.deactivate_collection(cid)

    assert result["deactivated"] == 4
    for _, paks in calls:
        assert paks == [], paks


def test_deactivate_rebuilds_once(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=8, installed=8)
    rebuilds: list[str] = []
    monkeypatch.setattr(
        server,
        "_safe_rebuild_conflicts",
        lambda conn, *, active_only, purpose, raise_on_error=False: rebuilds.append(purpose),
    )
    monkeypatch.setattr(server, "set_active_paks", lambda d, p: None)

    server.deactivate_collection(cid)
    assert len(rebuilds) == 1
    assert rebuilds[0] == "collection_deactivate"


def test_uninstalled_members_are_skipped_not_failed(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=10, installed=4)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    monkeypatch.setattr(server, "set_active_paks", lambda d, p: None)

    result = server.activate_collection(cid)

    assert result["activated"] == 4
    assert len(result["skipped"]) == 6
    assert all(s["reason"] == "not_installed" for s in result["skipped"])
    assert result["total_members"] == 10


def test_one_failing_mod_does_not_abort_the_batch(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=5, installed=5)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)

    def flaky(download_id, payload):
        if download_id == 3:
            raise RuntimeError("disk full")

    monkeypatch.setattr(server, "set_active_paks", flaky)

    result = server.activate_collection(cid)

    assert result["activated"] == 4
    assert len(result["skipped"]) == 1
    assert "disk full" in result["skipped"][0]["reason"]


def test_unknown_collection_returns_404(wired):
    with pytest.raises(HTTPException) as e:
        server.activate_collection(999999)
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# Bulk check-updates: one query, not N round trips
# ---------------------------------------------------------------------------
def test_check_updates_uses_one_query(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=10, installed=10)
    calls: list[dict] = []

    def spy(conn, *, mod_id=None, download_ids=None):
        calls.append({"mod_id": mod_id, "download_ids": download_ids})
        return []

    monkeypatch.setattr(server, "fetch_pak_version_status", spy)

    server.check_collection_updates(cid)

    assert len(calls) == 1, f"expected 1 batched query, got {len(calls)}"
    assert calls[0]["download_ids"] is not None
    assert len(calls[0]["download_ids"]) == 10


def test_check_updates_reports_pending(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=3, installed=3)
    monkeypatch.setattr(
        server,
        "fetch_pak_version_status",
        lambda conn, *, mod_id=None, download_ids=None: [
            {
                "pak_name": "mod0.pak",
                "mod_id": 1000,
                "local_download_id": 1,
                "local_version": "1.0",
                "reference_version": "2.0",
                "reference_file_id": 9,
                "version_status": "mismatch",
                "needs_update": True,
            },
            {"pak_name": "mod1.pak", "needs_update": False},
        ],
    )

    result = server.check_collection_updates(cid)
    assert result["needs_update"] is True
    assert len(result["pending"]) == 1
    assert result["pending"][0]["pak_name"] == "mod0.pak"


def test_check_updates_with_nothing_installed(wired, monkeypatch):
    cid = _seed_collection(wired, n_mods=5, installed=0)
    result = server.check_collection_updates(cid)
    assert result["needs_update"] is False
    assert result["pending"] == []
    assert result["checked_download_ids"] == []


def test_check_updates_unknown_collection_404(wired):
    with pytest.raises(HTTPException) as e:
        server.check_collection_updates(424242)
    assert e.value.status_code == 404


# ---------------------------------------------------------------------------
# Import failure tracking
# ---------------------------------------------------------------------------
def test_nexus_failure_records_a_handoff_failure_row(wired, monkeypatch):
    """A transient Nexus outage must leave a record, not vanish."""

    def boom(slug, revision=None):
        raise HTTPException(status_code=502, detail="Nexus Collections API request failed: 503")

    monkeypatch.setattr(server, "_fetch_collection_from_nexus", boom)

    with pytest.raises(HTTPException) as e:
        server.submit_nxm_handoff(
            {"nxm": "nxm://marvelrivals/collections/cool-slug/revisions/3"}
        )
    # Not a bare 500: the upstream failure is reported as a gateway error.
    assert e.value.status_code == 502, e.value.status_code

    rows = wired.execute(
        "SELECT file_id, retry_count, error_message FROM handoff_failures"
    ).fetchall()
    assert len(rows) == 1, rows
    assert rows[0][0] == "collection:cool-slug"
    assert rows[0][1] == 1
    assert "503" in rows[0][2]


def test_repeated_failures_increment_and_then_back_off(wired, monkeypatch):
    def boom(slug, revision=None):
        raise HTTPException(status_code=502, detail="upstream down")

    monkeypatch.setattr(server, "_fetch_collection_from_nexus", boom)
    url = {"nxm": "nxm://marvelrivals/collections/flaky/revisions/1"}

    with pytest.raises(HTTPException) as first:
        server.submit_nxm_handoff(url)
    assert first.value.status_code == 502

    # Second attempt lands inside the backoff window -> 429, not another 502.
    with pytest.raises(HTTPException) as second:
        server.submit_nxm_handoff(url)
    assert second.value.status_code == 429, second.value.status_code
    assert "backoff" in str(second.value.detail).lower()


def test_retry_ceiling_is_enforced(wired, monkeypatch):
    from core.api.services.handoffs import MAX_HANDOFF_RETRIES

    wired.execute(
        """
        INSERT INTO handoff_failures(file_id, mod_id, error_message, retry_count,
                                     last_attempt_at, handoff_id)
        VALUES('collection:doomed', NULL, 'always fails', ?, '2000-01-01 00:00:00',
               'collection:doomed')
        """,
        (MAX_HANDOFF_RETRIES,),
    )
    wired.commit()

    called: list[str] = []
    monkeypatch.setattr(
        server,
        "_fetch_collection_from_nexus",
        lambda slug, revision=None: called.append(slug),
    )

    with pytest.raises(HTTPException) as e:
        server.submit_nxm_handoff(
            {"nxm": "nxm://marvelrivals/collections/doomed/revisions/1"}
        )
    assert e.value.status_code == 429
    assert called == [], "Nexus must not be contacted once the ceiling is hit"


def test_successful_import_clears_prior_failures(wired, monkeypatch):
    wired.execute(
        """
        INSERT INTO handoff_failures(file_id, mod_id, error_message, retry_count,
                                     last_attempt_at, handoff_id)
        VALUES('collection:recovers', NULL, 'was down', 1, '2000-01-01 00:00:00',
               'collection:recovers')
        """
    )
    wired.commit()

    monkeypatch.setattr(
        server,
        "_fetch_collection_from_nexus",
        lambda slug, revision=None: {"revisionNumber": 1, "collection": {"name": "R"}},
    )
    monkeypatch.setattr(server, "_upsert_collection", lambda conn, rev, slug: 42)

    result = server.submit_nxm_handoff(
        {"nxm": "nxm://marvelrivals/collections/recovers/revisions/1"}
    )
    assert result["ok"] is True
    assert result["collection_id"] == 42

    remaining = wired.execute(
        "SELECT COUNT(*) FROM handoff_failures WHERE file_id = 'collection:recovers'"
    ).fetchone()[0]
    assert remaining == 0, "a successful import must clear the failure record"
