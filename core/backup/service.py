"""Backup and restore of user state.

Backup was previously entirely frontend-side: a JSON file of mod metadata, with
the *index* of backups kept in ``localStorage`` under "rivalnxt:backups"
(src/lib/backupUtils.ts). There was no backend endpoint at all. Consequences:

* clearing webview storage orphaned every backup file on disk,
* ``mods.db`` itself was never backed up -- only a projection of it,
* ``settings.json`` was never backed up,
* nothing handled the ``-wal`` / ``-shm`` sidecars, so naively copying mods.db
  mid-session yields a torn snapshot.

This module makes the filesystem the source of truth and snapshots the real
database using SQLite's online backup API, which is WAL-safe by construction.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("modmanager.backup")

BACKUP_MANIFEST_VERSION = 2
MANIFEST_NAME = "manifest.json"
DB_ENTRY_NAME = "mods.db"
SETTINGS_ENTRY_NAME = "settings.json"
BACKUPS_DIRNAME = "backups"


class BackupError(Exception):
    """Raised when a backup cannot be created or restored."""


@dataclass
class BackupInfo:
    name: str
    path: str
    created_at: Optional[str]
    size_bytes: int
    manifest_version: Optional[int]
    total_mods: Optional[int]
    active_mods: Optional[int]
    data_dir: Optional[str]
    marvel_rivals_root: Optional[str]
    downloads_root: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "created_at": self.created_at,
            "size_bytes": self.size_bytes,
            "manifest_version": self.manifest_version,
            "total_mods": self.total_mods,
            "active_mods": self.active_mods,
            "data_dir": self.data_dir,
            "marvel_rivals_root": self.marvel_rivals_root,
            "downloads_root": self.downloads_root,
        }


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _settings():
    from core.config.settings import SETTINGS

    return SETTINGS


def backups_dir(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir) if data_dir is not None else Path(_settings().data_dir)
    target = root / BACKUPS_DIRNAME
    target.mkdir(parents=True, exist_ok=True)
    return target


def _db_path(data_dir: Optional[Path] = None) -> Path:
    from core.db.db import DB_FILENAME

    root = Path(data_dir) if data_dir is not None else Path(_settings().data_dir)
    return root / DB_FILENAME


def _settings_file(data_dir: Optional[Path] = None) -> Path:
    root = Path(data_dir) if data_dir is not None else Path(_settings().data_dir)
    return root / "settings.json"


def _safe_component(value: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_ .") else "_" for c in str(value))
    return cleaned.strip().strip(".") or "backup"


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------
def _snapshot_database(source_db: Path, destination: Path) -> None:
    """Copy the live database using SQLite's online backup API.

    NOT a file copy: with WAL journaling the newest committed pages may live in
    ``mods.db-wal``, so copying only ``mods.db`` produces a snapshot that is
    missing recent writes (or is internally inconsistent). ``Connection.backup``
    walks the database through SQLite itself and yields a single consistent file
    with no WAL sidecar.
    """
    src = sqlite3.connect(str(source_db))
    try:
        dest = sqlite3.connect(str(destination))
        try:
            src.backup(dest)
        finally:
            dest.close()
    finally:
        src.close()


def _overwrite_live_database(staged_db: Path, live_db: Path) -> None:
    """Write the staged database over the live one using SQLite's backup API.

    The counterpart to ``_snapshot_database``, and for a related reason. A file
    copy onto the live database cannot work while the app is running:
    ``core/db/db.py`` opens connections with ``PRAGMA mmap_size = 268435456``,
    so SQLite keeps mods.db memory-mapped, and on Windows the CREATE_ALWAYS
    that ``shutil.copyfile`` issues fails against a file with a live mapped
    section (ERROR_USER_MAPPED_FILE). CPython has no errno for that code, so it
    surfaces as ``OSError: [Errno 22] Invalid argument``.

    Retiring the connection pool first does not help: ``reset_schema_cache``
    bumps a generation counter, so a worker thread drops its cached handle on
    its *next* ``get_db()`` -- an idle thread never gets there and its mapping
    outlives the restore. ``Connection.backup`` writes through the existing
    handles rather than around them, and is transactional: a failure part-way
    leaves the live database on its previous contents instead of half-written.
    """
    src = sqlite3.connect(str(staged_db))
    try:
        dst = sqlite3.connect(str(live_db))
        try:
            # A maintenance task holding the write lock is transient; wait for
            # it rather than failing the restore.
            dst.execute("PRAGMA busy_timeout = 30000")
            src.backup(dst)
        finally:
            dst.close()
    except sqlite3.Error as exc:
        raise BackupError(f"could not write the restored database: {exc}") from exc
    finally:
        src.close()


def _count_mods(db_file: Path) -> tuple[Optional[int], Optional[int]]:
    """(total, active) local downloads, for display in the backup list."""
    try:
        conn = sqlite3.connect(str(db_file))
        try:
            total = conn.execute("SELECT COUNT(*) FROM local_downloads").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM local_downloads "
                "WHERE active_paks IS NOT NULL AND active_paks NOT IN ('', '[]')"
            ).fetchone()[0]
            return int(total), int(active)
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("Could not count mods for manifest: %s", exc)
        return None, None


def create_backup(
    *,
    name: Optional[str] = None,
    timestamp: Optional[str] = None,
    data_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Snapshot the database + settings into a timestamped zip.

    ``timestamp`` is injectable so callers (and tests) control naming; it is not
    read from the clock here.
    """
    from datetime import datetime, timezone

    settings = _settings()
    root = Path(data_dir) if data_dir is not None else Path(settings.data_dir)
    source_db = _db_path(root)
    if not source_db.exists():
        raise BackupError(f"database not found at {source_db}")

    created_at = timestamp or datetime.now(timezone.utc).isoformat()
    stamp = _safe_component(created_at.replace(":", "-"))
    label = _safe_component(name) if name else "backup"
    archive_name = f"{label}-{stamp}.zip"
    archive_path = backups_dir(root) / archive_name

    tmpdir = Path(tempfile.mkdtemp(prefix="rivalnxt_backup_"))
    try:
        snapshot = tmpdir / DB_ENTRY_NAME
        _snapshot_database(source_db, snapshot)
        total_mods, active_mods = _count_mods(snapshot)

        manifest: Dict[str, Any] = {
            "manifest_version": BACKUP_MANIFEST_VERSION,
            "created_at": created_at,
            "name": name or label,
            "total_mods": total_mods,
            "active_mods": active_mods,
            # Recorded so restore can remap absolute paths when the app has
            # moved. Without these, restoring onto a different machine or a
            # relocated data dir silently leaves dead local_downloads.path rows.
            "data_dir": str(root),
            "marvel_rivals_root": (
                str(settings.marvel_rivals_root) if settings.marvel_rivals_root else None
            ),
            "downloads_root": (
                str(settings.marvel_rivals_local_downloads_root)
                if settings.marvel_rivals_local_downloads_root
                else None
            ),
        }

        settings_file = _settings_file(root)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            zf.write(snapshot, DB_ENTRY_NAME)
            if settings_file.exists():
                zf.write(settings_file, SETTINGS_ENTRY_NAME)
                manifest["includes_settings"] = True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    result = dict(manifest)
    result.update(
        {
            "ok": True,
            "path": str(archive_path),
            "archive_name": archive_name,
            "size_bytes": archive_path.stat().st_size,
        }
    )
    logger.info("[backup] Created %s (%s bytes)", archive_path, result["size_bytes"])
    return result


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------
def _read_manifest(archive: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(archive) as zf:
            with zf.open(MANIFEST_NAME) as fh:
                data = json.loads(fh.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def list_backups(*, data_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Enumerate backup archives on disk.

    This replaces ``localStorage`` as the index. The filesystem is authoritative,
    so backups survive a cleared webview store, a reinstall, or a different
    machine.
    """
    target = backups_dir(data_dir)
    out: List[BackupInfo] = []
    for entry in sorted(target.glob("*.zip")):
        manifest = _read_manifest(entry)
        try:
            size = entry.stat().st_size
        except OSError:
            size = 0
        out.append(
            BackupInfo(
                name=manifest.get("name") or entry.stem,
                path=str(entry),
                created_at=manifest.get("created_at"),
                size_bytes=size,
                manifest_version=manifest.get("manifest_version"),
                total_mods=manifest.get("total_mods"),
                active_mods=manifest.get("active_mods"),
                data_dir=manifest.get("data_dir"),
                marvel_rivals_root=manifest.get("marvel_rivals_root"),
                downloads_root=manifest.get("downloads_root"),
            )
        )
    # Newest first; archives without a timestamp sort last.
    out.sort(key=lambda b: (b.created_at or ""), reverse=True)
    return [b.as_dict() for b in out]


# ---------------------------------------------------------------------------
# Restore
# ---------------------------------------------------------------------------
def _validate_archive(archive: Path) -> Dict[str, Any]:
    if not archive.exists():
        raise BackupError(f"backup not found: {archive}")
    try:
        with zipfile.ZipFile(archive) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise BackupError(f"backup archive is corrupt (bad entry: {bad})")
            names = set(zf.namelist())
            if MANIFEST_NAME not in names:
                raise BackupError("backup archive has no manifest.json")
            if DB_ENTRY_NAME not in names:
                raise BackupError("backup archive contains no mods.db")
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise BackupError(f"backup archive is not a valid zip: {exc}") from exc

    if not isinstance(manifest, dict):
        raise BackupError("backup manifest is not an object")
    version = manifest.get("manifest_version")
    if version is not None and int(version) > BACKUP_MANIFEST_VERSION:
        raise BackupError(
            f"backup was written by a newer version (manifest v{version}, "
            f"this build understands v{BACKUP_MANIFEST_VERSION})"
        )
    return manifest


def _verify_restored_db(db_file: Path) -> None:
    """Confirm the extracted file is a usable SQLite database before it goes live."""
    conn = sqlite3.connect(str(db_file))
    try:
        result = conn.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise BackupError(f"restored database failed integrity check: {result}")
        conn.execute("SELECT COUNT(*) FROM local_downloads").fetchone()
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"restored file is not a valid database: {exc}") from exc
    finally:
        conn.close()


def _remap_paths(db_file: Path, mapping: Dict[str, str]) -> int:
    """Rewrite absolute path prefixes inside the restored database.

    Restoring a backup taken with a different data dir / downloads root would
    otherwise leave every ``local_downloads.path`` pointing at a location that no
    longer exists, and the rows would silently read as "file missing".
    """
    if not mapping:
        return 0
    conn = sqlite3.connect(str(db_file))
    updated = 0
    try:
        for old, new in mapping.items():
            if not old or not new or old == new:
                continue
            for variant_old, variant_new in {
                (old, new),
                (old.replace("\\", "/"), new.replace("\\", "/")),
                (old.replace("/", "\\"), new.replace("/", "\\")),
            }:
                cur = conn.execute(
                    "UPDATE local_downloads SET path = ? || SUBSTR(path, ?) "
                    "WHERE path LIKE ? || '%'",
                    (variant_new, len(variant_old) + 1, variant_old),
                )
                updated += cur.rowcount or 0
        conn.commit()
    finally:
        conn.close()
    if updated:
        logger.info("[backup] Remapped %s local_downloads path(s)", updated)
    return updated


def restore_backup(
    *,
    path: str,
    data_dir: Optional[Path] = None,
    remap_paths: bool = True,
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Restore a backup archive over the live database.

    Order matters: everything that can fail is done against temporary files, and
    a pre-restore safety snapshot is taken, before the live database is touched.
    A corrupt or unreadable archive therefore leaves the running install
    untouched.
    """
    settings = _settings()
    root = Path(data_dir) if data_dir is not None else Path(settings.data_dir)
    archive = Path(path)

    manifest = _validate_archive(archive)

    tmpdir = Path(tempfile.mkdtemp(prefix="rivalnxt_restore_"))
    try:
        with zipfile.ZipFile(archive) as zf:
            zf.extract(DB_ENTRY_NAME, tmpdir)
            has_settings = SETTINGS_ENTRY_NAME in set(zf.namelist())
            if has_settings:
                zf.extract(SETTINGS_ENTRY_NAME, tmpdir)

        staged_db = tmpdir / DB_ENTRY_NAME
        _verify_restored_db(staged_db)

        remapped = 0
        if remap_paths:
            mapping: Dict[str, str] = {}
            old_data_dir = manifest.get("data_dir")
            if old_data_dir and str(old_data_dir) != str(root):
                mapping[str(old_data_dir)] = str(root)
            old_downloads = manifest.get("downloads_root")
            current_downloads = settings.marvel_rivals_local_downloads_root
            if old_downloads and current_downloads and str(old_downloads) != str(current_downloads):
                mapping[str(old_downloads)] = str(current_downloads)
            remapped = _remap_paths(staged_db, mapping)

        # Retire pooled handles so no thread keeps serving reads from the
        # pre-restore state. Housekeeping, not a precondition any more:
        # _overwrite_live_database writes through SQLite precisely so that
        # handles outliving this call cannot block the restore.
        try:
            from core.api.dependencies import reset_schema_cache

            reset_schema_cache()
        except Exception as exc:
            logger.debug("[backup] Could not reset connection pool: %s", exc)

        live_db = _db_path(root)
        safety: Optional[Path] = None
        if live_db.exists():
            from datetime import datetime, timezone

            stamp = _safe_component(
                (timestamp or datetime.now(timezone.utc).isoformat()).replace(":", "-")
            )
            safety = backups_dir(root) / f"pre-restore-{stamp}.zip"
            try:
                safety_snapshot = tmpdir / "pre_restore.db"
                _snapshot_database(live_db, safety_snapshot)
                with zipfile.ZipFile(safety, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(
                        MANIFEST_NAME,
                        json.dumps(
                            {
                                "manifest_version": BACKUP_MANIFEST_VERSION,
                                "created_at": timestamp,
                                "name": "pre-restore safety snapshot",
                                "data_dir": str(root),
                            },
                            indent=2,
                        ),
                    )
                    zf.write(safety_snapshot, DB_ENTRY_NAME)
            except Exception as exc:
                logger.warning("[backup] Could not write safety snapshot: %s", exc)
                safety = None

        live_db.parent.mkdir(parents=True, exist_ok=True)
        _overwrite_live_database(staged_db, live_db)
        # No -wal/-shm cleanup here any more. Unlinking them was right while the
        # database file was being replaced behind SQLite's back: the sidecars
        # described pages that no longer existed. Writing through SQLite puts
        # the restored pages *in* the WAL, so deleting it would throw the
        # restore away. SQLite removes both sidecars itself when the last
        # connection to the database closes.

        restored_settings = False
        if has_settings:
            try:
                shutil.copyfile(tmpdir / SETTINGS_ENTRY_NAME, _settings_file(root))
                restored_settings = True
            except Exception as exc:
                logger.warning("[backup] Could not restore settings.json: %s", exc)

        # Re-apply migrations: the archive may predate the current schema.
        try:
            from core.api.dependencies import reset_schema_cache
            from core.db.db import get_connection, init_schema

            reset_schema_cache()
            conn = get_connection(str(live_db))
            try:
                init_schema(conn)  # calls run_migrations internally
            finally:
                conn.close()
        except Exception as exc:
            logger.warning("[backup] Post-restore migration failed: %s", exc)

        return {
            "ok": True,
            "restored_from": str(archive),
            "manifest_version": manifest.get("manifest_version"),
            "created_at": manifest.get("created_at"),
            "remapped_paths": remapped,
            "restored_settings": restored_settings,
            "safety_snapshot": str(safety) if safety else None,
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
