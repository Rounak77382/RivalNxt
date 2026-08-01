"""Shared pytest fixtures and import shims for the RivalNxt backend test suite.

`core/__init__.py` eagerly imports `core.assets`, which hard-requires the
`rust_ue_tools` PyO3 extension (built from src-tauri/src/rust-ue-tools via
maturin). That extension is a native I/O boundary: it parses real PAK/UTOC
containers. None of the pure-Python logic under test here needs it, so we
install a minimal stub into ``sys.modules`` *before* ``core`` is first
imported. This keeps the `backend` CI job fast (no Rust toolchain required)
and lets the suite run on any machine.

Tests that genuinely exercise asset extraction should be marked
``@pytest.mark.requires_pyo3`` and skipped unless the real module is present.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# --------------------------------------------------------------------------
# Point the app at a throwaway data dir *before* core.config.settings loads,
# so tests never read or write the developer's real settings.json / mods.db.
# --------------------------------------------------------------------------
_TEST_DATA_DIR = REPO_ROOT / ".pytest-data"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MOD_MANAGER_DATA_DIR", str(_TEST_DATA_DIR))

# --------------------------------------------------------------------------
# rust_ue_tools stub
# --------------------------------------------------------------------------
REAL_PYO3_AVAILABLE = importlib.util.find_spec("rust_ue_tools") is not None


def _install_rust_ue_tools_stub() -> None:
    if REAL_PYO3_AVAILABLE:
        return

    module = types.ModuleType("rust_ue_tools")

    class PyAssetPath:  # noqa: D101 - mirrors the native class shape
        def __init__(self, path: str = "", pak_name: str = "") -> None:
            self.path = path
            self.pak_name = pak_name

        def __repr__(self) -> str:  # pragma: no cover - debug helper
            return f"PyAssetPath(path={self.path!r}, pak_name={self.pak_name!r})"

    class PyUnpacker:  # noqa: D101 - mirrors the native class shape
        """No-op stand-in. Returns empty results rather than raising, so code
        paths that tolerate 'no assets found' behave as they would for an
        unreadable archive."""

        def __init__(self, *args, **kwargs) -> None:
            self._args = args
            self._kwargs = kwargs

        def extract_asset_paths_from_zip(self, *args, **kwargs):
            return []

        def extract_asset_map_from_folder(self, *args, **kwargs):
            return {}

        def list_paks(self, *args, **kwargs):
            return []

    module.PyUnpacker = PyUnpacker
    module.PyAssetPath = PyAssetPath
    module.__STUB__ = True
    sys.modules["rust_ue_tools"] = module


_install_rust_ue_tools_stub()


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "requires_pyo3: test needs the real rust_ue_tools native extension",
    )


def pytest_collection_modifyitems(config: pytest.Config, items) -> None:
    if REAL_PYO3_AVAILABLE:
        return
    skip = pytest.mark.skip(reason="real rust_ue_tools extension not built")
    for item in items:
        if "requires_pyo3" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------
# Shared DB fixtures
# --------------------------------------------------------------------------
@pytest.fixture
def schema_db(tmp_path: Path):
    """A real on-disk SQLite DB with the full app schema + migrations applied.

    On-disk (not :memory:) because several tests exercise multi-connection
    behaviour, which an in-memory DB cannot model.
    """
    from core.db.db import init_schema, run_migrations

    db_path = tmp_path / "mods.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    init_schema(conn)
    run_migrations(conn)
    conn.commit()
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass


class StatementRecorder:
    """Captures every SQL statement executed on a connection."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def __call__(self, statement: str) -> None:
        self.statements.append(statement)

    def matching(self, needle: str) -> list[str]:
        low = needle.lower()
        return [s for s in self.statements if low in s.lower()]

    def count(self, needle: str) -> int:
        return len(self.matching(needle))

    def reset(self) -> None:
        self.statements.clear()


@pytest.fixture
def recorder() -> StatementRecorder:
    return StatementRecorder()
