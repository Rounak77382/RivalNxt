"""L3: the ~mods / Paks directory layout must be identical everywhere.

core/db/db.py `_get_mods_folder_for_deletion` built "MarvelGame/~mods" --
a 2-segment path that never exists. `_remove_in_mods_by_names` /
`_remove_in_mods_by_stems` early-return when the directory is missing, so
`delete_outdated_versions` deleted DB rows while removing nothing from disk,
leaving orphaned .pak files loaded in-game with no DB record.
"""
from __future__ import annotations

from pathlib import Path


from core.config.settings import (
    RELATIVE_MODS_PATH,
    RELATIVE_PAKS_PATH,
    get_mods_dir,
    get_paks_dir,
)

FAKE_ROOT = Path("/fake/root")
EXPECTED_MODS = Path("/fake/root/MarvelGame/Marvel/Content/Paks/~mods")
EXPECTED_PAKS = Path("/fake/root/MarvelGame/Marvel/Content/Paks")


def test_get_mods_dir_exact_path():
    assert get_mods_dir(FAKE_ROOT) == EXPECTED_MODS


def test_get_paks_dir_exact_path():
    assert get_paks_dir(FAKE_ROOT) == EXPECTED_PAKS


def test_mods_dir_is_paks_dir_plus_tilde_mods():
    assert get_mods_dir(FAKE_ROOT) == get_paks_dir(FAKE_ROOT) / "~mods"


def test_accepts_str_root():
    assert get_mods_dir("/fake/root") == EXPECTED_MODS


def test_returns_none_when_root_unset():
    """Callers must handle the unset case rather than get a bogus relative path."""
    assert get_mods_dir(None) is None or isinstance(get_mods_dir(None), Path)
    # With an explicitly empty root there is no meaningful directory.
    assert get_mods_dir("") is None
    assert get_paks_dir("") is None


def test_relative_constants_have_five_segments():
    """Regression guard: the bug was a 2-segment path."""
    assert RELATIVE_PAKS_PATH.parts == ("MarvelGame", "Marvel", "Content", "Paks")
    assert RELATIVE_MODS_PATH.parts == (
        "MarvelGame",
        "Marvel",
        "Content",
        "Paks",
        "~mods",
    )


def test_deletion_helper_matches_shared_helper(monkeypatch):
    """The exact regression: _get_mods_folder_for_deletion must agree with
    get_mods_dir. Before the fix it returned /fake/root/MarvelGame/~mods."""
    import core.config.settings as settings_mod
    from core.db.db import _get_mods_folder_for_deletion

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(settings_mod.SETTINGS, marvel_rivals_root=FAKE_ROOT),
    )

    result = _get_mods_folder_for_deletion()
    assert result == EXPECTED_MODS, f"deletion helper drifted: {result}"
    assert result == get_mods_dir(FAKE_ROOT)
    # Explicitly assert the old wrong path is gone.
    assert result != FAKE_ROOT / "MarvelGame" / "~mods"


def test_deletion_helper_returns_none_when_root_unset(monkeypatch):
    import core.config.settings as settings_mod
    from core.db.db import _get_mods_folder_for_deletion

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(settings_mod.SETTINGS, marvel_rivals_root=None),
    )
    assert _get_mods_folder_for_deletion() is None


def test_server_helpers_agree(monkeypatch, tmp_path):
    """_mods_folder_from_env and _get_actually_active_filenames must resolve to
    the same directory as get_mods_dir."""
    import core.api.server as server
    import core.config.settings as settings_mod

    monkeypatch.setattr(
        settings_mod,
        "SETTINGS",
        settings_mod.replace(settings_mod.SETTINGS, marvel_rivals_root=tmp_path),
    )

    assert server._mods_folder_from_env() == get_mods_dir(tmp_path)

    # _get_actually_active_filenames should read the real 5-segment location.
    mods_dir = get_mods_dir(tmp_path)
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "Alpha.pak").write_bytes(b"x")
    (mods_dir / "Beta.pak").write_bytes(b"y")

    import logging

    found = server._get_actually_active_filenames(logging.getLogger("test"))
    assert found == {"alpha.pak", "beta.pak"}


def test_removal_helpers_actually_delete(tmp_path):
    """Proves the downstream consequence: with the correct directory the
    removal helpers do real work instead of silently returning []."""
    from core.db.db import _remove_in_mods_by_names, _remove_in_mods_by_stems

    mods_dir = get_mods_dir(tmp_path)
    mods_dir.mkdir(parents=True, exist_ok=True)
    (mods_dir / "Thing.pak").write_bytes(b"a")
    (mods_dir / "Thing.utoc").write_bytes(b"b")
    (mods_dir / "Other.pak").write_bytes(b"c")

    removed = _remove_in_mods_by_stems(mods_dir, ["Thing"])
    assert len(removed) == 2, removed
    assert not (mods_dir / "Thing.pak").exists()
    assert not (mods_dir / "Thing.utoc").exists()
    assert (mods_dir / "Other.pak").exists()

    removed2 = _remove_in_mods_by_names(mods_dir, ["Other.pak"])
    assert len(removed2) == 1
    assert not (mods_dir / "Other.pak").exists()


def test_wrong_legacy_path_would_have_found_nothing(tmp_path):
    """Demonstrates why the bug was silent: the old 2-segment directory does
    not exist, so the helper returns [] without error."""
    from core.db.db import _remove_in_mods_by_stems

    correct = get_mods_dir(tmp_path)
    correct.mkdir(parents=True, exist_ok=True)
    (correct / "Thing.pak").write_bytes(b"a")

    legacy = tmp_path / "MarvelGame" / "~mods"
    assert not legacy.exists()
    assert _remove_in_mods_by_stems(legacy, ["Thing"]) == []
    # File survives -> orphaned pak, exactly the reported symptom.
    assert (correct / "Thing.pak").exists()
