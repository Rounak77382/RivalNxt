"""M10: duplicate detection must also match on content hash.

_find_duplicate_download matched only on name + version (+ mod_id), so the same
archive re-downloaded under a different filename ingested twice. Nexus appends
"-1", browsers append " (2)", users rename files -- all of which defeat name
matching while the bytes are identical.
"""
from __future__ import annotations

import hashlib

import pytest

import core.api.server as server


def _insert(
    conn,
    *,
    download_id: int,
    path: str,
    name: str,
    version: str = "1.0",
    mod_id: int | None = None,
    file_md5: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, file_md5)
        VALUES(?, ?, ?, ?, ?, '[]', '[]', ?)
        """,
        (path, download_id, name, mod_id, version, file_md5),
    )
    conn.commit()


@pytest.fixture
def existing_archive(tmp_path, schema_db, monkeypatch):
    """One archive on disk, registered in the DB with its MD5."""
    payload = b"the-actual-mod-bytes" * 500
    digest = hashlib.md5(payload).hexdigest()

    stored = tmp_path / "CoolMod-1234-1-0.zip"
    stored.write_bytes(payload)

    _insert(
        schema_db,
        download_id=1,
        path=str(stored),
        name="Cool Mod",
        version="1.0",
        mod_id=1234,
        file_md5=digest,
    )

    monkeypatch.setattr(
        server, "resolve_absolute_download_path", lambda p: tmp_path / str(p).split("\\")[-1].split("/")[-1]
    )
    return digest, payload, stored


# ---------------------------------------------------------------------------
# The headline case
# ---------------------------------------------------------------------------
def test_renamed_archive_is_detected_by_md5(existing_archive, schema_db):
    """Different filename, different parsed name, identical bytes -> duplicate."""
    digest, _payload, _stored = existing_archive
    cur = schema_db.cursor()

    hit = server._find_duplicate_download(
        cur,
        "Cool Mod (2)",   # a name that will NOT match
        "1.0",
        None,             # no mod_id either
        digest,           # but the content hash matches
    )
    assert hit is not None, "MD5 duplicate was not detected"
    assert hit[0] == 1


def test_md5_match_wins_even_with_totally_different_name(existing_archive, schema_db):
    digest, _, _ = existing_archive
    cur = schema_db.cursor()
    hit = server._find_duplicate_download(
        cur, "completely-unrelated-title", "99.99", 999999, digest
    )
    assert hit is not None
    assert hit[0] == 1


def test_md5_comparison_is_case_insensitive(existing_archive, schema_db):
    digest, _, _ = existing_archive
    cur = schema_db.cursor()
    assert server._find_duplicate_download(cur, "x", "1.0", None, digest.upper()) is not None


def test_different_md5_is_not_a_duplicate(existing_archive, schema_db):
    cur = schema_db.cursor()
    other = hashlib.md5(b"different bytes entirely").hexdigest()
    assert server._find_duplicate_download(cur, "Brand New Mod", "1.0", None, other) is None


# ---------------------------------------------------------------------------
# The name/version tier must keep working
# ---------------------------------------------------------------------------
def test_name_tier_still_works_without_md5(existing_archive, schema_db):
    cur = schema_db.cursor()
    hit = server._find_duplicate_download(cur, "Cool Mod", "1.0", 1234, None)
    assert hit is not None
    assert hit[0] == 1


def test_no_md5_and_no_name_match_is_not_a_duplicate(existing_archive, schema_db):
    cur = schema_db.cursor()
    assert server._find_duplicate_download(cur, "Unrelated", "1.0", None, None) is None


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_md5_falls_through_to_name_tier(existing_archive, schema_db, blank):
    cur = schema_db.cursor()
    assert server._find_duplicate_download(cur, "Cool Mod", "1.0", 1234, blank) is not None


# ---------------------------------------------------------------------------
# Physical-existence rule must apply to the MD5 tier too
# ---------------------------------------------------------------------------
def test_md5_match_with_missing_file_permits_reingest(schema_db, tmp_path, monkeypatch):
    """A DB row whose file is gone must not block re-ingestion -- the same rule
    the name tier already enforced."""
    digest = hashlib.md5(b"gone").hexdigest()
    _insert(
        schema_db,
        download_id=5,
        path="C:/nowhere/Missing.zip",
        name="Missing Mod",
        file_md5=digest,
    )
    monkeypatch.setattr(
        server, "resolve_absolute_download_path", lambda p: tmp_path / "absent.zip"
    )

    cur = schema_db.cursor()
    assert server._find_duplicate_download(cur, "Whatever", "1.0", None, digest) is None


def test_rows_with_null_md5_are_ignored_by_the_md5_tier(schema_db, tmp_path, monkeypatch):
    """A NULL file_md5 must not match an empty-ish probe."""
    stored = tmp_path / "NoHash.zip"
    stored.write_bytes(b"x")
    _insert(
        schema_db,
        download_id=7,
        path=str(stored),
        name="No Hash Mod",
        file_md5=None,
    )
    monkeypatch.setattr(server, "resolve_absolute_download_path", lambda p: stored)

    cur = schema_db.cursor()
    probe = hashlib.md5(b"whatever").hexdigest()
    assert server._find_duplicate_download(cur, "Different", "1.0", None, probe) is None


# ---------------------------------------------------------------------------
# End-to-end through the ingest
# ---------------------------------------------------------------------------
@pytest.fixture
def ingest_harness(monkeypatch, schema_db, tmp_path):
    from pathlib import Path

    def fake_extract_archive(archive_path, dest):
        (Path(dest) / "thing.pak").write_bytes(b"\x00")

    monkeypatch.setattr(server, "extract_archive", fake_extract_archive)
    monkeypatch.setattr(
        server,
        "extract_pak_asset_map_from_folder",
        lambda folder, aes_key=None: {"thing.pak": ["/Game/A/x.uasset"]},
    )
    monkeypatch.setattr(
        server, "_sync_mod_metadata", lambda *a, **k: {"synced_mod_id": None}
    )
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    monkeypatch.setattr(server, "_schedule_conflict_rebuild", lambda **k: False)

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    return tmp_path


def test_ingest_persists_the_md5(ingest_harness, schema_db):
    """The hash computed for dedup must be stored, so later ingests can match."""
    tmp_path = ingest_harness
    payload = b"unique-mod-payload" * 100
    archive = tmp_path / "Fresh-42-1-0.zip"
    archive.write_bytes(payload)

    result = server._ingest_resolved_download(
        archive, name="Fresh", mod_id=42, version="1.0"
    )
    assert result["ok"] is True

    row = schema_db.execute(
        "SELECT file_md5 FROM local_downloads WHERE id = ?", (result["download_id"],)
    ).fetchone()
    assert row[0] == hashlib.md5(payload).hexdigest(), row


def test_ingest_rejects_a_renamed_copy(ingest_harness, schema_db, monkeypatch):
    """Ingest the same bytes twice under different names: the second must raise
    DuplicateDownloadError."""
    tmp_path = ingest_harness
    payload = b"identical-bytes" * 200

    first = tmp_path / "Original-77-1-0.zip"
    first.write_bytes(payload)
    result = server._ingest_resolved_download(
        first, name="Original", mod_id=77, version="1.0"
    )
    assert result["ok"] is True

    monkeypatch.setattr(server, "resolve_absolute_download_path", lambda p: first)

    second = tmp_path / "Original (2).zip"
    second.write_bytes(payload)

    with pytest.raises(server.DuplicateDownloadError) as excinfo:
        server._ingest_resolved_download(
            second, name="Something Else Entirely", mod_id=None, version="9.9"
        )
    assert excinfo.value.download_id == result["download_id"]


def test_ingest_allows_genuinely_different_bytes(ingest_harness, schema_db, monkeypatch):
    tmp_path = ingest_harness

    a = tmp_path / "ModA-1-1-0.zip"
    a.write_bytes(b"aaaa" * 100)
    ra = server._ingest_resolved_download(a, name="Mod A", mod_id=1, version="1.0")

    monkeypatch.setattr(server, "resolve_absolute_download_path", lambda p: a)

    b = tmp_path / "ModB-2-1-0.zip"
    b.write_bytes(b"bbbb" * 100)
    rb = server._ingest_resolved_download(b, name="Mod B", mod_id=2, version="1.0")

    assert ra["download_id"] != rb["download_id"]


def test_duplicate_is_caught_before_extraction(ingest_harness, schema_db, monkeypatch):
    """The point of hashing up front: a duplicate must not pay for extraction."""
    tmp_path = ingest_harness
    payload = b"skip-extraction" * 200

    first = tmp_path / "First-88-1-0.zip"
    first.write_bytes(payload)
    server._ingest_resolved_download(first, name="First", mod_id=88, version="1.0")

    monkeypatch.setattr(server, "resolve_absolute_download_path", lambda p: first)

    extract_calls: list[str] = []

    def counting_extract(archive_path, dest):
        extract_calls.append(str(archive_path))

    monkeypatch.setattr(server, "extract_archive", counting_extract)

    dup = tmp_path / "First-copy.zip"
    dup.write_bytes(payload)
    with pytest.raises(server.DuplicateDownloadError):
        server._ingest_resolved_download(dup, name="Copy", mod_id=None, version="1.0")

    assert extract_calls == [], "duplicate was extracted before being rejected"
