"""L5: version equivalence must not swallow real updates.

`versions_equivalent` truncated both versions to min(local_parts, remote_parts)
before comparing. So local "2" vs Nexus "2.5" compared only the major
component, reported "match", set needs_update=False, and the update never
surfaced in the UI. Prefix-equivalence is now directional: it applies only when
the LOCAL value carries extra trailing segments (filename artifacts such as a
file sub-id or upload timestamp), never when the REMOTE is more precise.
"""
from __future__ import annotations

import pytest

from core.db.db import fetch_pak_version_status, make_version_key, versions_equivalent

# The mandated table from the spec.
CASES = [
    ("2", "2.5", False),              # remote more precise -> NOT equivalent (was broken)
    ("2", "2.177.1", False),          # remote more precise -> NOT equivalent (was broken)
    ("2.0.1743611945", "2.0", True),  # local has extra artifact segment -> equivalent
    ("1.2", "1.3", False),            # genuine version bump
    ("1.0.0", "1.0.0", True),         # exact match
]


@pytest.mark.parametrize("local,remote,expected", CASES)
def test_mandated_cases(local, remote, expected):
    assert versions_equivalent(local, remote) is expected, (
        f"versions_equivalent({local!r}, {remote!r}) should be {expected}"
    )


# ---------------------------------------------------------------------------
# Direction is the whole point: assert asymmetry explicitly.
# ---------------------------------------------------------------------------
def test_prefix_rule_is_directional():
    # local carries the extra segments -> equivalent
    assert versions_equivalent("2.177.1", "2") is True
    # remote carries the extra segments -> NOT equivalent
    assert versions_equivalent("2", "2.177.1") is False


def test_timestamp_artifact_still_matches_both_lengths():
    """The documented real-world case must keep working."""
    assert versions_equivalent("2.0.1743611945", "2.0") is True
    assert versions_equivalent("1.2.123", "1.2") is True


@pytest.mark.parametrize(
    "local,remote,expected",
    [
        # equal precision -> full comparison
        ("1.0", "1.0", True),
        ("1.0", "1.1", False),
        ("3.2.1", "3.2.1", True),
        ("3.2.1", "3.2.2", False),
        # "v" prefix is stripped
        ("v1.0", "1.0", True),
        ("1.0", "V1.0", True),
        # whitespace tolerated
        (" 1.0 ", "1.0", True),
        # missing / empty inputs are never equivalent
        (None, "1.0", False),
        ("1.0", None, False),
        ("", "1.0", False),
        ("1.0", "", False),
        # non-numeric junk
        ("abc", "1.0", False),
        ("abc", "def", False),
        # the critical missed-update shapes
        ("1", "1.5", False),
        ("1", "1.0.1", False),
        ("2.0", "2.0.1", False),
    ],
)
def test_additional_cases(local, remote, expected):
    assert versions_equivalent(local, remote) is expected, (
        f"versions_equivalent({local!r}, {remote!r})"
    )


def test_make_version_key_is_string_comparable():
    """fetch_pak_version_status relies on lexicographic ordering of the key."""
    assert make_version_key("2.0")[0] < make_version_key("2.5")[0]
    assert make_version_key("1.9.9")[0] < make_version_key("2.0")[0]
    assert make_version_key("10.0")[0] > make_version_key("9.0")[0]


# ---------------------------------------------------------------------------
# fetch_pak_version_status: the only_needs_update trap
# ---------------------------------------------------------------------------
def test_only_needs_update_param_is_gone():
    """It filtered on the view's raw flag before post-processing could flip it."""
    import inspect

    sig = inspect.signature(fetch_pak_version_status)
    assert "only_needs_update" not in sig.parameters, (
        "only_needs_update must not be reintroduced: it filters pre-post-processing"
    )


def _seed_version_rows(conn, local_version: str, remote_version: str) -> None:
    """Populate the tables behind v_mod_pak_version_status.

    The view joins mod_files -> mod_paks on a normalised name key derived from
    mod_paks.source_zip or local_downloads.name, so those must line up.
    """
    conn.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(1, 'marvelrivals', 'Test Mod')"
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES('C:/dl/Test.zip', 1, 'Test Mod', 1, ?, '["a.pak"]', '[]')
        """,
        (local_version,),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO mod_paks(pak_name, mod_id, source_zip, local_download_id)
        VALUES('a.pak', 1, 'Test Mod', 1)
        """
    )
    key = make_version_key(remote_version)[0]
    conn.execute(
        """
        INSERT OR REPLACE INTO mod_files(mod_id, file_id, name, version, version_key, uploaded_at)
        VALUES(1, 99, 'Test Mod', ?, ?, '2024-01-01T00:00:00Z')
        """,
        (remote_version, key),
    )
    conn.commit()


