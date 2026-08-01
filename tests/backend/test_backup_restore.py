"""H4: real backup/restore of user state.

Backup was entirely frontend-side: a JSON projection of mod metadata, with the
*index* in localStorage (src/lib/backupUtils.ts). No backend endpoint existed.
So mods.db itself was never backed up, settings.json was never backed up, the
WAL/SHM sidecars were unhandled, and clearing webview storage orphaned every
archive on disk.
"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from core.backup import BackupError, create_backup, list_backups, restore_backup
from core.backup import service as backup_service


@pytest.fixture
def env(monkeypatch, tmp_path):
    """An isolated data dir with a real, populated database."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # create_backup() creates this on demand, but several tests drop a
    # hand-crafted archive in first.
    (data_dir / "backups").mkdir()
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    game_root = tmp_path / "game"
    game_root.mkdir()

    import core.config.settings as settings_mod
    from core.db.db import init_schema, run_migrations

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS,
            data_dir=data_dir,
            marvel_rivals_root=game_root,
            marvel_rivals_local_downloads_root=downloads,
        ),
    )

    db_path = data_dir / "mods.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL;")
    init_schema(conn)
    run_migrations(conn)
    conn.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(1, 'marvelrivals', 'Alpha')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(2, 'marvelrivals', 'Beta')"
    )
    conn.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, 1, 'Alpha', 1, '1.0', '["a.pak"]', '["a.pak"]')
        """,
        (str(downloads / "Alpha.zip"),),
    )
    conn.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, 2, 'Beta', 2, '2.0', '["b.pak"]', '[]')
        """,
        (str(downloads / "Beta.zip"),),
    )
    conn.commit()
    conn.close()

    (data_dir / "settings.json").write_text(
        json.dumps({"data_dir": str(data_dir), "nexus_api_key": "secret"}),
        encoding="utf-8",
    )

    return {
        "data_dir": data_dir,
        "downloads": downloads,
        "game_root": game_root,
        "db_path": db_path,
    }


def _snapshot_state(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        return {
            "local_downloads": conn.execute(
                "SELECT path, id, name, mod_id, version, contents, active_paks "
                "FROM local_downloads ORDER BY id"
            ).fetchall(),
            "mods": conn.execute(
                "SELECT mod_id, name FROM mods ORDER BY mod_id"
            ).fetchall(),
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Sub-test 1: round trip
# ---------------------------------------------------------------------------
def test_roundtrip_preserves_state(env):
    before = _snapshot_state(env["db_path"])
    assert before["local_downloads"], "fixture produced no rows"

    result = create_backup(name="rt", timestamp="2026-01-01T00:00:00+00:00")
    assert result["ok"] is True
    archive = Path(result["path"])
    assert archive.exists()

    # Wipe the live database entirely.
    conn = sqlite3.connect(str(env["db_path"]))
    conn.execute("DELETE FROM local_downloads")
    conn.execute("DELETE FROM mods")
    conn.commit()
    conn.close()
    assert _snapshot_state(env["db_path"])["local_downloads"] == []

    restore_backup(path=str(archive), timestamp="2026-01-02T00:00:00+00:00")

    after = _snapshot_state(env["db_path"])
    assert after["local_downloads"] == before["local_downloads"]
    assert after["mods"] == before["mods"]


def test_roundtrip_preserves_active_paks(env):
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    conn = sqlite3.connect(str(env["db_path"]))
    conn.execute("UPDATE local_downloads SET active_paks = '[]'")
    conn.commit()
    conn.close()

    restore_backup(path=result["path"], timestamp="2026-01-02T00:00:00+00:00")

    conn = sqlite3.connect(str(env["db_path"]))
    try:
        rows = dict(
            conn.execute("SELECT id, active_paks FROM local_downloads").fetchall()
        )
    finally:
        conn.close()
    assert rows[1] == '["a.pak"]', rows
    assert rows[2] == "[]", rows


def test_backup_snapshot_is_wal_safe(env):
    """Writes committed to the WAL but not yet checkpointed must be captured.

    A plain file copy of mods.db would miss them; Connection.backup does not.
    """
    conn = sqlite3.connect(str(env["db_path"]))
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(99, 'marvelrivals', 'WalOnly')"
    )
    conn.commit()
    # Deliberately do NOT checkpoint, and keep the connection open so the WAL
    # stays un-merged.
    try:
        result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
        with zipfile.ZipFile(result["path"]) as zf:
            extracted = zf.read("mods.db")
    finally:
        conn.close()

    staged = env["data_dir"] / "check.db"
    staged.write_bytes(extracted)
    probe = sqlite3.connect(str(staged))
    try:
        names = [r[0] for r in probe.execute("SELECT name FROM mods ORDER BY mod_id")]
    finally:
        probe.close()
    assert "WalOnly" in names, (
        "the backup missed a committed-but-uncheckpointed write; the snapshot is "
        "not WAL-safe"
    )


def test_archive_contains_manifest_db_and_settings(env):
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    with zipfile.ZipFile(result["path"]) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("manifest.json"))
    assert names == {"manifest.json", "mods.db", "settings.json"}, names
    assert manifest["manifest_version"] == backup_service.BACKUP_MANIFEST_VERSION
    assert manifest["data_dir"] == str(env["data_dir"])
    assert manifest["total_mods"] == 2
    assert manifest["active_mods"] == 1


