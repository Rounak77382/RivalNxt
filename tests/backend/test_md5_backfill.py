"""M4: MD5 hashing must stream, and the backfill must batch its commits.

_md5_backfill_worker did `hashlib.md5(fh.read()).hexdigest()` -- loading an
entire archive into RAM before hashing. Mod archives routinely run to several
GB. It also called conn.commit() once per row, i.e. one fsync per file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import core.api.server as server
from core.api.server import MD5_COMMIT_BATCH, compute_file_md5


# ---------------------------------------------------------------------------
# compute_file_md5 correctness
# ---------------------------------------------------------------------------
def test_matches_hashlib_reference_on_3mib_file(tmp_path):
    """A 3 MiB file spans multiple 1 MiB chunks, so boundaries are exercised."""
    payload = os.urandom(3 * 1024 * 1024)
    target = tmp_path / "big.zip"
    target.write_bytes(payload)

    assert compute_file_md5(target) == hashlib.md5(payload).hexdigest()


@pytest.mark.parametrize(
    "size",
    [
        0,                       # empty file
        1,                       # single byte
        1024 * 1024 - 1,         # just under one chunk
        1024 * 1024,             # exactly one chunk
        1024 * 1024 + 1,         # just over one chunk
        2 * 1024 * 1024,         # exactly two chunks
    ],
)
def test_chunk_boundary_sizes(tmp_path, size):
    payload = os.urandom(size)
    target = tmp_path / f"f_{size}.bin"
    target.write_bytes(payload)
    assert compute_file_md5(target) == hashlib.md5(payload).hexdigest()


def test_small_chunk_size_gives_same_digest(tmp_path):
    """Digest must be independent of chunk size."""
    payload = os.urandom(100_000)
    target = tmp_path / "x.bin"
    target.write_bytes(payload)

    expected = hashlib.md5(payload).hexdigest()
    for chunk in (1, 7, 4096, 99_999, 10**7):
        assert compute_file_md5(target, chunk_size=chunk) == expected


def test_accepts_str_and_path(tmp_path):
    target = tmp_path / "s.bin"
    target.write_bytes(b"hello")
    assert compute_file_md5(str(target)) == compute_file_md5(Path(target))


def test_does_not_read_whole_file_into_memory(tmp_path, monkeypatch):
    """Assert on the read pattern: many bounded reads, never one unbounded one."""
    payload = os.urandom(3 * 1024 * 1024)
    target = tmp_path / "big.zip"
    target.write_bytes(payload)

    read_sizes: list[int | None] = []
    real_open = open

    class TracingFile:
        def __init__(self, fh):
            self._fh = fh

        def read(self, n=-1):
            read_sizes.append(n)
            return self._fh.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._fh.close()
            return False

    def tracing_open(path, mode="r", *args, **kwargs):
        return TracingFile(real_open(path, mode, *args, **kwargs))

    monkeypatch.setattr("builtins.open", tracing_open)
    digest = compute_file_md5(target)
    monkeypatch.undo()

    assert digest == hashlib.md5(payload).hexdigest()
    assert read_sizes, "no reads recorded"
    # The bug was a single read() with no size limit.
    assert all(n == 1024 * 1024 for n in read_sizes), read_sizes
    assert len(read_sizes) >= 4, f"expected chunked reads, got {read_sizes}"


# ---------------------------------------------------------------------------
# Backfill worker: batched commits
# ---------------------------------------------------------------------------
@pytest.fixture
def backfill_env(monkeypatch, schema_db, tmp_path):
    """Wire the worker at a temp downloads root and the throwaway schema DB."""
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS, marvel_rivals_local_downloads_root=downloads
        ),
    )

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
    # The worker sleeps 50ms per file; skip that in tests.
    monkeypatch.setattr(server.time, "sleep", lambda *_: None)
    return downloads, schema_db


def _seed_downloads(conn, downloads: Path, count: int) -> dict[str, str]:
    """Create `count` archives needing a hash. Returns {rel_path: expected md5}."""
    expected: dict[str, str] = {}
    for i in range(count):
        rel = f"Mod{i}.zip"
        payload = f"content-{i}".encode() * 100
        (downloads / rel).write_bytes(payload)
        expected[rel] = hashlib.md5(payload).hexdigest()
        conn.execute(
            """
            INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, file_md5)
            VALUES(?, ?, ?, NULL, '1.0', '[]', '[]', NULL)
            """,
            (rel, i, f"Mod {i}"),
        )
    conn.commit()
    return expected


def test_backfill_writes_correct_hashes(backfill_env):
    downloads, conn = backfill_env
    expected = _seed_downloads(conn, downloads, 5)

    server._md5_backfill_worker()

    for rel, digest in expected.items():
        row = conn.execute(
            "SELECT file_md5 FROM local_downloads WHERE path = ?", (rel,)
        ).fetchone()
        assert row[0] == digest, f"{rel}: {row[0]} != {digest}"


def test_backfill_commits_are_batched(backfill_env, recorder):
    """120 rows must produce ~3 commits (batch of 50), not 120."""
    downloads, conn = backfill_env
    rows = 120
    _seed_downloads(conn, downloads, rows)

    conn.set_trace_callback(recorder)
    server._md5_backfill_worker()
    conn.set_trace_callback(None)

    commits = recorder.count("COMMIT")
    expected_max = (rows // MD5_COMMIT_BATCH) + 2
    assert commits <= expected_max, (
        f"{commits} commits for {rows} rows; batching of {MD5_COMMIT_BATCH} "
        f"should give at most {expected_max}"
    )
    assert commits >= 2, f"expected at least 2 commits, got {commits}"


def test_backfill_commits_trailing_partial_batch(backfill_env):
    """A count below the batch size must still be persisted."""
    downloads, conn = backfill_env
    expected = _seed_downloads(conn, downloads, 3)  # < MD5_COMMIT_BATCH

    server._md5_backfill_worker()

    for rel, digest in expected.items():
        row = conn.execute(
            "SELECT file_md5 FROM local_downloads WHERE path = ?", (rel,)
        ).fetchone()
        assert row[0] == digest, f"trailing batch not committed for {rel}"


def test_backfill_skips_missing_files(backfill_env):
    downloads, conn = backfill_env
    conn.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, file_md5)
        VALUES('Gone.zip', 99, 'Gone', NULL, '1.0', '[]', '[]', NULL)
        """
    )
    conn.commit()

    server._md5_backfill_worker()  # must not raise

    row = conn.execute(
        "SELECT file_md5 FROM local_downloads WHERE path = 'Gone.zip'"
    ).fetchone()
    assert row[0] is None


def test_backfill_handles_unset_downloads_root(monkeypatch, schema_db):
    """Regression: Path(None) raised
    "expected str, bytes or os.PathLike object, not NoneType" and the worker
    aborted with a warning on every startup with no downloads root set."""
    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS, marvel_rivals_local_downloads_root=None
        ),
    )
    server._md5_backfill_worker()  # must return cleanly, not raise


def test_backfill_leaves_already_hashed_rows_alone(backfill_env):
    downloads, conn = backfill_env
    (downloads / "Done.zip").write_bytes(b"payload")
    conn.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks, file_md5)
        VALUES('Done.zip', 1, 'Done', NULL, '1.0', '[]', '[]', 'preexisting')
        """
    )
    conn.commit()

    server._md5_backfill_worker()

    row = conn.execute(
        "SELECT file_md5 FROM local_downloads WHERE path = 'Done.zip'"
    ).fetchone()
    assert row[0] == "preexisting"
