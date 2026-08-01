"""L1: proves the test harness itself works.

If this file fails, no other backend test can be trusted.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repo_root_importable():
    assert str(REPO_ROOT) in sys.path


def test_rust_ue_tools_importable_via_stub_or_real():
    """conftest guarantees `rust_ue_tools` resolves one way or the other."""
    import rust_ue_tools

    assert hasattr(rust_ue_tools, "PyUnpacker")
    assert hasattr(rust_ue_tools, "PyAssetPath")


def test_core_package_imports():
    """The whole point of the stub: `import core` must not explode."""
    import core

    assert core is not None


def test_core_db_and_settings_import():
    from core.config import settings as settings_mod
    from core.db import db as db_mod

    assert hasattr(db_mod, "init_schema")
    assert hasattr(db_mod, "run_migrations")
    assert hasattr(settings_mod, "SETTINGS")


def test_schema_db_fixture_builds_full_schema(schema_db: sqlite3.Connection):
    """The shared fixture must produce the real app schema, not a subset."""
    names = {
        row[0]
        for row in schema_db.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        ).fetchall()
    }
    for required in (
        "local_downloads",
        "mods",
        "mod_paks",
        "pak_assets",
        "asset_conflicts",
        "asset_conflicts_active",
        "asset_tags",
        "pak_tags_json",
        "collections",
        "collection_mod_files",
        "handoff_failures",
        "v_asset_conflicts_all",
        "v_asset_conflicts_active",
    ):
        assert required in names, f"missing schema object: {required}"


def test_recorder_fixture_captures_statements(schema_db, recorder):
    schema_db.set_trace_callback(recorder)
    schema_db.execute("SELECT COUNT(*) FROM local_downloads").fetchone()
    schema_db.set_trace_callback(None)
    assert recorder.count("local_downloads") >= 1


def test_settings_point_at_throwaway_data_dir():
    """Guards against a test run stomping the developer's real mods.db."""
    import os

    data_dir = os.environ.get("MOD_MANAGER_DATA_DIR", "")
    assert ".pytest-data" in data_dir, data_dir