def test_settings_are_restored(env):
    create_backup(timestamp="2026-01-01T00:00:00+00:00")
    settings_file = env["data_dir"] / "settings.json"
    settings_file.write_text(json.dumps({"clobbered": True}), encoding="utf-8")

    backups = list_backups()
    res = restore_backup(path=backups[0]["path"], timestamp="2026-01-02T00:00:00+00:00")

    assert res["restored_settings"] is True
    restored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert restored.get("nexus_api_key") == "secret", restored


def test_stale_wal_sidecars_are_removed(env):
    """The restored snapshot is self-contained; leftover sidecars would point at
    pages from the replaced database."""
    create_backup(timestamp="2026-01-01T00:00:00+00:00")
    wal = Path(str(env["db_path"]) + "-wal")
    shm = Path(str(env["db_path"]) + "-shm")
    wal.write_bytes(b"stale-wal")
    shm.write_bytes(b"stale-shm")

    restore_backup(
        path=list_backups()[0]["path"], timestamp="2026-01-02T00:00:00+00:00"
    )

    assert not wal.exists(), "stale -wal survived the restore"
    assert not shm.exists(), "stale -shm survived the restore"


# ---------------------------------------------------------------------------
# Sub-test 2: changed data_dir -> paths remapped
# ---------------------------------------------------------------------------
def test_restore_remaps_paths_for_changed_data_dir(env, monkeypatch, tmp_path):
    """Restoring onto a relocated data dir must rewrite absolute paths, or every
    local_downloads row silently reads as "file missing"."""
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    archive = Path(result["path"])

    # Move to a new data dir and copy the archive across.
    new_data_dir = tmp_path / "moved_data"
    new_data_dir.mkdir()
    new_backups = new_data_dir / "backups"
    new_backups.mkdir()
    moved_archive = new_backups / archive.name
    moved_archive.write_bytes(archive.read_bytes())

    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(settings_mod.SETTINGS, data_dir=new_data_dir),
    )

    res = restore_backup(
        path=str(moved_archive), timestamp="2026-01-02T00:00:00+00:00"
    )
    assert res["ok"] is True

    conn = sqlite3.connect(str(new_data_dir / "mods.db"))
    try:
        paths = [r[0] for r in conn.execute("SELECT path FROM local_downloads")]
    finally:
        conn.close()

    old_prefix = str(env["data_dir"])
    assert all(old_prefix not in p for p in paths), (
        f"paths still reference the old data dir: {paths}"
    )


