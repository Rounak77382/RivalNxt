"""M2: the ingest must read each archive exactly once.

_ingest_resolved_download called extract_archive(path, tmpdir) and then
list_entries(path) -- decompressing the archive a second time purely to
enumerate its contents, when the fully extracted tree was already sitting in
tmpdir. Enumeration now walks tmpdir instead.

Hierarchical paths matter: the old list_entries call returned archive-relative
paths like "subdir/xl/thing.pak" (the UI shows them, set_active_paks resolves
basenames out of them), so the replacement uses os.path.relpath rather than
bare basenames.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import core.api.server as server


# ---------------------------------------------------------------------------
# _enumerate_pak_entries
# ---------------------------------------------------------------------------
def _make_tree(root: Path, rel_paths: list[str]) -> None:
    for rel in rel_paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00")


def test_enumerates_flat_paks(tmp_path):
    _make_tree(tmp_path, ["Alpha.pak", "Beta.pak"])
    assert server._enumerate_pak_entries(str(tmp_path)) == ["Alpha.pak", "Beta.pak"]


def test_preserves_hierarchical_paths(tmp_path):
    """The critical behaviour: nested layout must survive, not be flattened."""
    _make_tree(tmp_path, ["subdir/xl/thicc_luna.pak", "top.pak"])
    result = server._enumerate_pak_entries(str(tmp_path))
    assert "subdir/xl/thicc_luna.pak" in result
    assert "top.pak" in result
    # A basename-only walk would have produced "thicc_luna.pak".
    assert "thicc_luna.pak" not in result


def test_uses_forward_slashes_on_all_platforms(tmp_path):
    _make_tree(tmp_path, ["a/b/c/deep.pak"])
    result = server._enumerate_pak_entries(str(tmp_path))
    assert result == ["a/b/c/deep.pak"]
    assert not any(os.sep in r and os.sep != "/" for r in result)


def test_includes_all_container_extensions(tmp_path):
    _make_tree(
        tmp_path,
        ["m.pak", "m.utoc", "m.ucas", "m.sig", "readme.txt", "cover.png", "notes.md"],
    )
    result = server._enumerate_pak_entries(str(tmp_path))
    assert set(result) == {"m.pak", "m.utoc", "m.ucas", "m.sig"}


def test_extension_match_is_case_insensitive(tmp_path):
    _make_tree(tmp_path, ["Loud.PAK", "Mixed.UToC"])
    assert set(server._enumerate_pak_entries(str(tmp_path))) == {"Loud.PAK", "Mixed.UToC"}


def test_empty_tree_yields_empty_list(tmp_path):
    assert server._enumerate_pak_entries(str(tmp_path)) == []


def test_result_is_deterministically_ordered(tmp_path):
    _make_tree(tmp_path, ["z.pak", "a.pak", "m/n.pak"])
    first = server._enumerate_pak_entries(str(tmp_path))
    second = server._enumerate_pak_entries(str(tmp_path))
    assert first == second == sorted(first)


# ---------------------------------------------------------------------------
# End-to-end: list_entries must not be called
# ---------------------------------------------------------------------------
@pytest.fixture
def ingest_harness(monkeypatch, schema_db, tmp_path):
    """Stub the extraction boundary and DB so _ingest_resolved_download runs
    without a real archive or the Rust extension."""
    calls = {"list_entries": 0, "extract_archive": 0, "asset_map": 0}
    extracted: dict[str, str] = {}

    def fake_extract_archive(archive_path, dest):
        calls["extract_archive"] += 1
        extracted["dest"] = dest
        # Lay down a nested container layout, as a real mod archive would.
        for rel in ("subdir/xl/thicc_luna.pak", "subdir/xl/thicc_luna.utoc", "readme.txt"):
            target = Path(dest) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"\x00")

    def fake_list_entries(archive_path):
        calls["list_entries"] += 1
        raise AssertionError(
            "list_entries was called: the archive is being read a second time"
        )

    def fake_asset_map(folder, aes_key=None):
        calls["asset_map"] += 1
        return {"thicc_luna.pak": ["/Game/A/x.uasset"], "thicc_luna.utoc": []}

    monkeypatch.setattr(server, "extract_archive", fake_extract_archive)
    monkeypatch.setattr(server, "list_entries", fake_list_entries)
    monkeypatch.setattr(server, "extract_pak_asset_map_from_folder", fake_asset_map)
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    monkeypatch.setattr(
        server, "_sync_mod_metadata", lambda *a, **k: {"synced_mod_id": None}
    )

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    return calls


def test_ingest_never_calls_list_entries(ingest_harness, tmp_path, monkeypatch):
    """The headline assertion for M2."""
    archive = tmp_path / "TheMod-1234-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="The Mod", mod_id=1234, version="1.0"
    )

    assert ingest_harness["list_entries"] == 0, "archive was read twice"
    assert ingest_harness["extract_archive"] == 1, "archive should be extracted once"
    assert result["ok"] is True
    assert "ingest_warning" not in result, result


def test_ingest_contents_preserve_hierarchy(ingest_harness, tmp_path):
    """contents must come from the tmpdir walk with paths intact."""
    archive = tmp_path / "Nested-99-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="Nested", mod_id=99, version="1.0"
    )

    contents = result["contents"]
    assert contents, contents
    # collapse_pak_bundle merges the .pak/.utoc pair; the surviving entry must
    # still carry its directory prefix.
    assert any("subdir/xl" in c for c in contents), contents
    assert not any(c == "readme.txt" for c in contents), contents


def test_ingest_records_paks_and_assets(ingest_harness, tmp_path, schema_db):
    archive = tmp_path / "Recorded-77-2-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="Recorded", mod_id=77, version="2.0"
    )
    assert result["ingested_paks"] >= 1, result
    assert result["ingested_assets"] >= 1, result

    rows = schema_db.execute("SELECT pak_name FROM mod_paks").fetchall()
    assert rows, "no mod_paks rows written"


def test_ingest_seeds_pak_map_when_asset_map_empty(
    monkeypatch, ingest_harness, tmp_path, schema_db
):
    """Preserves the old fallback: an empty asset map must still yield pak rows
    so the io_store check has something to inspect."""
    monkeypatch.setattr(
        server, "extract_pak_asset_map_from_folder", lambda folder, aes_key=None: {}
    )

    archive = tmp_path / "Fallback-55-1-0.zip"
    archive.write_bytes(b"PK\x03\x04fake")

    result = server._ingest_resolved_download(
        archive, name="Fallback", mod_id=55, version="1.0"
    )

    assert result["ok"] is True
    assert result["contents"], result
    assert result["ingested_paks"] >= 1, result


def test_extraction_failure_is_reported_not_swallowed(
    monkeypatch, ingest_harness, tmp_path
):
    """A failed extraction must still surface ingest_warning."""

    def boom(archive_path, dest):
        raise RuntimeError("corrupt archive")

    monkeypatch.setattr(server, "extract_archive", boom)

    archive = tmp_path / "Broken-11-1-0.zip"
    archive.write_bytes(b"garbage")

    result = server._ingest_resolved_download(
        archive, name="Broken", mod_id=11, version="1.0"
    )
    assert result["ok"] is True
    assert "ingest_warning" in result, result
    assert "corrupt archive" in result["ingest_warning"]
