"""H5: set_active_paks must walk the ~mods tree once, not once per pak.

The function called `mods_dir.rglob(name)` from inside three separate loops, so
activating a mod with N paks walked the entire ~mods tree N times --
O(names x tree size). One indexed walk makes it O(tree size + names).

Also covers _resolve_desired_paks, the pure pak-name matching logic extracted
out of the 535-line body.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import core.api.server as server
from core.api.server import _index_lookup, _index_mods_dir, _resolve_desired_paks


# ---------------------------------------------------------------------------
# _index_mods_dir / _index_lookup
# ---------------------------------------------------------------------------
def _make_tree(root: Path, rel_paths: list[str]) -> None:
    for rel in rel_paths:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x00")


def test_index_finds_files_at_any_depth(tmp_path):
    _make_tree(tmp_path, ["top.pak", "a/mid.pak", "a/b/c/deep.pak"])
    index = _index_mods_dir(tmp_path)

    assert _index_lookup(index, "top.pak")[0].name == "top.pak"
    assert _index_lookup(index, "mid.pak")[0].name == "mid.pak"
    assert _index_lookup(index, "deep.pak")[0].name == "deep.pak"


def test_index_lookup_is_case_insensitive(tmp_path):
    _make_tree(tmp_path, ["MixedCase.pak"])
    index = _index_mods_dir(tmp_path)
    assert _index_lookup(index, "mixedcase.pak")
    assert _index_lookup(index, "MIXEDCASE.PAK")


def test_index_lookup_accepts_a_path_and_matches_the_basename(tmp_path):
    _make_tree(tmp_path, ["sub/thing.pak"])
    index = _index_mods_dir(tmp_path)
    assert _index_lookup(index, "some/other/thing.pak")


def test_index_returns_all_duplicates(tmp_path):
    """The same basename can exist in several character folders."""
    _make_tree(tmp_path, ["luna/skin.pak", "hela/skin.pak"])
    index = _index_mods_dir(tmp_path)
    found = _index_lookup(index, "skin.pak")
    assert len(found) == 2
    assert {p.parent.name for p in found} == {"luna", "hela"}


def test_index_lookup_skips_deleted_files(tmp_path):
    """The prune passes delete files; a later pass must not act on a stale entry."""
    _make_tree(tmp_path, ["gone.pak", "stays.pak"])
    index = _index_mods_dir(tmp_path)
    (tmp_path / "gone.pak").unlink()

    assert _index_lookup(index, "gone.pak") == []
    assert len(_index_lookup(index, "stays.pak")) == 1


def test_index_excludes_directories(tmp_path):
    (tmp_path / "notafile.pak").mkdir()
    index = _index_mods_dir(tmp_path)
    assert _index_lookup(index, "notafile.pak") == []


def test_index_of_missing_directory_is_empty(tmp_path):
    assert _index_mods_dir(tmp_path / "does-not-exist") == {}


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_index_lookup_rejects_blank_names(tmp_path, blank):
    _make_tree(tmp_path, ["a.pak"])
    assert _index_lookup(_index_mods_dir(tmp_path), blank) == []


def test_index_walks_the_tree_exactly_once(tmp_path, monkeypatch):
    """A single rglob call, regardless of how many lookups follow."""
    _make_tree(tmp_path, [f"f{i}.pak" for i in range(50)])

    calls: list[str] = []
    real_rglob = Path.rglob

    def counting_rglob(self, pattern):
        calls.append(str(pattern))
        return real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", counting_rglob)
    index = _index_mods_dir(tmp_path)
    for i in range(50):
        _index_lookup(index, f"f{i}.pak")
    monkeypatch.undo()

    assert len(calls) == 1, f"expected 1 rglob, got {len(calls)}: {calls}"
    assert calls == ["*"]


# ---------------------------------------------------------------------------
# _resolve_desired_paks (pure)
# ---------------------------------------------------------------------------
def _alt_ext(name: str) -> list[str]:
    """Mirrors the closure inside set_active_paks."""
    import os

    stem, ext = os.path.splitext(name)
    if ext.lower() == ".pak":
        return [name, f"{stem}.utoc"]
    if ext.lower() == ".utoc":
        return [name, f"{stem}.pak"]
    return [name]


def _valid(contents: list[str]) -> dict[str, str]:
    import os

    return {os.path.basename(c).lower(): c for c in contents}


def test_resolve_exact_relative_path():
    contents = ["sub/xl/thing.pak"]
    desired, mapping, unresolved = _resolve_desired_paks(
        ["sub/xl/thing.pak"], contents, _valid(contents), _alt_ext
    )
    assert unresolved is None
    assert desired == ["sub/xl/thing.pak"]
    assert mapping["sub/xl/thing.pak"] == "thing.pak"


def test_resolve_by_basename_returns_the_relative_path():
    contents = ["sub/xl/thing.pak"]
    desired, _, unresolved = _resolve_desired_paks(
        ["thing.pak"], contents, _valid(contents), _alt_ext
    )
    assert unresolved is None
    assert desired == ["sub/xl/thing.pak"], "hierarchy must be preserved"


def test_resolve_is_case_insensitive():
    contents = ["Thing.PAK"]
    desired, _, unresolved = _resolve_desired_paks(
        ["thing.pak"], contents, _valid(contents), _alt_ext
    )
    assert unresolved is None
    assert desired == ["Thing.PAK"]


def test_resolve_falls_back_to_alternate_extension():
    """A .utoc request resolves to the .pak member of the same bundle."""
    contents = ["bundle.pak"]
    desired, _, unresolved = _resolve_desired_paks(
        ["bundle.utoc"], contents, _valid(contents), _alt_ext
    )
    assert unresolved is None
    assert desired == ["bundle.pak"]


def test_resolve_reports_the_first_unresolved_name():
    contents = ["known.pak"]
    desired, _, unresolved = _resolve_desired_paks(
        ["known.pak", "unknown.pak", "also-unknown.pak"],
        contents,
        _valid(contents),
        _alt_ext,
    )
    assert unresolved == "unknown.pak"
    assert desired == ["known.pak"], "already-resolved entries are still returned"


def test_resolve_empty_request():
    desired, mapping, unresolved = _resolve_desired_paks([], ["a.pak"], _valid(["a.pak"]), _alt_ext)
    assert desired == []
    assert mapping == {}
    assert unresolved is None


def test_resolve_preserves_request_order():
    contents = ["a.pak", "b.pak", "c.pak"]
    desired, _, _ = _resolve_desired_paks(
        ["c.pak", "a.pak", "b.pak"], contents, _valid(contents), _alt_ext
    )
    assert desired == ["c.pak", "a.pak", "b.pak"]


def test_resolve_matches_the_original_inline_algorithm():
    """Differential test against a transcription of the pre-refactor loop."""
    import os

    def original(desired_raw, contents, valid_basenames, alt_ext):
        valid_lower = set(valid_basenames.keys())
        desired, rel_to_basename = [], {}
        for d in desired_raw:
            base_d = os.path.basename(d)
            dl = base_d.lower()
            exact = next(
                (c for c in contents if isinstance(c, str) and c.lower() == d.lower()),
                None,
            )
            if exact:
                desired.append(exact)
                rel_to_basename[exact] = os.path.basename(exact)
                continue
            if dl in valid_lower:
                rel_path = valid_basenames[dl]
                desired.append(rel_path)
                rel_to_basename[rel_path] = os.path.basename(rel_path)
                continue
            found_rel = None
            for a in alt_ext(base_d):
                if a.lower() in valid_lower:
                    found_rel = valid_basenames[a.lower()]
                    break
            if found_rel:
                desired.append(found_rel)
                rel_to_basename[found_rel] = os.path.basename(found_rel)
                continue
            return desired, rel_to_basename, d
        return desired, rel_to_basename, None

    fixtures = [
        (["a.pak"], ["a.pak"]),
        (["a.pak"], ["sub/a.pak"]),
        (["sub/a.pak"], ["sub/a.pak"]),
        (["a.utoc"], ["a.pak"]),
        (["a.pak"], ["a.utoc"]),
        (["A.PAK"], ["a.pak"]),
        (["missing.pak"], ["a.pak"]),
        (["a.pak", "missing.pak", "b.pak"], ["a.pak", "b.pak"]),
        ([], ["a.pak"]),
        (["a.sig"], ["a.pak"]),
    ]
    for desired_raw, contents in fixtures:
        valid = _valid(contents)
        assert _resolve_desired_paks(desired_raw, contents, valid, _alt_ext) == original(
            desired_raw, contents, valid, _alt_ext
        ), (desired_raw, contents)


# ---------------------------------------------------------------------------
# End-to-end: real filesystem, real DB, one rglob
# ---------------------------------------------------------------------------
@pytest.fixture
def activation_env(monkeypatch, tmp_path, schema_db):
    """A ~mods tree with 50 pre-existing paks and a download to activate."""
    from core.config.settings import get_mods_dir

    game_root = tmp_path / "game"
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    mods_dir = get_mods_dir(game_root)
    mods_dir.mkdir(parents=True)

    # 50 unrelated files already present, so a per-name walk would be costly.
    for i in range(50):
        (mods_dir / f"existing_{i}.pak").write_bytes(b"\x00")
    # Two stale files from a previous activation of this download.
    (mods_dir / "stale.pak").write_bytes(b"\x00")
    (mods_dir / "stale.utoc").write_bytes(b"\x00")

    # The download's source folder holding the paks to copy in.
    src = downloads / "TheMod"
    src.mkdir()
    (src / "fresh.pak").write_bytes(b"\x01")

    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(
            settings_mod.SETTINGS,
            marvel_rivals_root=game_root,
            marvel_rivals_local_downloads_root=downloads,
        ),
    )

    schema_db.execute(
        "INSERT OR REPLACE INTO mods(mod_id, game, name) VALUES(1, 'marvelrivals', 'The Mod')"
    )
    schema_db.execute(
        """
        INSERT INTO local_downloads(path, id, name, mod_id, version, contents, active_paks)
        VALUES(?, 1, 'The Mod', 1, '1.0', ?, ?)
        """,
        (
            str(src),
            json.dumps(["fresh.pak", "stale.pak"]),
            json.dumps(["stale.pak"]),
        ),
    )
    schema_db.commit()

    class _NoClose:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            pass

    monkeypatch.setattr(server, "get_db", lambda: _NoClose(schema_db))
    monkeypatch.setattr(server, "_safe_rebuild_conflicts", lambda *a, **k: None)
    monkeypatch.setattr(server, "scan_active_main", lambda *a, **k: None)

    return {"mods_dir": mods_dir, "src": src, "db": schema_db}


def test_activation_walks_mods_dir_once(activation_env, monkeypatch):
    """THE headline assertion for H5."""
    mods_dir = activation_env["mods_dir"]

    calls: list[tuple[str, str]] = []
    real_rglob = Path.rglob

    def counting_rglob(self, pattern):
        calls.append((str(self), str(pattern)))
        return real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", counting_rglob)
    try:
        server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    finally:
        monkeypatch.undo()

    mods_dir_walks = [c for c in calls if c[0] == str(mods_dir)]
    assert len(mods_dir_walks) == 1, (
        f"expected exactly 1 walk of {mods_dir}, got {len(mods_dir_walks)}: "
        f"{mods_dir_walks}"
    )


def test_activation_copies_the_requested_pak(activation_env):
    mods_dir = activation_env["mods_dir"]
    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    copied = list(mods_dir.rglob("fresh.pak"))
    assert copied, "requested pak was not copied into ~mods"


def test_activation_removes_stale_files(activation_env):
    mods_dir = activation_env["mods_dir"]
    assert (mods_dir / "stale.pak").exists()
    assert (mods_dir / "stale.utoc").exists()

    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})

    assert not (mods_dir / "stale.pak").exists(), "stale pak was not removed"
    assert not (mods_dir / "stale.utoc").exists(), (
        "stale IoStore companion was not removed"
    )


def test_activation_leaves_unrelated_files_alone(activation_env):
    mods_dir = activation_env["mods_dir"]
    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    survivors = {p.name for p in mods_dir.rglob("existing_*.pak")}
    assert len(survivors) == 50, f"unrelated files were touched: {len(survivors)}/50"


def test_activation_updates_the_database(activation_env):
    db = activation_env["db"]
    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    row = db.execute("SELECT active_paks FROM local_downloads WHERE id = 1").fetchone()
    stored = json.loads(row[0])
    assert any("fresh.pak" in s for s in stored), stored


def test_deactivation_clears_everything(activation_env):
    mods_dir = activation_env["mods_dir"]
    db = activation_env["db"]
    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    server.set_active_paks(1, {"active_paks": []})

    row = db.execute("SELECT active_paks FROM local_downloads WHERE id = 1").fetchone()
    assert json.loads(row[0]) == []
    assert list(mods_dir.rglob("fresh.pak")) == []


def test_unknown_pak_is_rejected(activation_env):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        server.set_active_paks(1, {"active_paks": ["not-in-contents.pak"]})
    assert e.value.status_code == 400
    assert "not part of this download" in str(e.value.detail)


def test_non_list_payload_is_rejected(activation_env):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        server.set_active_paks(1, {"active_paks": "fresh.pak"})
    assert e.value.status_code == 400


def test_rebuild_conflicts_flag_is_honoured(activation_env, monkeypatch):
    """Batch callers pass rebuild_conflicts=False (used by collection activate)."""
    calls: list[str] = []
    monkeypatch.setattr(
        server,
        "_safe_rebuild_conflicts",
        lambda conn, *, active_only, purpose, raise_on_error=False: calls.append(purpose),
    )

    server.set_active_paks(1, {"active_paks": ["fresh.pak"], "rebuild_conflicts": False})
    assert calls == [], "rebuild should have been deferred"

    server.set_active_paks(1, {"active_paks": ["fresh.pak"]})
    assert calls == ["set_active_paks"], calls