def test_remap_rewrites_downloads_root(env, monkeypatch, tmp_path):
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")

    new_downloads = tmp_path / "new_downloads"
    new_downloads.mkdir()
    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS, marvel_rivals_local_downloads_root=new_downloads
        ),
    )

    res = restore_backup(path=result["path"], timestamp="2026-01-02T00:00:00+00:00")
    assert res["remapped_paths"] >= 1, res

    conn = sqlite3.connect(str(env["db_path"]))
    try:
        paths = [r[0] for r in conn.execute("SELECT path FROM local_downloads")]
    finally:
        conn.close()
    assert all(str(new_downloads) in p for p in paths), paths


def test_remap_can_be_disabled(env, monkeypatch, tmp_path):
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    new_downloads = tmp_path / "other_downloads"
    new_downloads.mkdir()
    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS, marvel_rivals_local_downloads_root=new_downloads
        ),
    )
    res = restore_backup(
        path=result["path"], remap_paths=False, timestamp="2026-01-02T00:00:00+00:00"
    )
    assert res["remapped_paths"] == 0


def test_restore_to_same_location_does_not_remap(env):
    result = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    res = restore_backup(path=result["path"], timestamp="2026-01-02T00:00:00+00:00")
    assert res["remapped_paths"] == 0


# ---------------------------------------------------------------------------
# Sub-test 3: corrupted archive must NOT mutate the live DB
# ---------------------------------------------------------------------------
def test_truncated_archive_is_rejected_without_touching_live_db(env):
    good = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    before = _snapshot_state(env["db_path"])
    before_bytes = env["db_path"].read_bytes()

    truncated = env["data_dir"] / "backups" / "truncated.zip"
    data = Path(good["path"]).read_bytes()
    truncated.write_bytes(data[: len(data) // 2])

    with pytest.raises(BackupError):
        restore_backup(path=str(truncated), timestamp="2026-01-02T00:00:00+00:00")

    assert _snapshot_state(env["db_path"]) == before
    assert env["db_path"].read_bytes() == before_bytes, "live DB was modified"


def test_non_zip_file_is_rejected(env):
    before = _snapshot_state(env["db_path"])
    bogus = env["data_dir"] / "backups" / "notazip.zip"
    bogus.write_bytes(b"this is definitely not a zip archive")

    with pytest.raises(BackupError, match="not a valid zip|corrupt"):
        restore_backup(path=str(bogus), timestamp="2026-01-02T00:00:00+00:00")
    assert _snapshot_state(env["db_path"]) == before


def test_archive_without_manifest_is_rejected(env):
    before = _snapshot_state(env["db_path"])
    bad = env["data_dir"] / "backups" / "nomanifest.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("mods.db", b"whatever")

    with pytest.raises(BackupError, match="manifest"):
        restore_backup(path=str(bad), timestamp="2026-01-02T00:00:00+00:00")
    assert _snapshot_state(env["db_path"]) == before


def test_archive_without_database_is_rejected(env):
    before = _snapshot_state(env["db_path"])
    bad = env["data_dir"] / "backups" / "nodb.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"manifest_version": 2}))

    with pytest.raises(BackupError, match="mods.db"):
        restore_backup(path=str(bad), timestamp="2026-01-02T00:00:00+00:00")
    assert _snapshot_state(env["db_path"]) == before


def test_archive_with_garbage_database_is_rejected(env):
    """A zip that is structurally fine but whose mods.db is not a database."""
    before = _snapshot_state(env["db_path"])
    bad = env["data_dir"] / "backups" / "garbagedb.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"manifest_version": 2}))
        zf.writestr("mods.db", b"\x00\x01\x02 not a sqlite file at all")

    with pytest.raises(BackupError):
        restore_backup(path=str(bad), timestamp="2026-01-02T00:00:00+00:00")
    assert _snapshot_state(env["db_path"]) == before


def test_future_manifest_version_is_rejected(env):
    before = _snapshot_state(env["db_path"])
    good = create_backup(timestamp="2026-01-01T00:00:00+00:00")
    future = env["data_dir"] / "backups" / "future.zip"
    with zipfile.ZipFile(good["path"]) as src, zipfile.ZipFile(future, "w") as dst:
        for item in src.namelist():
            if item == "manifest.json":
                dst.writestr(item, json.dumps({"manifest_version": 999}))
            else:
                dst.writestr(item, src.read(item))

    with pytest.raises(BackupError, match="newer version"):
        restore_backup(path=str(future), timestamp="2026-01-02T00:00:00+00:00")
    assert _snapshot_state(env["db_path"]) == before


def test_missing_archive_is_rejected(env):
    with pytest.raises(BackupError, match="not found"):
        restore_backup(path=str(env["data_dir"] / "nope.zip"))


def test_restore_writes_a_safety_snapshot(env):
    """A pre-restore snapshot means a bad restore is itself recoverable."""
    good = create_backup(name="src", timestamp="2026-01-01T00:00:00+00:00")
    res = restore_backup(path=good["path"], timestamp="2026-01-02T00:00:00+00:00")
    assert res["safety_snapshot"], res
    safety = Path(res["safety_snapshot"])
    assert safety.exists()
    with zipfile.ZipFile(safety) as zf:
        assert "mods.db" in zf.namelist()


# ---------------------------------------------------------------------------
# Listing: filesystem is the source of truth
# ---------------------------------------------------------------------------
def test_list_reflects_the_filesystem(env):
    assert list_backups() == []
    create_backup(name="one", timestamp="2026-01-01T00:00:00+00:00")
    create_backup(name="two", timestamp="2026-02-01T00:00:00+00:00")
    listed = list_backups()
    assert len(listed) == 2
    # Newest first.
    assert listed[0]["created_at"] > listed[1]["created_at"]


def test_list_survives_a_cleared_frontend_store(env):
    """The exact failure the old localStorage index had."""
    create_backup(name="persisted", timestamp="2026-01-01T00:00:00+00:00")
    # Nothing to clear on the backend -- that is the point.
    assert len(list_backups()) == 1
    assert list_backups()[0]["name"] == "persisted"


def test_list_ignores_non_zip_files(env):
    create_backup(timestamp="2026-01-01T00:00:00+00:00")
    (env["data_dir"] / "backups" / "notes.txt").write_text("hi", encoding="utf-8")
    assert len(list_backups()) == 1


def test_list_tolerates_an_unreadable_archive(env):
    create_backup(timestamp="2026-01-01T00:00:00+00:00")
    (env["data_dir"] / "backups" / "broken.zip").write_bytes(b"nope")
    listed = list_backups()
    assert len(listed) == 2, "a corrupt archive should still be listed, not crash"


def test_create_reports_size_and_counts(env):
    result = create_backup(name="sized", timestamp="2026-01-01T00:00:00+00:00")
    assert result["size_bytes"] > 0
    assert result["total_mods"] == 2
    assert result["active_mods"] == 1


def test_missing_database_is_reported(env):
    env["db_path"].unlink()
    with pytest.raises(BackupError, match="database not found"):
        create_backup(timestamp="2026-01-01T00:00:00+00:00")


# ---------------------------------------------------------------------------
# API surface
# ---------------------------------------------------------------------------
def test_endpoints_are_registered():
    import core.api.server as server

    paths = {getattr(r, "path", None) for r in server.app.routes}
    assert "/api/backup/create" in paths
    assert "/api/backup/list" in paths
    assert "/api/backup/restore" in paths


def test_restore_endpoint_returns_400_for_a_bad_archive(env, monkeypatch):
    import core.api.server as server
    from fastapi import HTTPException

    bogus = env["data_dir"] / "backups" / "bad.zip"
    bogus.write_bytes(b"nope")

    with pytest.raises(HTTPException) as e:
        server.restore_backup_route(
            server.BackupRestorePayload(path=str(bogus), remap_paths=True)
        )
    assert e.value.status_code == 400


def test_list_endpoint_shape(env):
    import core.api.server as server

    create_backup(name="api", timestamp="2026-01-01T00:00:00+00:00")
    result = server.list_backups_route()
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["backups"][0]["name"] == "api"