def test_missed_update_now_surfaces(schema_db):
    """End-to-end: local "2" with Nexus "2.5" must report needs_update=True.

    This is the user-visible symptom of the bug -- the update button never
    appeared for these mods.
    """
    _seed_version_rows(schema_db, "2", "2.5")
    rows = fetch_pak_version_status(schema_db, mod_id=1)
    assert rows, "expected at least one pak row"
    row = rows[0]
    assert row["version_status"] != "match", row
    assert row["needs_update"] is True, row


def test_equivalent_versions_do_not_report_update(schema_db):
    """The false-positive guard must still hold: artifact segments are not updates."""
    _seed_version_rows(schema_db, "2.0.1743611945", "2.0")
    rows = fetch_pak_version_status(schema_db, mod_id=1)
    assert rows
    assert rows[0]["version_status"] == "match", rows[0]
    assert rows[0]["needs_update"] is False, rows[0]


def test_remote_downgrade_does_not_report_update(schema_db):
    """Preserved behaviour: a lower remote version is not an update."""
    _seed_version_rows(schema_db, "3.0", "2.0")
    rows = fetch_pak_version_status(schema_db, mod_id=1)
    assert rows
    assert rows[0]["needs_update"] is False, rows[0]


def test_unknown_versions_do_not_report_update(schema_db):
    _seed_version_rows(schema_db, "", "2.0")
    rows = fetch_pak_version_status(schema_db, mod_id=1)
    assert rows
    assert rows[0]["needs_update"] is False, rows[0]


def test_endpoint_filter_is_consistent_with_returned_flag(schema_db, monkeypatch):
    """?only_needs_update=true must never return a row with needs_update False.

    Previously the SQL filtered on the view's flag, so post-processing could
    hand back rows that contradicted the filter.
    """
    import core.api.server as server

    _seed_version_rows(schema_db, "2.0.1743611945", "2.0")  # equivalent -> no update

    class _NoCloseConn:
        """The endpoint closes its connection in a finally block; keep the
        shared fixture connection alive across both calls."""

        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoCloseConn(schema_db))

    filtered = server.get_pak_version_status_endpoint(
        mod_id=1, download_ids=None, only_needs_update=True
    )
    assert all(r["needs_update"] for r in filtered), filtered
    assert filtered == [], "equivalent versions should be filtered out entirely"

    unfiltered = server.get_pak_version_status_endpoint(
        mod_id=1, download_ids=None, only_needs_update=False
    )
    assert len(unfiltered) == 1


def test_check_mod_update_superseded_download(schema_db, monkeypatch):
    """When a mod has an old download (1.0) and a new download (2.0),
    check_mod_update must report needs_update=False."""
    import core.api.server as server

    # Mod 1 with old download ID 1 (v1.0) and new download ID 2 (v2.0)
    _seed_version_rows(schema_db, "1.0", "2.0")
    schema_db.execute(
        """
        INSERT OR REPLACE INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES('C:/dl/Test_v2.zip', 2, 'Test Mod', 1, '2.0', '["a.pak"]', '["a.pak"]')
        """
    )
    schema_db.commit()

    class _NoCloseConn:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoCloseConn(schema_db))
    monkeypatch.setattr(server, "_sync_mod_metadata", lambda conn, mod_id, info: {})

    res = server.check_mod_update(mod_id=1)
    assert res["ok"] is True
    assert res["needs_update"] is False, f"Expected needs_update=False, got: {res}"
    assert res["pending"] == []


def test_list_downloads_and_get_local_download(schema_db, monkeypatch):
    """Ensure list_downloads and get_local_download SQL queries run cleanly without schema errors."""
    import core.api.server as server

    _seed_version_rows(schema_db, "1.0", "2.0")

    class _NoCloseConn:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoCloseConn(schema_db))
    monkeypatch.setattr(server, "_get_actually_active_filenames", lambda logger: set())

    dls = server.list_downloads()
    assert len(dls) >= 1
    assert dls[0]["mod_id"] == 1
    assert dls[0]["latest_version"] == "2.0"

    single = server.get_local_download(1)
    assert single["id"] == 1
    assert single["latest_version"] == "2.0"


